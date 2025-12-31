"""Models for async sync job tracking."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


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
        )
