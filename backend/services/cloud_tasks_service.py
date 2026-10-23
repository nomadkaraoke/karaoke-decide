"""Cloud Tasks service for enqueueing background jobs."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import TYPE_CHECKING, Any

import httpx

from backend.config import BackendSettings
from karaoke_decide.core.lazy_import import lazy_module

if TYPE_CHECKING:
    from google.cloud import tasks_v2
    from google.protobuf import timestamp_pb2
else:  # imported on first use (API cold start)
    tasks_v2 = lazy_module("google.cloud.tasks_v2")
    timestamp_pb2 = lazy_module("google.protobuf.timestamp_pb2")

logger = logging.getLogger(__name__)

# DECIDE_INLINE_TASKS: a sync run can take as long as Cloud Run allows a request.
INLINE_TASK_TIMEOUT_S = 1800.0
# Strong references to running inline tasks (the event loop only keeps weak ones).
_INLINE_TASKS: set[asyncio.Task[None]] = set()


class CloudTasksService:
    """Service for creating Cloud Tasks for background processing."""

    def __init__(self, settings: BackendSettings):
        """Initialize the Cloud Tasks service.

        Args:
            settings: Backend settings with Cloud Tasks configuration.
        """
        self.settings = settings
        self.project = settings.google_cloud_project
        self.location = settings.cloud_tasks_location
        self.queue = settings.cloud_tasks_queue
        self._client: tasks_v2.CloudTasksClient | None = None
        self._client_lock = threading.Lock()
        self._inline_transport: httpx.AsyncBaseTransport | None = None  # tests only

    @property
    def client(self) -> tasks_v2.CloudTasksClient:
        """Get or create Cloud Tasks client (thread-safe)."""
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    self._client = tasks_v2.CloudTasksClient()
        return self._client

    @property
    def queue_path(self) -> str:
        """Get the full queue path."""
        return self.client.queue_path(self.project, self.location, self.queue)

    def create_sync_task(
        self,
        job_id: str,
        user_id: str,
        delay_seconds: int = 0,
    ) -> str:
        """Create a Cloud Task for sync processing.

        Args:
            job_id: Sync job ID.
            user_id: User ID to sync.
            delay_seconds: Optional delay before task execution.

        Returns:
            Task name.
        """
        payload = {"job_id": job_id, "user_id": user_id}
        if self.settings.inline_tasks:
            if delay_seconds > 0:
                logger.info(f"Inline task inline:{job_id}: delay_seconds={delay_seconds} ignored locally")
            return self._run_inline("/internal/sync/process", payload, name=f"inline:{job_id}")

        # Build the Cloud Run URL for the internal endpoint
        cloud_run_url = self.settings.cloud_run_url
        if not cloud_run_url:
            # Fallback for development
            cloud_run_url = f"http://localhost:{self.settings.api_port}"

        url = f"{cloud_run_url}/internal/sync/process"

        # Create task request
        task: dict[str, Any] = {
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": url,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(payload).encode(),
            }
        }

        # Add OIDC token for Cloud Run authentication
        # Use default compute service account
        if self.settings.is_production:
            service_account_email = f"{self._get_project_number()}-compute@developer.gserviceaccount.com"
            task["http_request"]["oidc_token"] = {
                "service_account_email": service_account_email,
                "audience": cloud_run_url,
            }

        # Add schedule time if delayed
        if delay_seconds > 0:
            import time

            schedule_time = timestamp_pb2.Timestamp()
            schedule_time.FromSeconds(int(time.time()) + delay_seconds)
            task["schedule_time"] = schedule_time

        # Create the task
        request = tasks_v2.CreateTaskRequest(
            parent=self.queue_path,
            task=task,
        )

        response = self.client.create_task(request=request)
        logger.info(f"Created sync task: {response.name}")
        return response.name

    def _run_inline(self, path: str, payload: dict[str, Any], name: str) -> str:
        """DECIDE_INLINE_TASKS (local stack): POST the task to this API now, in the background.

        Fire-and-forget on the running event loop, never awaited by the caller, so the request
        that enqueued it returns at once and the handler runs as its own request (the same
        /internal route Cloud Tasks would call; non-production needs no OIDC). No retries.
        """
        url = self.settings.public_api_base_url.rstrip("/") + path
        task = asyncio.get_running_loop().create_task(self._post_inline(url, payload, name))
        _INLINE_TASKS.add(task)
        task.add_done_callback(_INLINE_TASKS.discard)
        logger.info(f"Running task inline: {name} -> {url}")
        return name

    async def _post_inline(self, url: str, payload: dict[str, Any], name: str) -> None:
        try:
            async with httpx.AsyncClient(transport=self._inline_transport, timeout=INLINE_TASK_TIMEOUT_S) as client:
                response = await client.post(url, json=payload)
        except httpx.HTTPError as e:
            logger.warning(f"Inline task {name} failed: {type(e).__name__}: {e}")
            return
        if response.status_code >= 400:
            logger.warning(f"Inline task {name} answered {response.status_code}: {response.text[:300]}")

    def _get_project_number(self) -> str:
        """Get the GCP project number.

        In production, this is needed for service account references.
        """
        if self.settings.google_cloud_project_number:
            return self.settings.google_cloud_project_number
        # Fallback for development - should be set via GOOGLE_CLOUD_PROJECT_NUMBER env var
        raise ValueError("GOOGLE_CLOUD_PROJECT_NUMBER must be set in production for Cloud Tasks OIDC auth")


# Lazy initialization
_cloud_tasks_service: CloudTasksService | None = None
_cloud_tasks_lock = threading.Lock()


def get_cloud_tasks_service(settings: BackendSettings | None = None) -> CloudTasksService:
    """Get the Cloud Tasks service instance (thread-safe singleton).

    Args:
        settings: Optional settings override. Only used on first call.

    Returns:
        CloudTasksService instance.
    """
    global _cloud_tasks_service

    if _cloud_tasks_service is None:
        with _cloud_tasks_lock:
            # Double-check after acquiring lock
            if _cloud_tasks_service is None:
                if settings is None:
                    from backend.config import get_backend_settings

                    settings = get_backend_settings()
                _cloud_tasks_service = CloudTasksService(settings)

    return _cloud_tasks_service
