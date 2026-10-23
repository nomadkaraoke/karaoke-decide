"""Models for async sync job tracking."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

# An in-progress job with no heartbeat for this long is stalled (its instance died or hung).
# BigQuery matching can take 3+ minutes for large track sets, so 5 minutes is reasonable.
STALE_JOB_THRESHOLD_SECONDS = 300

# A PENDING job is waiting for a Cloud Tasks delivery. A failed run is requeued as PENDING and the
# queue retries it after up to max_backoff (600 s, infrastructure/__main__.py), so it only counts
# as lost (stalled) once it has waited longer than that.
STALE_PENDING_THRESHOLD_SECONDS = 900

# A job that fails (or is resumed after stalling) this many times is marked failed for good.
MAX_SYNC_RESUMES = 5


class RunSupersededError(Exception):
    """Another delivery claimed this sync job; the current run must stop writing.

    Raised by the progress callback. Per-service sync wrappers must re-raise it ahead of
    their broad ``except Exception`` handlers so a superseded run stops at once and leaves
    per-service status to the run that now owns the job.
    """


class SyncJobStatus(str, Enum):
    """Sync job status values."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class SyncJobProgress:
    """Progress tracking for a sync job."""

    current_service: str | None = None
    current_phase: str | None = None  # "fetching", "matching", "storing"
    total_tracks: int = 0
    processed_tracks: int = 0
    matched_tracks: int = 0

    @property
    def percentage(self) -> int:
        """Calculate progress percentage."""
        if self.total_tracks == 0:
            return 0
        return int((self.processed_tracks / self.total_tracks) * 100)


@dataclass
class SyncJobResult:
    """Result for a single service sync."""

    service_type: str
    tracks_fetched: int = 0
    tracks_matched: int = 0
    user_songs_created: int = 0
    user_songs_updated: int = 0
    artists_stored: int = 0
    error: str | None = None


@dataclass
class SyncJob:
    """Async sync job state stored in Firestore."""

    id: str
    user_id: str
    status: SyncJobStatus = SyncJobStatus.PENDING
    progress: SyncJobProgress = field(default_factory=SyncJobProgress)
    results: list[SyncJobResult] = field(default_factory=list)
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    resume_count: int = 0
    run_id: str | None = None  # token of the run that owns the job; only that run may write results

    def is_stalled(self, now: datetime | None = None) -> bool:
        """True if the job should be running (running, or queued) but nothing has touched it for too long.

        A pending job past STALE_PENDING_THRESHOLD_SECONDS has lost its Cloud Task (or exhausted
        the queue's retries); before that a scheduled retry may still be on its way.
        """
        if self.status == SyncJobStatus.IN_PROGRESS:
            threshold = STALE_JOB_THRESHOLD_SECONDS
        elif self.status == SyncJobStatus.PENDING:
            threshold = STALE_PENDING_THRESHOLD_SECONDS
        else:
            return False
        age = ((now or datetime.now(UTC)) - self.updated_at).total_seconds()
        return age > threshold

    def to_dict(self) -> dict[str, Any]:
        """Convert to Firestore document dict."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "status": self.status.value,
            "progress": {
                "current_service": self.progress.current_service,
                "current_phase": self.progress.current_phase,
                "total_tracks": self.progress.total_tracks,
                "processed_tracks": self.progress.processed_tracks,
                "matched_tracks": self.progress.matched_tracks,
                "percentage": self.progress.percentage,
            },
            "results": [
                {
                    "service_type": r.service_type,
                    "tracks_fetched": r.tracks_fetched,
                    "tracks_matched": r.tracks_matched,
                    "user_songs_created": r.user_songs_created,
                    "user_songs_updated": r.user_songs_updated,
                    "artists_stored": r.artists_stored,
                    "error": r.error,
                }
                for r in self.results
            ],
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "resume_count": self.resume_count,
            "run_id": self.run_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SyncJob":
        """Create from Firestore document dict."""
        progress_data = data.get("progress", {})
        progress = SyncJobProgress(
            current_service=progress_data.get("current_service"),
            current_phase=progress_data.get("current_phase"),
            total_tracks=progress_data.get("total_tracks", 0),
            processed_tracks=progress_data.get("processed_tracks", 0),
            matched_tracks=progress_data.get("matched_tracks", 0),
        )

        results = [
            SyncJobResult(
                service_type=r.get("service_type", ""),
                tracks_fetched=r.get("tracks_fetched", 0),
                tracks_matched=r.get("tracks_matched", 0),
                user_songs_created=r.get("user_songs_created", 0),
                user_songs_updated=r.get("user_songs_updated", 0),
                artists_stored=r.get("artists_stored", 0),
                error=r.get("error"),
            )
            for r in data.get("results", [])
        ]

        return cls(
            id=data.get("id", ""),
            user_id=data.get("user_id", ""),
            status=SyncJobStatus(data.get("status", "pending")),
            progress=progress,
            results=results,
            error=data.get("error"),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(UTC),
            updated_at=datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else datetime.now(UTC),
            completed_at=datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None,
            resume_count=data.get("resume_count", 0),
            run_id=data.get("run_id"),
        )
