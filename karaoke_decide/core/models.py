"""Core data models for Karaoke Decide."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class User(BaseModel):
    """User account."""

    id: str
    email: str
    display_name: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Aggregated stats (denormalized)
    total_songs_known: int = 0
    total_songs_sung: int = 0
    last_sync_at: datetime | None = None

    # Quiz data (for data-light users)
    quiz_completed_at: datetime | None = None
    quiz_songs_known: list[str] = Field(default_factory=list)
    quiz_decade_pref: str | None = None
    quiz_energy_pref: Literal["chill", "medium", "high"] | None = None


class MusicService(BaseModel):
    """Connected music service account."""

    id: str
    user_id: str
    service_type: Literal["spotify", "lastfm", "apple_music"]
    service_user_id: str
    service_username: str

    # OAuth tokens (should be encrypted in production)
    access_token: str | None = None
    refresh_token: str | None = None
    token_expires_at: datetime | None = None

    # Sync state
    last_sync_at: datetime | None = None
    sync_status: Literal["idle", "syncing", "error"] = "idle"
    sync_error: str | None = None
    tracks_synced: int = 0

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class SongSource(BaseModel):
    """Source information for a karaoke song."""

    source: Literal["karaokenerds", "openkj", "karafun"]
    external_id: str
    url: str | None = None


class KaraokeSong(BaseModel):
    """Karaoke song from the catalog."""

    id: str  # Normalized: slugify(artist-title)
    artist: str
    title: str

    # Source tracking
    sources: list[SongSource] = Field(default_factory=list)

    # Optional metadata
    duration_ms: int | None = None
    genres: list[str] = Field(default_factory=list)
    popularity: int | None = None  # 0-100

    # Flags
    is_popular_karaoke: bool = False

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class UserSong(BaseModel):
    """User's relationship to a karaoke song."""

    id: str  # user_id:song_id
    user_id: str
    song_id: str

    # Source tracking
    source: Literal["spotify", "lastfm", "quiz"] = "spotify"

    # From listening history
    play_count: int = 0
    last_played_at: datetime | None = None
    is_saved: bool = False

    # From user tracking
    times_sung: int = 0
    last_sung_at: datetime | None = None
    average_rating: float | None = None  # 1-5
    notes: str | None = None

    # Denormalized for queries
    artist: str
    title: str

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Playlist(BaseModel):
    """User-created karaoke playlist."""

    id: str
    user_id: str
    name: str
    description: str | None = None

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
    rating: int | None = None  # 1-5
    notes: str | None = None

    # Optional context
    venue: str | None = None
    playlist_id: str | None = None


class QuizSong(BaseModel):
    """Song presented in the onboarding quiz."""

    id: str  # Same as KaraokeSong id
    artist: str
    title: str
    decade: str  # "1970s", "1980s", etc.
    popularity: int  # 0-100 from Spotify
    brand_count: int  # Number of karaoke brands


class QuizResponse(BaseModel):
    """User's response to the onboarding quiz."""

    user_id: str
    known_song_ids: list[str]  # Songs the user recognized
    decade_preference: str | None = None  # Preferred decade
    energy_preference: Literal["chill", "medium", "high"] | None = None
    submitted_at: datetime = Field(default_factory=datetime.utcnow)


class Recommendation(BaseModel):
    """A song recommendation for a user."""

    song_id: str
    artist: str
    title: str
    score: float  # 0-1 relevance score
    reason: str  # Human-readable explanation
    reason_type: Literal[
        "known_artist",
        "similar_genre",
        "decade_match",
        "crowd_pleaser",
        "popular",
    ]

    # Optional metadata
    brand_count: int = 0
    popularity: int = 0


class UserSongSource(BaseModel):
    """Source information for a UserSong record."""

    source: Literal["spotify", "lastfm", "quiz"]
    play_count: int = 0
    last_played_at: datetime | None = None
