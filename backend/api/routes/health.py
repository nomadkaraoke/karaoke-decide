"""Health check endpoints."""

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from backend.api.deps import FirestoreServiceDep, Settings

logger = logging.getLogger(__name__)

router = APIRouter()


class HealthCheckResponse(BaseModel):
    """Basic health check response."""

    status: str
    service: str


class DeepHealthCheckResponse(BaseModel):
    """Deep health check response with component status."""

    status: str
    service: str
    timestamp: str
    checks: dict[str, dict[str, Any]]


@router.get("/health", response_model=HealthCheckResponse)
async def health_check() -> HealthCheckResponse:
    """Health check endpoint for load balancers and monitoring."""
    return HealthCheckResponse(status="healthy", service="karaoke-decide")


@router.get("/health/deep", response_model=DeepHealthCheckResponse)
async def deep_health_check(
    firestore: FirestoreServiceDep,
    settings: Settings,
) -> DeepHealthCheckResponse:
    """Deep health check that validates connectivity to all infrastructure components.

    Checks:
    - Firestore: Can read from users collection
    - BigQuery: Can query catalog (via import check)
    - Cloud Tasks: Queue is accessible

    This endpoint is useful for post-deploy verification and scheduled monitoring.
    Note: Does not require authentication since it only tests connectivity, not user data.
    """
    checks: dict[str, dict[str, Any]] = {}
    overall_healthy = True

    # Check Firestore connectivity
    try:
        # Try to count documents in a collection (fast operation)
        count = await firestore.count_documents("users", filters=[])
        checks["firestore"] = {
            "status": "healthy",
            "message": f"Connected, {count} users in database",
        }
    except Exception as e:
        logger.error(f"Firestore health check failed: {e}")
        checks["firestore"] = {
            "status": "unhealthy",
            "error": str(e),
        }
        overall_healthy = False

    # Check BigQuery connectivity (via catalog service)
    try:
        from backend.api.routes.catalog import get_catalog_service

        catalog = get_catalog_service()
        # Quick query to verify BigQuery connectivity
        stats = catalog.get_catalog_stats()
        checks["bigquery"] = {
            "status": "healthy",
            "message": f"Connected, {stats.get('total_songs', 0):,} songs in catalog",
        }
    except Exception as e:
        logger.error(f"BigQuery health check failed: {e}")
        checks["bigquery"] = {
            "status": "unhealthy",
            "error": str(e),
        }
        overall_healthy = False

    # Check Cloud Tasks connectivity
    try:
        from google.cloud import tasks_v2

        client = tasks_v2.CloudTasksClient()
        queue_path = client.queue_path(
            settings.google_cloud_project,
            settings.cloud_tasks_location,
            settings.cloud_tasks_queue,
        )
        # Get queue to verify it exists and is accessible
        queue = client.get_queue(name=queue_path)
        checks["cloud_tasks"] = {
            "status": "healthy",
            "message": f"Queue '{queue.name}' accessible",
        }
    except Exception as e:
        logger.error(f"Cloud Tasks health check failed: {e}")
        checks["cloud_tasks"] = {
            "status": "unhealthy",
            "error": str(e),
        }
        overall_healthy = False

    return DeepHealthCheckResponse(
        status="healthy" if overall_healthy else "degraded",
        service="karaoke-decide",
        timestamp=datetime.now(UTC).isoformat(),
        checks=checks,
    )
