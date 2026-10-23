"""Internal routes for Cloud Tasks callbacks.

These endpoints are called by Cloud Tasks, not by users directly.
Protected by OIDC token verification (backend.api.internal_auth) in production.
"""

import logging
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from backend.api.deps import FirestoreServiceDep, Settings, SyncServiceDep
from backend.api.internal_auth import InternalCaller
from backend.config import BackendSettings
from backend.models.sync_job import SyncJob, SyncJobProgress, SyncJobResult, SyncJobStatus
from backend.services.firestore_service import FirestoreService

logger = logging.getLogger(__name__)

router = APIRouter()


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

    try:
        # Get the job from Firestore
        job_data = await firestore.get_document("sync_jobs", job_id)
        if not job_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Sync job not found: {job_id}",
            )

        job = SyncJob.from_dict(job_data)

        # Check if already in progress, completed, or failed
        if job.status in (SyncJobStatus.IN_PROGRESS, SyncJobStatus.COMPLETED, SyncJobStatus.FAILED):
            logger.info(f"Job already {job.status.value}: {job_id}")
            return SyncProcessResponse(
                job_id=job_id,
                status=job.status.value,
                message=f"Job already {job.status.value}",
            )

        # Update status to in_progress
        job.status = SyncJobStatus.IN_PROGRESS
        job.updated_at = datetime.now(UTC)
        await firestore.set_document("sync_jobs", job_id, job.to_dict())

        # Execute sync with progress updates
        results = await sync_service.sync_all_services_with_progress(
            user_id=user_id,
            progress_callback=_create_progress_callback(firestore, job_id),
        )

        # Update job with results
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
        logger.exception(f"Sync job failed: {job_id}")

        # Update job with error
        try:
            job_data = await firestore.get_document("sync_jobs", job_id)
            if job_data:
                job = SyncJob.from_dict(job_data)
                job.status = SyncJobStatus.FAILED
                job.error = str(e)
                job.updated_at = datetime.now(UTC)
                await firestore.set_document("sync_jobs", job_id, job.to_dict())
        except Exception:
            logger.exception("Failed to update job status on error")

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Sync failed: {e}",
        )


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
