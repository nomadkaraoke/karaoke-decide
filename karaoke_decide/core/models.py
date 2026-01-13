"""Core data models for Karaoke Decide."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# Valid values for "Enjoy Singing" metadata
SINGING_TAGS = [
    "easy_to_sing",
    "crowd_pleaser",
    "shows_range",
    "fun_lyrics",
    "nostalgic",
]

SINGING_ENERGY_OPTIONS = ["upbeat_party", "chill_ballad", "emotional_powerhouse"]
VOCAL_COMFORT_OPTIONS = ["easy", "comfortable", "challenging"]


class User(BaseModel):
    """User account."""

    id: str
    email: str | None = None  # None for guest users
    display_name: str | None = None
    is_guest: bool = False  # True for anonymous/guest users
    is_admin: bool = False  # True for admin users
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Aggregated stats (denormalized)
    total_songs_known: int = 0
    total_songs_sung: int = 0
    last_sync_at: datetime | None = None

    # Quiz data (for data-light users)
    quiz_completed_at: datetime | None = None
    quiz_songs_known: list[str] = Field(default_factory=list)
    quiz_decade_pref: str | None = None  # Legacy: single decade
    quiz_energy_pref: Literal["chill", "medium", "high"] | None = None

    # New quiz preferences (v2)
    quiz_decades: list[str] = Field(default_factory=list)  # Multi-select decades
    quiz_genres: list[str] = Field(default_factory=list)  # Selected genre IDs
    quiz_vocal_comfort_pref: Literal["easy", "challenging", "any"] | None = None
    quiz_crowd_pleaser_pref: Literal["hits", "deep_cuts", "any"] | None = None
    quiz_manual_artists: list[str] = Field(default_factory=list)  # Manually entered artists


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
    tracks_synced: int = 0  # Karaoke-matched tracks only
    songs_synced: int = 0  # Total unique songs synced (all tracks)

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


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

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class UserSong(BaseModel):
    """User's relationship to a karaoke song."""

    id: str  # user_id:song_id
    user_id: str
    song_id: str

    # Source tracking
    source: Literal["spotify", "lastfm", "quiz", "known_songs", "enjoy_singing"] = "spotify"

    # From listening history
    play_count: int = 0  # Legacy: sync count (times seen during sync)
    playcount: int | None = None  # Actual play count from Last.fm
    rank: int | None = None  # Rank in user's top list
    last_played_at: datetime | None = None
    is_saved: bool = False

    # From user tracking
    times_sung: int = 0
    last_sung_at: datetime | None = None
    average_rating: float | None = None  # 1-5
    notes: str | None = None

    # "Enjoy Singing" metadata - for songs user enjoys singing at karaoke
    enjoy_singing: bool = False  # True if user marked this as "enjoy singing"
    singing_tags: list[str] = Field(default_factory=list)
    singing_energy: Literal["upbeat_party", "chill_ballad", "emotional_powerhouse"] | None = None
    vocal_comfort: Literal["easy", "comfortable", "challenging"] | None = None

    @field_validator("singing_tags")
    @classmethod
    def validate_singing_tags(cls, v: list[str]) -> list[str]:
        """Validate that all singing tags are from the allowed set."""
        if not v:
            return v
        invalid_tags = [tag for tag in v if tag not in SINGING_TAGS]
        if invalid_tags:
            raise ValueError(f"Invalid singing tags: {invalid_tags}. Valid: {SINGING_TAGS}")
        return v

    # Denormalized for queries
    artist: str
    title: str

    # Karaoke availability (for "Create Your Own Karaoke" feature)
    has_karaoke_version: bool = True  # False for Spotify-only songs
    spotify_popularity: int | None = None  # For sorting/filtering
    duration_ms: int | None = None  # Song duration
    explicit: bool = False  # Explicit content flag

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Playlist(BaseModel):
    """User-created karaoke playlist."""

    id: str
    user_id: str
    name: str
    description: str | None = None

    song_ids: list[str] = Field(default_factory=list)
    song_count: int = 0

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SungRecord(BaseModel):
    """Record of a user singing a song."""

    id: str
    user_id: str
    song_id: str

    sung_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
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


class SuggestionReason(BaseModel):
    """Explanation for why an artist was suggested."""

    type: Literal["fans_also_like", "similar_artist", "genre_match", "decade_match", "popular_choice"]
    display_text: str  # Human-readable text, e.g., "Based on punk, rock"
    related_to: str | None = None  # For similar_artist/fans_also_like: the artist name(s)


class QuizArtist(BaseModel):
    """Artist presented in the onboarding quiz."""

    name: str  # Artist name (used as ID)
    song_count: int  # Number of karaoke songs by this artist
    top_songs: list[str]  # Top 3 song titles for preview
    total_brand_count: int  # Sum of brand counts across all songs
    primary_decade: str  # Most common decade for their songs
    genres: list[str] = Field(default_factory=list)  # Genres from Spotify
    image_url: str | None = None  # Artist image URL (from Spotify)
    suggestion_reason: SuggestionReason | None = None  # Why this artist is suggested


class QuizResponse(BaseModel):
    """User's response to the onboarding quiz."""

    user_id: str
    known_song_ids: list[str] = Field(default_factory=list)  # Legacy: Songs the user recognized
    known_artists: list[str] = Field(default_factory=list)  # Artists the user knows
    decade_preference: str | None = None  # Legacy: single decade
    decade_preferences: list[str] = Field(default_factory=list)  # Multi-select decades
    energy_preference: Literal["chill", "medium", "high"] | None = None
    submitted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # New preferences (v2)
    genres: list[str] = Field(default_factory=list)  # Selected genre IDs
    vocal_comfort_pref: Literal["easy", "challenging", "any"] | None = None
    crowd_pleaser_pref: Literal["hits", "deep_cuts", "any"] | None = None
    manual_artists: list[str] = Field(default_factory=list)  # Manually entered artists


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
        "generate_karaoke",  # For songs without karaoke version
        "similar_to_enjoyed",  # Similar audio profile to songs user enjoys singing
    ]

    # Karaoke availability
    brand_count: int = 0
    popularity: int = 0
    has_karaoke_version: bool = True  # False for generate-only songs
    is_classic: bool = False  # True if brand_count >= 20

    # Optional metadata for filtering
    duration_ms: int | None = None
    explicit: bool = False
