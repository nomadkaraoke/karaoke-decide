"""Internal routes for Cloud Tasks callbacks.

These endpoints are called by Cloud Tasks, not by users directly.
Protected by OIDC token verification (backend.api.internal_auth) in production.
"""

import asyncio
import logging
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from backend.api.deps import FirestoreServiceDep, Settings, SyncServiceDep
from backend.api.internal_auth import InternalCaller
from backend.config import BackendSettings
from backend.models.sync_job import MAX_SYNC_RESUMES, SyncJob, SyncJobProgress, SyncJobResult, SyncJobStatus
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

    heartbeat = asyncio.create_task(_heartbeat(firestore, job_id))
    try:
        results = await sync_service.sync_all_services_with_progress(
            user_id=user_id,
            progress_callback=_create_progress_callback(firestore, job_id),
        )
        heartbeat.cancel()

        # Re-read so progress written during the run isn't clobbered.
        job_data = await firestore.get_document("sync_jobs", job_id)
        job = SyncJob.from_dict(job_data) if job_data else job
        job.status = SyncJobStatus.COMPLETED
        job.results = [
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
        job.completed_at = datetime.now(UTC)
        job.updated_at = datetime.now(UTC)

        # Check for errors in results
        errors = [r.error for r in results if r.error]
        if errors:
            job.error = "; ".join(errors)

        await firestore.set_document("sync_jobs", job_id, job.to_dict())

        # Send completion email
        try:
            await _send_sync_completion_email(user_id, job, firestore, settings)
        except Exception as e:
            logger.error(f"Failed to send completion email: {e}")

        logger.info(f"Sync job completed: {job_id}")
        return SyncProcessResponse(
            job_id=job_id,
            status="completed",
            message="Sync completed successfully",
        )

    except Exception as e:
        heartbeat.cancel()
        logger.exception(f"Sync job failed: {job_id}")
        requeue = await _record_failure(firestore, job_id, e)
        if requeue:
            # 500 makes Cloud Tasks retry; the retry resumes from the checkpoint.
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Sync failed, will retry: {e}",
            )
        return SyncProcessResponse(job_id=job_id, status="failed", message=f"Sync failed: {e}")
    finally:
        heartbeat.cancel()


async def _claim_job(firestore: FirestoreService, job_id: str) -> tuple[str, SyncJob | None]:
    """Atomically decide whether this delivery may run the job.

    Runs in one Firestore transaction so two concurrent deliveries cannot both claim it.
    Returns ("missing" | "done" | "running" | "claimed", job). A claim marks the job
    in_progress with a fresh heartbeat; claiming a stalled job counts as a resume.
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
            job.resume_count += 1
            logger.warning(f"Resuming stalled sync job {job_id} (resume {job.resume_count})")
        job.status = SyncJobStatus.IN_PROGRESS
        job.updated_at = datetime.now(UTC)
        tx.set("sync_jobs", job_id, job.to_dict())
        return "claimed", job

    return await firestore.run_transaction(claim)


async def _record_failure(firestore: FirestoreService, job_id: str, error: Exception) -> bool:
    """Put the job back to pending for a retry, or mark it failed after too many. True = retry."""
    try:
        data = await firestore.get_document("sync_jobs", job_id)
        if not data:
            return True
        job = SyncJob.from_dict(data)
        job.error = str(error)
        job.updated_at = datetime.now(UTC)
        requeue = job.resume_count < MAX_SYNC_RESUMES
        if requeue:
            job.resume_count += 1
            job.status = SyncJobStatus.PENDING
        else:
            job.status = SyncJobStatus.FAILED
            job.completed_at = datetime.now(UTC)
        await firestore.set_document("sync_jobs", job_id, job.to_dict())
        return requeue
    except Exception:
        logger.exception("Failed to update job status on error")
        return True


async def _heartbeat(firestore: FirestoreService, job_id: str) -> None:
    """Keep updated_at fresh while the sync runs, so a retry sees this run as alive."""
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
        try:
            await firestore.update_document("sync_jobs", job_id, {"updated_at": datetime.now(UTC).isoformat()})
        except Exception:
            logger.warning(f"Sync heartbeat write failed for {job_id}", exc_info=True)


def _create_progress_callback(firestore: FirestoreService, job_id: str) -> Callable[..., Coroutine[Any, Any, None]]:
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

        await firestore.update_document(
            "sync_jobs",
            job_id,
            {
                "progress": {
                    "current_service": progress.current_service,
                    "current_phase": progress.current_phase,
                    "total_tracks": progress.total_tracks,
                    "processed_tracks": progress.processed_tracks,
                    "matched_tracks": progress.matched_tracks,
                    "percentage": progress.percentage,
                },
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )

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
