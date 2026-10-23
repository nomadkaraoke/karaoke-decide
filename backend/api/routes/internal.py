"""Internal routes for Cloud Tasks callbacks.

These endpoints are called by Cloud Tasks, not by users directly.
Protected by OIDC token verification (backend.api.internal_auth) in production.
"""

import asyncio
import logging
import uuid
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from backend.api.deps import FirestoreServiceDep, Settings, SyncServiceDep
from backend.api.internal_auth import InternalCaller
from backend.config import BackendSettings
from backend.models.sync_job import (
    MAX_SYNC_RESUMES,
    RunSupersededError,
    SyncJob,
    SyncJobProgress,
    SyncJobResult,
    SyncJobStatus,
)
from backend.services.firestore_service import FirestoreService, TransactionView

logger = logging.getLogger(__name__)

router = APIRouter()

HEARTBEAT_INTERVAL_SECONDS = 60


class SyncProcessRequest(BaseModel):
    """Request body for sync process task."""

    job_id: str
    user_id: str


class SyncProcessResponse(BaseModel):
    """Response from sync process task."""

    job_id: str
    status: str
    message: str


@router.post("/sync/process", response_model=SyncProcessResponse, dependencies=[InternalCaller])
async def process_sync_task(
    body: SyncProcessRequest,
    settings: Settings,
    firestore: FirestoreServiceDep,
    sync_service: SyncServiceDep,
) -> SyncProcessResponse:
    """Process a sync task from Cloud Tasks.

    This endpoint is called by Cloud Tasks to execute the actual sync.
    It updates the job status in Firestore as it progresses.
    """
    job_id = body.job_id
    user_id = body.user_id

    logger.info(f"Processing sync task: job_id={job_id}, user_id={user_id}")

    claim, job = await _claim_job(firestore, job_id)
    if claim == "missing":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Sync job not found: {job_id}")
    if claim == "running":
        # Another run is alive (fresh heartbeat): make Cloud Tasks retry later instead of racing it.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="in_progress",
            headers={"Retry-After": str(HEARTBEAT_INTERVAL_SECONDS)},
        )
    assert job is not None
    if claim == "done":
        logger.info(f"Job already {job.status.value}: {job_id}")
        return SyncProcessResponse(job_id=job_id, status=job.status.value, message=f"Job already {job.status.value}")

    run_id = job.run_id or ""
    superseded = HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="superseded")
    heartbeat = asyncio.create_task(_heartbeat(firestore, job_id, run_id))
    try:
        results = await sync_service.sync_all_services_with_progress(
            user_id=user_id,
            progress_callback=_create_progress_callback(firestore, job_id, run_id),
        )
        await _stop(heartbeat)

        errors = [r.error for r in results if r.error]

        def complete(j: SyncJob) -> None:
            j.status = SyncJobStatus.COMPLETED
            j.results = [
                SyncJobResult(
                    service_type=r.service_type,
                    tracks_fetched=r.tracks_fetched,
                    tracks_matched=r.tracks_matched,
                    user_songs_created=r.user_songs_created,
                    user_songs_updated=r.user_songs_updated,
                    artists_stored=getattr(r, "artists_stored", 0),
                    error=r.error,
                )
                for r in results
            ]
            j.completed_at = j.updated_at = datetime.now(UTC)
            # Always assign: a resumed run must not keep a stale error from an earlier attempt.
            j.error = "; ".join(errors) if errors else None

        # Only the run that still owns the job may record its outcome.
        done = await _update_owned(firestore, job_id, run_id, complete)
        if done is None:
            logger.warning(f"Sync run for {job_id} was superseded; discarding its result")
            raise superseded

        # Send completion email
        try:
            await _send_sync_completion_email(user_id, done, firestore, settings)
        except Exception as e:
            logger.error(f"Failed to send completion email: {e}")

        logger.info(f"Sync job completed: {job_id}")
        return SyncProcessResponse(
            job_id=job_id,
            status="completed",
            message="Sync completed successfully",
        )

    except HTTPException:
        raise
    except RunSupersededError:
        logger.warning(f"Sync run for {job_id} was superseded")
        raise superseded
    except Exception as e:
        await _stop(heartbeat)
        logger.exception(f"Sync job failed: {job_id}")
        requeue = await _record_failure(firestore, job_id, run_id, e)
        if requeue is None:
            raise superseded
        if requeue:
            # 500 makes Cloud Tasks retry; the retry resumes from the checkpoint.
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Sync failed, will retry: {e}",
            )
        return SyncProcessResponse(job_id=job_id, status="failed", message=f"Sync failed: {e}")
    finally:
        await _stop(heartbeat)


