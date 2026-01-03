"""Admin API response models."""

from datetime import datetime

from pydantic import BaseModel


class UserStats(BaseModel):
    """User statistics."""

    total: int
    verified: int
    guests: int
    active_7d: int


class SyncJobStats(BaseModel):
    """Sync job statistics (last 24 hours)."""

    total: int
    pending: int
    in_progress: int
    completed: int
    failed: int


class ServiceStats(BaseModel):
    """Music service connection statistics."""

    spotify_connected: int
    lastfm_connected: int


class AdminStats(BaseModel):
    """Combined admin statistics for dashboard."""

    users: UserStats
    sync_jobs: SyncJobStats
    services: ServiceStats


class UserListItem(BaseModel):
    """User item for admin list view."""

    id: str
    email: str | None
    display_name: str | None
    is_guest: bool
    is_admin: bool
    created_at: datetime
    last_sync_at: datetime | None
    quiz_completed_at: datetime | None
    total_songs_known: int


class UserListResponse(BaseModel):
    """Paginated user list response."""

    users: list[UserListItem]
    total: int
    limit: int
    offset: int


class ServiceSummary(BaseModel):
    """Summary of a connected music service."""

    service_type: str
    service_username: str
    sync_status: str
    last_sync_at: datetime | None
    tracks_synced: int
    sync_error: str | None


class SyncJobSummary(BaseModel):
    """Summary of a sync job for user detail."""

    id: str
    status: str
    created_at: datetime
    completed_at: datetime | None
    error: str | None


class DataSummary(BaseModel):
    """Summary of user data counts."""

    artists_count: int
    songs_count: int
    playlists_count: int


class UserDetail(UserListItem):
    """Detailed user info for admin view."""

    services: list[ServiceSummary]
    sync_jobs: list[SyncJobSummary]
    data_summary: DataSummary


class SyncJobListItem(BaseModel):
    """Sync job item for admin list view."""

    id: str
    user_id: str
    user_email: str | None
    status: str
    created_at: datetime
    completed_at: datetime | None
    error: str | None


class SyncJobListResponse(BaseModel):
    """Paginated sync job list response."""

    jobs: list[SyncJobListItem]
    total: int
    limit: int
    offset: int


class SyncJobProgress(BaseModel):
    """Sync job progress details."""

    current_service: str | None
    current_phase: str | None
    total_tracks: int
    processed_tracks: int
    matched_tracks: int
    percentage: int


class SyncJobResultItem(BaseModel):
    """Result for a single service sync."""

    service_type: str
    tracks_fetched: int
    tracks_matched: int
    user_songs_created: int
    user_songs_updated: int
    artists_stored: int
    error: str | None


class SyncJobDetail(SyncJobListItem):
    """Detailed sync job info for admin view."""

    progress: SyncJobProgress | None
    results: list[SyncJobResultItem]
