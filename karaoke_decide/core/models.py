"""Core data models for Karaoke Decide."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class User(BaseModel):
    """User account."""

    id: str
    email: str
    display_name: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Aggregated stats (denormalized)
    total_songs_known: int = 0
    total_songs_sung: int = 0
    last_sync_at: Optional[datetime] = None


class MusicService(BaseModel):
    """Connected music service account."""

    id: str
    user_id: str
    service_type: Literal["spotify", "lastfm", "apple_music"]
    service_user_id: str
    service_username: str

    # OAuth tokens (should be encrypted in production)
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    token_expires_at: Optional[datetime] = None

    # Sync state
    last_sync_at: Optional[datetime] = None
    sync_status: Literal["idle", "syncing", "error"] = "idle"
    sync_error: Optional[str] = None
    tracks_synced: int = 0

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class SongSource(BaseModel):
    """Source information for a karaoke song."""

    source: Literal["karaokenerds", "openkj", "karafun"]
    external_id: str
    url: Optional[str] = None


class KaraokeSong(BaseModel):
    """Karaoke song from the catalog."""

    id: str  # Normalized: slugify(artist-title)
    artist: str
    title: str

    # Source tracking
    sources: list[SongSource] = Field(default_factory=list)

    # Optional metadata
    duration_ms: Optional[int] = None
    genres: list[str] = Field(default_factory=list)
    popularity: Optional[int] = None  # 0-100

    # Flags
    is_popular_karaoke: bool = False

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class UserSong(BaseModel):
    """User's relationship to a karaoke song."""

    id: str  # user_id:song_id
    user_id: str
    song_id: str

    # From listening history
    play_count: int = 0
    last_played_at: Optional[datetime] = None
    is_saved: bool = False

    # From user tracking
    times_sung: int = 0
    last_sung_at: Optional[datetime] = None
    average_rating: Optional[float] = None  # 1-5
    notes: Optional[str] = None

    # Denormalized for queries
    artist: str
    title: str

    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Playlist(BaseModel):
    """User-created karaoke playlist."""

    id: str
    user_id: str
    name: str
    description: Optional[str] = None

    song_ids: list[str] = Field(default_factory=list)
    song_count: int = 0

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class SungRecord(BaseModel):
    """Record of a user singing a song."""

    id: str
    user_id: str
    song_id: str

    sung_at: datetime = Field(default_factory=datetime.utcnow)
    rating: Optional[int] = None  # 1-5
    notes: Optional[str] = None

    # Optional context
    venue: Optional[str] = None
    playlist_id: Optional[str] = None
