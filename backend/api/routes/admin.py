"""Admin routes for system management.

Provides endpoints for:
- Dashboard statistics
- User management
- Sync job monitoring
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status

from backend.api.deps import AdminUser, FirestoreServiceDep
from backend.models.admin import (
    AdminStats,
    DataSummary,
    ServiceStats,
    ServiceSummary,
    SyncJobDetail,
    SyncJobListItem,
    SyncJobListResponse,
    SyncJobProgress,
    SyncJobResultItem,
    SyncJobStats,
    SyncJobSummary,
    UserDetail,
    UserListItem,
    UserListResponse,
    UserStats,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# -----------------------------------------------------------------------------
# Dashboard Stats
# -----------------------------------------------------------------------------


@router.get("/stats", response_model=AdminStats)
async def get_admin_stats(
    user: AdminUser,
    firestore: FirestoreServiceDep,
) -> AdminStats:
    """Get dashboard statistics.

    Returns aggregate counts for users, sync jobs, and service connections.

    Note: The Firestore DB is shared with karaoke-gen (being migrated to separate collection).
    Count queries use simple filters to avoid complex Firestore index requirements.
    Once migration is complete, these counts will only include karaoke-decide users.
    """
    # User stats - count users with is_guest field (karaoke-decide specific field)
    # Using is_guest filter to exclude karaoke-gen users which don't have this field
    total_users = await firestore.count_documents("users", filters=[("is_guest", "in", [True, False])])
    verified_users = await firestore.count_documents("users", filters=[("is_guest", "==", False)])
    guest_users = await firestore.count_documents("users", filters=[("is_guest", "==", True)])

    # Active users (synced in last 7 days)
    seven_days_ago = datetime.now(UTC) - timedelta(days=7)
    active_users = await firestore.count_documents("users", filters=[("last_sync_at", ">=", seven_days_ago)])

    # Sync job stats (last 24 hours)
    twenty_four_hours_ago = datetime.now(UTC) - timedelta(hours=24)
    total_jobs = await firestore.count_documents("sync_jobs", filters=[("created_at", ">=", twenty_four_hours_ago)])
    pending_jobs = await firestore.count_documents(
        "sync_jobs",
        filters=[
            ("created_at", ">=", twenty_four_hours_ago),
            ("status", "==", "pending"),
        ],
    )
    in_progress_jobs = await firestore.count_documents(
        "sync_jobs",
        filters=[
            ("created_at", ">=", twenty_four_hours_ago),
            ("status", "==", "in_progress"),
        ],
    )
    completed_jobs = await firestore.count_documents(
        "sync_jobs",
        filters=[
            ("created_at", ">=", twenty_four_hours_ago),
            ("status", "==", "completed"),
        ],
    )
    failed_jobs = await firestore.count_documents(
        "sync_jobs",
        filters=[
            ("created_at", ">=", twenty_four_hours_ago),
            ("status", "==", "failed"),
        ],
    )

    # Service connection stats
    spotify_connected = await firestore.count_documents("music_services", filters=[("service_type", "==", "spotify")])
    lastfm_connected = await firestore.count_documents("music_services", filters=[("service_type", "==", "lastfm")])

    return AdminStats(
        users=UserStats(
            total=total_users,
            verified=verified_users,
            guests=guest_users,
            active_7d=active_users,
        ),
        sync_jobs=SyncJobStats(
            total=total_jobs,
            pending=pending_jobs,
            in_progress=in_progress_jobs,
            completed=completed_jobs,
            failed=failed_jobs,
        ),
        services=ServiceStats(
            spotify_connected=spotify_connected,
            lastfm_connected=lastfm_connected,
        ),
    )


# -----------------------------------------------------------------------------
# User Management
# -----------------------------------------------------------------------------


@router.get("/users", response_model=UserListResponse)
async def list_users(
    user: AdminUser,
    firestore: FirestoreServiceDep,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    filter: Literal["all", "verified", "guests"] = Query(default="all"),
    search: str | None = Query(default=None, description="Search by email"),
) -> UserListResponse:
    """List users with pagination and filtering."""
    # Filter for karaoke-decide users only (those with user_id field).
    # The Firestore DB is shared with karaoke-gen which has different user schema.
    filters: list[tuple[str, str, object]] = [("user_id", "!=", "")]

    if filter == "verified":
        filters.append(("is_guest", "==", False))
    elif filter == "guests":
        filters.append(("is_guest", "==", True))

    # Note: Firestore doesn't support LIKE queries for partial text search.
    # For email search, we fetch up to 500 docs and filter client-side.
    # This is acceptable for admin-only functionality with limited user base.
    # Future improvement: Add indexed normalized_email field for range queries,
    # or integrate Algolia/Elasticsearch for full-text search.

    # Get total count for pagination
    total = await firestore.count_documents("users", filters=filters if filters else None)

    # Get users with pagination
    user_docs = await firestore.query_documents(
        "users",
        filters=filters if filters else None,
        order_by="created_at",
        order_direction="DESCENDING",
        limit=limit if not search else 500,  # Get more if searching
        offset=offset if not search else 0,
    )

    users = []
    for doc in user_docs:
        user_item = UserListItem(
            id=doc.get("user_id", ""),
            email=doc.get("email"),
            display_name=doc.get("display_name"),
            is_guest=doc.get("is_guest", False),
            is_admin=doc.get("is_admin", False),
            created_at=_parse_datetime(doc.get("created_at")) or datetime.now(UTC),
            last_sync_at=_parse_datetime(doc.get("last_sync_at")),
            quiz_completed_at=_parse_datetime(doc.get("quiz_completed_at")),
            total_songs_known=doc.get("total_songs_known", 0),
        )

        # Apply email search filter client-side
        if search:
            email = doc.get("email") or ""
            if search.lower() not in email.lower():
                continue

        users.append(user_item)

    # Apply pagination after search filtering
    if search:
        total = len(users)
        users = users[offset : offset + limit]

    return UserListResponse(
        users=users,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/users/{user_id}", response_model=UserDetail)
async def get_user_detail(
    user_id: str,
    user: AdminUser,
    firestore: FirestoreServiceDep,
) -> UserDetail:
    """Get detailed information about a specific user."""
    # Get user document - guest users use user_id as doc ID, regular users need query
    if user_id.startswith("guest_"):
        user_doc = await firestore.get_document("users", user_id)
    else:
        # Regular users: query by user_id field (doc ID is email hash)
        user_docs = await firestore.query_documents(
            "users",
            filters=[("user_id", "==", user_id)],
            limit=1,
        )
        user_doc = user_docs[0] if user_docs else None

    if not user_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Get connected services
    service_docs = await firestore.query_documents(
        "music_services",
        filters=[("user_id", "==", user_id)],
    )
    services = [
        ServiceSummary(
            service_type=doc.get("service_type", ""),
            service_username=doc.get("service_username", ""),
            sync_status=doc.get("sync_status", "idle"),
            last_sync_at=_parse_datetime(doc.get("last_sync_at")),
            tracks_synced=doc.get("tracks_synced", 0),
            sync_error=doc.get("sync_error"),
        )
        for doc in service_docs
    ]

    # Get recent sync jobs (last 10)
    job_docs = await firestore.query_documents(
        "sync_jobs",
        filters=[("user_id", "==", user_id)],
        order_by="created_at",
        order_direction="DESCENDING",
        limit=10,
    )
    sync_jobs = [
        SyncJobSummary(
            id=doc.get("id", ""),
            status=doc.get("status", ""),
            created_at=_parse_datetime(doc.get("created_at")) or datetime.now(UTC),
            completed_at=_parse_datetime(doc.get("completed_at")),
            error=doc.get("error"),
        )
        for doc in job_docs
    ]

    # Get data counts
    artists_count = await firestore.count_documents("user_artists", filters=[("user_id", "==", user_id)])
    songs_count = await firestore.count_documents("user_songs", filters=[("user_id", "==", user_id)])
    playlists_count = await firestore.count_documents("playlists", filters=[("user_id", "==", user_id)])

    return UserDetail(
        id=user_doc.get("user_id", ""),
        email=user_doc.get("email"),
        display_name=user_doc.get("display_name"),
        is_guest=user_doc.get("is_guest", False),
        is_admin=user_doc.get("is_admin", False),
        created_at=_parse_datetime(user_doc.get("created_at")) or datetime.now(UTC),
        last_sync_at=_parse_datetime(user_doc.get("last_sync_at")),
        quiz_completed_at=_parse_datetime(user_doc.get("quiz_completed_at")),
        total_songs_known=user_doc.get("total_songs_known", 0),
        services=services,
        sync_jobs=sync_jobs,
        data_summary=DataSummary(
            artists_count=artists_count,
            songs_count=songs_count,
            playlists_count=playlists_count,
        ),
    )


# -----------------------------------------------------------------------------
# Sync Job Monitoring
# -----------------------------------------------------------------------------


@router.get("/sync-jobs", response_model=SyncJobListResponse)
async def list_sync_jobs(
    user: AdminUser,
    firestore: FirestoreServiceDep,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status_filter: Literal["all", "pending", "in_progress", "completed", "failed"] = Query(
        default="all", alias="status"
    ),
) -> SyncJobListResponse:
    """List sync jobs with pagination and filtering."""
    filters: list[tuple[str, str, object]] = []

    if status_filter != "all":
        filters.append(("status", "==", status_filter))

    # Get total count
    total = await firestore.count_documents("sync_jobs", filters=filters if filters else None)

    # Get jobs with pagination
    job_docs = await firestore.query_documents(
        "sync_jobs",
        filters=filters if filters else None,
        order_by="created_at",
        order_direction="DESCENDING",
        limit=limit,
        offset=offset,
    )

    # Collect user IDs to batch fetch emails
    user_ids = list({doc.get("user_id") for doc in job_docs if doc.get("user_id")})
    user_emails: dict[str, str | None] = {}

    # Batch fetch user emails using Firestore "in" query (max 30 per query)
    for i in range(0, len(user_ids), 30):
        batch_ids = user_ids[i : i + 30]
        if batch_ids:
            user_docs = await firestore.query_documents("users", filters=[("user_id", "in", batch_ids)])
            for user_doc in user_docs:
                user_emails[user_doc.get("user_id", "")] = user_doc.get("email")

    jobs = [
        SyncJobListItem(
            id=doc.get("id", ""),
            user_id=doc.get("user_id", ""),
            user_email=user_emails.get(doc.get("user_id", "")),
            status=doc.get("status", ""),
            created_at=_parse_datetime(doc.get("created_at")) or datetime.now(UTC),
            completed_at=_parse_datetime(doc.get("completed_at")),
            error=doc.get("error"),
        )
        for doc in job_docs
    ]

    return SyncJobListResponse(
        jobs=jobs,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/sync-jobs/{job_id}", response_model=SyncJobDetail)
async def get_sync_job_detail(
    job_id: str,
    user: AdminUser,
    firestore: FirestoreServiceDep,
) -> SyncJobDetail:
    """Get detailed information about a specific sync job."""
    # Get job document
    job_doc = await firestore.get_document("sync_jobs", job_id)
    if not job_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sync job not found",
        )

    # Get user email
    user_id = job_doc.get("user_id", "")
    user_email = None
    if user_id:
        user_doc = await firestore.get_document("users", user_id)
        if user_doc:
            user_email = user_doc.get("email")

    # Parse progress
    progress_data = job_doc.get("progress")
    progress = None
    if progress_data:
        progress = SyncJobProgress(
            current_service=progress_data.get("current_service"),
            current_phase=progress_data.get("current_phase"),
            total_tracks=progress_data.get("total_tracks", 0),
            processed_tracks=progress_data.get("processed_tracks", 0),
            matched_tracks=progress_data.get("matched_tracks", 0),
            percentage=progress_data.get("percentage", 0),
        )

    # Parse results
    results_data = job_doc.get("results", [])
    results = [
        SyncJobResultItem(
            service_type=r.get("service_type", ""),
            tracks_fetched=r.get("tracks_fetched", 0),
            tracks_matched=r.get("tracks_matched", 0),
            user_songs_created=r.get("user_songs_created", 0),
            user_songs_updated=r.get("user_songs_updated", 0),
            artists_stored=r.get("artists_stored", 0),
            error=r.get("error"),
        )
        for r in results_data
    ]

    return SyncJobDetail(
        id=job_doc.get("id", ""),
        user_id=user_id,
        user_email=user_email,
        status=job_doc.get("status", ""),
        created_at=_parse_datetime(job_doc.get("created_at")) or datetime.now(UTC),
        completed_at=_parse_datetime(job_doc.get("completed_at")),
        error=job_doc.get("error"),
        progress=progress,
        results=results,
    )


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _parse_datetime(value: str | datetime | None) -> datetime | None:
    """Parse datetime from various formats. Returns None for invalid/missing values."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