async def _stop(task: "asyncio.Task[None]") -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def _claim_job(firestore: FirestoreService, job_id: str) -> tuple[str, SyncJob | None]:
    """Atomically decide whether this delivery may run the job.

    Runs in one Firestore transaction so two concurrent deliveries cannot both claim it.
    Returns ("missing" | "done" | "running" | "claimed", job). A claim marks the job
    in_progress with a fresh heartbeat and a new run_id; claiming a stalled job counts as a
    resume, and a job already resumed MAX_SYNC_RESUMES times is failed instead.
    """

    async def claim(tx: TransactionView) -> tuple[str, SyncJob | None]:
        data = await tx.get("sync_jobs", job_id)
        if not data:
            return "missing", None
        job = SyncJob.from_dict(data)
        if job.status in (SyncJobStatus.COMPLETED, SyncJobStatus.FAILED):
            return "done", job
        if job.status == SyncJobStatus.IN_PROGRESS:
            if not job.is_stalled():
                return "running", job
            if job.resume_count >= MAX_SYNC_RESUMES:
                job.status = SyncJobStatus.FAILED
                job.error = job.error or f"Sync stalled {job.resume_count} times"
                job.completed_at = job.updated_at = datetime.now(UTC)
                tx.set("sync_jobs", job_id, job.to_dict())
                return "done", job
            job.resume_count += 1
            logger.warning(f"Resuming stalled sync job {job_id} (resume {job.resume_count})")
        job.status = SyncJobStatus.IN_PROGRESS
        job.updated_at = datetime.now(UTC)
        job.run_id = uuid.uuid4().hex
        tx.set("sync_jobs", job_id, job.to_dict())
        return "claimed", job

    return await firestore.run_transaction(claim)


async def _update_owned(
    firestore: FirestoreService,
    job_id: str,
    run_id: str,
    mutate: Callable[[SyncJob], None],
) -> SyncJob | None:
    """Apply ``mutate`` to the job only if ``run_id`` still owns it (one transaction).

    ``mutate`` must be side-effect free (the transaction may retry). Returns the updated
    job, or None when another run has taken over.
    """

    async def txn(tx: TransactionView) -> SyncJob | None:
        data = await tx.get("sync_jobs", job_id)
        if not data or data.get("run_id") != run_id:
            return None
        job = SyncJob.from_dict(data)
        mutate(job)
        tx.set("sync_jobs", job_id, job.to_dict())
        return job

    return await firestore.run_transaction(txn)


async def _record_failure(firestore: FirestoreService, job_id: str, run_id: str, error: Exception) -> bool | None:
    """Put the job back to pending for a retry, or fail it after too many.

    True = retry, False = failed for good, None = this run no longer owns the job.
    """

    def fail(job: SyncJob) -> None:
        job.error = str(error)
        job.updated_at = datetime.now(UTC)
        if job.resume_count < MAX_SYNC_RESUMES:
            job.resume_count += 1
            job.status = SyncJobStatus.PENDING
        else:
            job.status = SyncJobStatus.FAILED
            job.completed_at = datetime.now(UTC)

    try:
        job = await _update_owned(firestore, job_id, run_id, fail)
    except Exception:
        logger.exception("Failed to update job status on error")
        return True
    if job is None:
        return None
    return job.status == SyncJobStatus.PENDING


async def _heartbeat(firestore: FirestoreService, job_id: str, run_id: str) -> None:
    """Keep updated_at fresh while the sync runs, so a retry sees this run as alive."""

    def beat(job: SyncJob) -> None:
        job.updated_at = datetime.now(UTC)

    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
        try:
            if await _update_owned(firestore, job_id, run_id, beat) is None:
                return  # superseded: stop beating for a job we no longer own
        except Exception:
            logger.warning(f"Sync heartbeat write failed for {job_id}", exc_info=True)


def _create_progress_callback(
    firestore: FirestoreService, job_id: str, run_id: str
) -> Callable[..., Coroutine[Any, Any, None]]:
    """Create a progress callback function for sync service.

    Args:
        firestore: Firestore service.
        job_id: Job ID to update.

    Returns:
        Async callback function.
    """

    async def callback(
        current_service: str | None = None,
        current_phase: str | None = None,
        total_tracks: int = 0,
        processed_tracks: int = 0,
        matched_tracks: int = 0,
    ) -> None:
        """Update job progress in Firestore."""
        progress = SyncJobProgress(
            current_service=current_service,
            current_phase=current_phase,
            total_tracks=total_tracks,
            processed_tracks=processed_tracks,
            matched_tracks=matched_tracks,
        )

        def apply(job: SyncJob) -> None:
            job.progress = progress
            job.updated_at = datetime.now(UTC)

        if await _update_owned(firestore, job_id, run_id, apply) is None:
            raise RunSupersededError(job_id)

    return callback


async def _send_sync_completion_email(
    user_id: str,
    job: SyncJob,
    firestore: FirestoreService,
    settings: BackendSettings,
) -> None:
    """Send email notification when sync completes.

    Args:
        user_id: User ID.
        job: Completed sync job.
        firestore: Firestore service.
        settings: App settings.
    """
    from backend.services.email_service import get_email_service

    # Get user email from decide_users collection
    user_data = await firestore.get_document("decide_users", user_id)
    if not user_data or not user_data.get("email"):
        logger.warning(f"No email found for user: {user_id}")
        return

    email = user_data["email"]

    # Calculate totals
    total_matched = sum(r.tracks_matched for r in job.results)
    total_artists = sum(r.artists_stored for r in job.results)
    services = [r.service_type for r in job.results]

    # Send email
    email_service = get_email_service(settings)
    await email_service.send_sync_complete_email(
        to_email=email,
        songs_matched=total_matched,
        artists_stored=total_artists,
        services=services,
    )
