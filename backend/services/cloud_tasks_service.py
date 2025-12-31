"""Cloud Tasks service for enqueueing background jobs."""

import json
import logging
from typing import Any

from google.cloud import tasks_v2
from google.protobuf import timestamp_pb2

from backend.config import BackendSettings

logger = logging.getLogger(__name__)


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

    @property
    def client(self) -> tasks_v2.CloudTasksClient:
        """Get or create Cloud Tasks client."""
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
        # Build the Cloud Run URL for the internal endpoint
        cloud_run_url = self.settings.cloud_run_url
        if not cloud_run_url:
            # Fallback for development
            cloud_run_url = f"http://localhost:{self.settings.api_port}"

        url = f"{cloud_run_url}/internal/sync/process"

        # Build task payload
        payload = {
            "job_id": job_id,
            "user_id": user_id,
        }

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

    def _get_project_number(self) -> str:
        """Get the GCP project number.

        In production, this is needed for service account references.
        """
        # Hardcoded for now - could be looked up via Resource Manager API
        return "718638054799"


# Lazy initialization
_cloud_tasks_service: CloudTasksService | None = None


def get_cloud_tasks_service(settings: BackendSettings | None = None) -> CloudTasksService:
    """Get the Cloud Tasks service instance.

    Args:
        settings: Optional settings override.

    Returns:
        CloudTasksService instance.
    """
    global _cloud_tasks_service

    if _cloud_tasks_service is None or settings is not None:
        if settings is None:
            from backend.config import get_backend_settings

            settings = get_backend_settings()
        _cloud_tasks_service = CloudTasksService(settings)

    return _cloud_tasks_service
