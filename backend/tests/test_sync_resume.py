# ruff: noqa: F811
"""A retried Cloud Task resumes a stalled sync instead of exiting or racing a live run."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.deps import get_firestore, get_settings, get_sync_service_dep
from backend.api.routes import internal
from backend.config import BackendSettings
from backend.models.sync_job import (
    MAX_SYNC_RESUMES,
    STALE_JOB_THRESHOLD_SECONDS,
    STALE_PENDING_THRESHOLD_SECONDS,
    SyncJob,
    SyncJobStatus,
)

# Reuse the services-route test harness (auth_client + mocked Firestore/Cloud Tasks).
from backend.tests.test_services_routes import (  # noqa: E402, F401
    auth_client,
    mock_auth_service,
    mock_catalog_service,
    mock_cloud_tasks_service,
    mock_firestore_service,
    mock_music_service_service,
    mock_settings,
    mock_sync_service,
    sample_lastfm_service,
    sample_spotify_service,
    sample_user,
)

JOB_ID = "job-1"
USER_ID = "fixture-user"
BODY = {"job_id": JOB_ID, "user_id": USER_ID}


class FakeFirestore:
    """In-memory sync_jobs store; run_transaction serializes like Firestore's optimistic retry."""

    def __init__(self, job: SyncJob | None) -> None:
        self.docs: dict[str, dict[str, Any]] = {JOB_ID: job.to_dict()} if job else {}
        self._lock = asyncio.Lock()

    async def get_document(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        return self.docs.get(doc_id)

    async def set_document(self, collection: str, doc_id: str, data: dict[str, Any]) -> None:
        self.docs[doc_id] = data

    async def update_document(self, collection: str, doc_id: str, data: dict[str, Any]) -> None:
        self.docs[doc_id].update(data)

    async def run_transaction(self, fn: Any) -> Any:
        async with self._lock:
            writes: dict[str, dict[str, Any]] = {}
            view = SimpleNamespace(
                get=AsyncMock(side_effect=lambda c, d: self.docs.get(d)),
                set=lambda c, d, data: writes.__setitem__(d, data),
            )
            result = await fn(view)
            self.docs.update(writes)
            return result


def _job(status: SyncJobStatus, age_seconds: int = 0, resume_count: int = 0) -> SyncJob:
    return SyncJob(
        id=JOB_ID,
        user_id=USER_ID,
        status=status,
        updated_at=datetime.now(UTC) - timedelta(seconds=age_seconds),
        resume_count=resume_count,
    )


def _sync_service(side_effect: Any = None) -> MagicMock:
    svc = MagicMock()
    svc.sync_all_services_with_progress = AsyncMock(
        return_value=[
            SimpleNamespace(
                service_type="lastfm",
                tracks_fetched=3,
                tracks_matched=2,
                user_songs_created=2,
                user_songs_updated=0,
                error=None,
            )
        ],
        side_effect=side_effect,
    )
    return svc


def _client(fs: FakeFirestore, svc: MagicMock) -> TestClient:
    app = FastAPI()
    app.include_router(internal.router, prefix="/internal")
    app.dependency_overrides[get_firestore] = lambda: fs
    app.dependency_overrides[get_sync_service_dep] = lambda: svc
    app.dependency_overrides[get_settings] = lambda: BackendSettings(environment="development")
    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_email() -> Any:
    with patch.object(internal, "_send_sync_completion_email", new=AsyncMock()):
        yield


def test_fresh_heartbeat_returns_503() -> None:
    fs = FakeFirestore(_job(SyncJobStatus.IN_PROGRESS, age_seconds=10))
    svc = _sync_service()
    r = _client(fs, svc).post("/internal/sync/process", json=BODY)
    assert r.status_code == 503
    assert r.json()["detail"] == "in_progress"
    svc.sync_all_services_with_progress.assert_not_called()
    assert fs.docs[JOB_ID]["resume_count"] == 0


def test_stale_heartbeat_resumes() -> None:
    fs = FakeFirestore(_job(SyncJobStatus.IN_PROGRESS, age_seconds=STALE_JOB_THRESHOLD_SECONDS + 5))
    svc = _sync_service()
    r = _client(fs, svc).post("/internal/sync/process", json=BODY)
    assert r.status_code == 200
    svc.sync_all_services_with_progress.assert_awaited_once()
    assert fs.docs[JOB_ID]["status"] == "completed"
    assert fs.docs[JOB_ID]["resume_count"] == 1


@pytest.mark.parametrize("status", [SyncJobStatus.COMPLETED, SyncJobStatus.FAILED])
def test_finished_job_is_noop(status: SyncJobStatus) -> None:
    fs = FakeFirestore(_job(status))
    svc = _sync_service()
    assert _client(fs, svc).post("/internal/sync/process", json=BODY).status_code == 200
    svc.sync_all_services_with_progress.assert_not_called()


def test_missing_job_404() -> None:
    assert _client(FakeFirestore(None), _sync_service()).post("/internal/sync/process", json=BODY).status_code == 404


def test_exception_requeues_until_five_then_failed() -> None:
    fs = FakeFirestore(_job(SyncJobStatus.PENDING))
    svc = _sync_service(side_effect=RuntimeError("lastfm down"))
    client = _client(fs, svc)
    for attempt in range(1, MAX_SYNC_RESUMES + 1):
        r = client.post("/internal/sync/process", json=BODY)
        assert r.status_code == 500
        assert fs.docs[JOB_ID]["status"] == "pending"
        assert fs.docs[JOB_ID]["resume_count"] == attempt
        assert "lastfm down" in fs.docs[JOB_ID]["error"]
    r = client.post("/internal/sync/process", json=BODY)
    assert r.status_code == 200
    assert fs.docs[JOB_ID]["status"] == "failed"


async def test_concurrent_deliveries_only_one_claims() -> None:
    fs = FakeFirestore(_job(SyncJobStatus.IN_PROGRESS, age_seconds=STALE_JOB_THRESHOLD_SECONDS + 5))
    results = await asyncio.gather(internal._claim_job(fs, JOB_ID), internal._claim_job(fs, JOB_ID))  # type: ignore[arg-type]
    assert sorted(r[0] for r in results) == ["claimed", "running"]
    assert fs.docs[JOB_ID]["resume_count"] == 1


async def test_heartbeat_refreshes_updated_at() -> None:
    old = (datetime.now(UTC) - timedelta(seconds=200)).isoformat()
    fs = FakeFirestore(_job(SyncJobStatus.IN_PROGRESS))
    fs.docs[JOB_ID].update(updated_at=old, run_id="r1")
    with patch.object(internal, "HEARTBEAT_INTERVAL_SECONDS", 0.01):
        task = asyncio.create_task(internal._heartbeat(fs, JOB_ID, "r1"))  # type: ignore[arg-type]
        await asyncio.sleep(0.05)
        task.cancel()
    assert fs.docs[JOB_ID]["updated_at"] > old


# --- API side -----------------------------------------------------------------


def test_status_reports_stalled(auth_client: TestClient, mock_firestore_service: MagicMock) -> None:
    job = _job(SyncJobStatus.IN_PROGRESS, age_seconds=STALE_JOB_THRESHOLD_SECONDS + 60)
    mock_firestore_service.query_documents = AsyncMock(return_value=[job.to_dict()])
    r = auth_client.get("/api/services/sync/status", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200
    assert r.json()["active_job"]["status"] == "stalled"
    mock_firestore_service.update_document.assert_not_called()


def test_trigger_sync_reuses_stalled_job(
    auth_client: TestClient, mock_firestore_service: MagicMock, mock_cloud_tasks_service: MagicMock, sample_user: Any
) -> None:
    job = _job(SyncJobStatus.IN_PROGRESS, age_seconds=STALE_JOB_THRESHOLD_SECONDS + 60)
    mock_firestore_service.query_documents = AsyncMock(return_value=[job.to_dict()])
    r = auth_client.post("/api/services/sync", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 202
    assert r.json()["job_id"] == JOB_ID
    mock_cloud_tasks_service.create_sync_task.assert_called_once_with(job_id=JOB_ID, user_id=sample_user.id)
    mock_firestore_service.set_document.assert_not_called()


def test_stale_job_at_resume_cap_is_failed_not_resumed() -> None:
    fs = FakeFirestore(
        _job(SyncJobStatus.IN_PROGRESS, age_seconds=STALE_JOB_THRESHOLD_SECONDS + 5, resume_count=MAX_SYNC_RESUMES)
    )
    svc = _sync_service()
    assert _client(fs, svc).post("/internal/sync/process", json=BODY).status_code == 200
    svc.sync_all_services_with_progress.assert_not_called()
    assert fs.docs[JOB_ID]["status"] == "failed"


def test_pending_job_is_claimed_without_counting_a_resume() -> None:
    fs = FakeFirestore(_job(SyncJobStatus.PENDING, age_seconds=STALE_JOB_THRESHOLD_SECONDS + 5))
    assert _client(fs, _sync_service()).post("/internal/sync/process", json=BODY).status_code == 200
    assert fs.docs[JOB_ID]["resume_count"] == 0


def test_superseded_run_does_not_write_its_result() -> None:
    """A zombie run whose claim was taken over must not complete or fail the job."""
    fs = FakeFirestore(_job(SyncJobStatus.PENDING))

    async def takeover(**_: Any) -> list[Any]:
        fs.docs[JOB_ID]["run_id"] = "someone-else"
        return [
            SimpleNamespace(
                service_type="lastfm",
                tracks_fetched=1,
                tracks_matched=1,
                user_songs_created=1,
                user_songs_updated=0,
                error=None,
            )
        ]

    svc = MagicMock()
    svc.sync_all_services_with_progress = AsyncMock(side_effect=takeover)
    r = _client(fs, svc).post("/internal/sync/process", json=BODY)
    assert r.status_code == 503
    assert fs.docs[JOB_ID]["status"] == "in_progress"
    assert fs.docs[JOB_ID]["run_id"] == "someone-else"


def test_superseded_run_failure_does_not_requeue() -> None:
    fs = FakeFirestore(_job(SyncJobStatus.PENDING))

    async def boom(**_: Any) -> list[Any]:
        fs.docs[JOB_ID]["run_id"] = "someone-else"
        raise RuntimeError("late failure")

    svc = MagicMock()
    svc.sync_all_services_with_progress = AsyncMock(side_effect=boom)
    assert _client(fs, svc).post("/internal/sync/process", json=BODY).status_code == 503
    assert fs.docs[JOB_ID]["status"] == "in_progress"


def test_status_reports_stalled_for_lost_pending_job(
    auth_client: TestClient, mock_firestore_service: MagicMock
) -> None:
    job = _job(SyncJobStatus.PENDING, age_seconds=STALE_PENDING_THRESHOLD_SECONDS + 60)
    mock_firestore_service.query_documents = AsyncMock(return_value=[job.to_dict()])
    r = auth_client.get("/api/services/sync/status", headers={"Authorization": "Bearer test-token"})
    assert r.json()["active_job"]["status"] == "stalled"


def test_trigger_sync_reuses_lost_pending_job(
    auth_client: TestClient, mock_firestore_service: MagicMock, mock_cloud_tasks_service: MagicMock
) -> None:
    job = _job(SyncJobStatus.PENDING, age_seconds=STALE_PENDING_THRESHOLD_SECONDS + 60)
    mock_firestore_service.query_documents = AsyncMock(return_value=[job.to_dict()])
    r = auth_client.post("/api/services/sync", headers={"Authorization": "Bearer test-token"})
    assert r.json()["job_id"] == JOB_ID
    mock_firestore_service.set_document.assert_not_called()


def test_requeued_pending_job_is_not_stalled_while_a_retry_is_scheduled(
    auth_client: TestClient, mock_firestore_service: MagicMock
) -> None:
    """A failed run requeued as pending waits up to the queue's max backoff (600 s) for its retry."""
    job = _job(SyncJobStatus.PENDING, age_seconds=STALE_JOB_THRESHOLD_SECONDS + 300)
    assert not job.is_stalled()
    mock_firestore_service.query_documents = AsyncMock(return_value=[job.to_dict()])
    r = auth_client.get("/api/services/sync/status", headers={"Authorization": "Bearer test-token"})
    assert r.json()["active_job"]["status"] == "pending"


@pytest.mark.parametrize("age", [5, None])
def test_trigger_sync_new_job_when_latest_not_stalled(
    auth_client: TestClient, mock_firestore_service: MagicMock, mock_cloud_tasks_service: MagicMock, age: int | None
) -> None:
    """A live job (fresh heartbeat) or a finished one is not reused."""
    job = _job(SyncJobStatus.IN_PROGRESS, age_seconds=age) if age else _job(SyncJobStatus.COMPLETED, age_seconds=999)
    mock_firestore_service.query_documents = AsyncMock(return_value=[job.to_dict()])
    r = auth_client.post("/api/services/sync", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 202
    assert r.json()["job_id"] != JOB_ID
    mock_firestore_service.set_document.assert_called_once()
    assert mock_cloud_tasks_service.create_sync_task.call_args.kwargs["job_id"] != JOB_ID
