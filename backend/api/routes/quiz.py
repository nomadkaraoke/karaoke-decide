"""Quiz routes for data-light user onboarding.

Provides quiz songs for users without streaming data and handles
quiz submission to create personalized song recommendations.
"""

from typing import Literal

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field

from backend.api.deps import CurrentUser, KnownSongsServiceDep, QuizServiceDep
from backend.services.quiz_service import ManualArtist

router = APIRouter()


# -----------------------------------------------------------------------------
# Request/Response Models
# -----------------------------------------------------------------------------


class QuizSongResponse(BaseModel):
    """Song presented in the onboarding quiz."""

    id: str
    artist: str
    title: str
    decade: str
    popularity: int
    brand_count: int


class QuizSongsResponse(BaseModel):
    """Response containing quiz songs."""

    songs: list[QuizSongResponse]


class QuizArtistResponse(BaseModel):
    """Artist presented in the onboarding quiz."""

    name: str
    song_count: int
    top_songs: list[str]
    total_brand_count: int
    primary_decade: str
    genres: list[str] = []
    image_url: str | None = None


class QuizArtistsResponse(BaseModel):
    """Response containing quiz artists."""

    artists: list[QuizArtistResponse]


class DecadeArtist(BaseModel):
    """Example artist for a decade."""

    name: str
    top_song: str


class DecadeInfo(BaseModel):
    """Decade with example artists."""

    decade: str
    artists: list[DecadeArtist]


class DecadeArtistsResponse(BaseModel):
    """Response containing decade example artists."""

    decades: list[DecadeInfo]


class ManualArtistInput(BaseModel):
    """Artist manually entered by user via autocomplete search."""

    artist_id: str = Field(..., description="Spotify artist ID")
    artist_name: str = Field(..., description="Artist name for display")
    genres: list[str] = Field(default_factory=list, description="Artist genres")


class QuizSubmitRequest(BaseModel):
    """Request to submit quiz responses."""

    known_song_ids: list[str] = Field(
        default_factory=list,
        description="List of song IDs the user recognized (legacy)",
    )
    known_artists: list[str] = Field(
        default_factory=list,
        description="List of artist names the user knows",
    )
    decade_preference: str | None = Field(
        None,
        description="User's preferred decade - legacy single select (e.g., '1980s', '1990s')",
    )
    decade_preferences: list[str] = Field(
        default_factory=list,
        description="User's preferred decades - multi-select (e.g., ['1980s', '1990s'])",
    )
    energy_preference: Literal["chill", "medium", "high"] | None = Field(
        None,
        description="User's preferred energy level",
    )
    # New preferences (v2)
    genres: list[str] = Field(
        default_factory=list,
        description="Selected genre IDs (e.g., ['rock', 'punk', 'emo'])",
    )
    vocal_comfort_pref: Literal["easy", "challenging", "any"] | None = Field(
        None,
        description="Preferred vocal comfort level for songs",
    )
    crowd_pleaser_pref: Literal["hits", "deep_cuts", "any"] | None = Field(
        None,
        description="Prefer popular hits or niche deep cuts",
    )
    manual_artists: list[ManualArtistInput] = Field(
        default_factory=list,
        description="Artists selected by user via autocomplete (with Spotify IDs)",
    )


class QuizSubmitResponse(BaseModel):
    """Response after submitting quiz."""

    message: str
    songs_added: int
    recommendations_ready: bool


class QuizStatusResponse(BaseModel):
    """User's quiz completion status."""

    completed: bool
    completed_at: str | None
    songs_known_count: int


class EnjoySongEntry(BaseModel):
    """A song the user enjoys singing with optional metadata."""

    song_id: str = Field(..., description="Song ID - karaoke catalog ID or 'spotify:{track_id}'")
    singing_tags: list[str] = Field(
        default_factory=list,
        description="Tags describing why user enjoys singing. "
        "Valid: easy_to_sing, crowd_pleaser, shows_range, fun_lyrics, nostalgic",
    )
    singing_energy: str | None = Field(
        default=None,
        description="Energy/mood. Valid: upbeat_party, chill_ballad, emotional_powerhouse",
    )
    vocal_comfort: str | None = Field(
        default=None,
        description="Comfort level. Valid: easy, comfortable, challenging",
    )
    notes: str | None = Field(
        default=None,
        max_length=500,
        description="Optional free-form notes about the song",
    )


class QuizEnjoySingingRequest(BaseModel):
    """Request to submit songs the user enjoys singing (quiz step 4)."""

    songs: list[EnjoySongEntry] = Field(
        ...,
        min_length=1,
        max_length=50,
        description="List of songs with enjoy singing metadata",
    )


class QuizEnjoySingingResponse(BaseModel):
    """Response after submitting enjoy singing songs."""

    songs_added: int
    songs_updated: int
    songs_failed: int


class SmartArtistRequest(BaseModel):
    """Request for smart artist suggestions based on user input."""

    genres: list[str] = Field(
        default_factory=list,
        description="Selected genre IDs to filter artists",
    )
    decades: list[str] = Field(
        default_factory=list,
        description="Selected decades to filter artists",
    )
    manual_artists: list[str] = Field(
        default_factory=list,
        description="Artists manually entered by user (to find similar)",
    )
    manual_song_artists: list[str] = Field(
        default_factory=list,
        description="Artists from songs user enjoys singing",
    )
    exclude: list[str] = Field(
        default_factory=list,
        description="Artist names to exclude (already selected)",
    )
    count: int = Field(
        25,
        ge=10,
        le=50,
        description="Number of artists to return",
    )


# -----------------------------------------------------------------------------
# Get Quiz Songs
# -----------------------------------------------------------------------------


@router.get("/songs", response_model=QuizSongsResponse)
async def get_quiz_songs(
    user: CurrentUser,
    quiz_service: QuizServiceDep,
    count: int = Query(15, ge=5, le=30, description="Number of quiz songs"),
) -> QuizSongsResponse:
    """Get quiz songs for onboarding (legacy endpoint).

    Returns a selection of popular karaoke songs for the user to
    indicate which ones they know. Songs are selected to provide
    variety across different artists.

    Note: Consider using /artists instead for better UX.

    Requires authentication to track quiz completion.
    """
    songs = await quiz_service.get_quiz_songs(count)

    return QuizSongsResponse(
        songs=[
            QuizSongResponse(
                id=song.id,
                artist=song.artist,
                title=song.title,
                decade=song.decade,
                popularity=song.popularity,
                brand_count=song.brand_count,
            )
            for song in songs
        ]
    )


@router.get("/artists", response_model=QuizArtistsResponse)
async def get_quiz_artists(
    user: CurrentUser,
    quiz_service: QuizServiceDep,
    count: int = Query(25, ge=10, le=50, description="Number of quiz artists"),
    genres: list[str] = Query(default=[], description="Filter by genres (e.g., pop, rock, hiphop)"),
    exclude: list[str] = Query(default=[], description="Artist names to exclude (for pagination)"),
) -> QuizArtistsResponse:
    """Get quiz artists for onboarding.

    Returns a selection of popular karaoke artists for the user to
    indicate which ones they know. This provides a faster and richer
    onboarding experience than selecting individual songs.

    Artists are selected based on:
    - Total number of karaoke songs available
    - Combined brand coverage (popularity proxy)
    - Optional genre filter from user selections

    Use `exclude` parameter to load more artists without duplicates.

    Requires authentication to track quiz completion.
    """
    artists = await quiz_service.get_quiz_artists(
        count=count,
        genres=genres if genres else None,
        exclude_artists=exclude if exclude else None,
    )

    return QuizArtistsResponse(
        artists=[
            QuizArtistResponse(
                name=artist.name,
                song_count=artist.song_count,
                top_songs=artist.top_songs,
                total_brand_count=artist.total_brand_count,
                primary_decade=artist.primary_decade,
                genres=artist.genres,
                image_url=artist.image_url,
            )
            for artist in artists
        ]
    )


@router.post("/artists/smart", response_model=QuizArtistsResponse)
async def get_smart_quiz_artists(
    request: SmartArtistRequest,
    user: CurrentUser,
    quiz_service: QuizServiceDep,
) -> QuizArtistsResponse:
    """Get quiz artists informed by user's preferences and manual entries.

    This is an enhanced version of /artists that uses the user's selections
    from earlier quiz steps to provide more relevant artist suggestions:

    - Genres: Filter to artists in selected genres
    - Decades: Filter to artists active in selected decades
    - Manual artists: Find artists in similar genres
    - Manual song artists: Use genres from songs user enjoys singing

    The algorithm combines explicit genre selections with inferred genres
    from manually entered artists/songs to find artists the user is more
    likely to know and enjoy.

    Use this endpoint for the final "Artists You Know" step in the quiz,
    after collecting preferences in earlier steps.
    """
    artists = await quiz_service.get_smart_quiz_artists(
        genres=request.genres if request.genres else None,
        decades=request.decades if request.decades else None,
        seed_artists=request.manual_artists if request.manual_artists else None,
        seed_song_artists=request.manual_song_artists if request.manual_song_artists else None,
        exclude_artists=request.exclude if request.exclude else None,
        count=request.count,
    )

    return QuizArtistsResponse(
        artists=[
            QuizArtistResponse(
                name=artist.name,
                song_count=artist.song_count,
                top_songs=artist.top_songs,
                total_brand_count=artist.total_brand_count,
                primary_decade=artist.primary_decade,
                genres=artist.genres,
                image_url=artist.image_url,
            )
            for artist in artists
        ]
    )


@router.get("/decade-artists", response_model=DecadeArtistsResponse)
async def get_decade_artists(
    user: CurrentUser,
    quiz_service: QuizServiceDep,
    artists_per_decade: int = Query(5, ge=3, le=10, description="Artists per decade"),
) -> DecadeArtistsResponse:
    """Get example artists for each decade.

    Returns top karaoke artists organized by their primary decade,
    helping users understand what era each decade represents.

    Useful for the decade preference selection in the quiz.
    """
    decade_data = await quiz_service.get_decade_artists(artists_per_decade)

    return DecadeArtistsResponse(
        decades=[
            DecadeInfo(
                decade=decade,
                artists=[DecadeArtist(name=a["name"], top_song=a["top_song"]) for a in artists],
            )
            for decade, artists in decade_data.items()
        ]
    )


# -----------------------------------------------------------------------------
# Submit Quiz
# -----------------------------------------------------------------------------


@router.post("/submit", response_model=QuizSubmitResponse, status_code=status.HTTP_201_CREATED)
async def submit_quiz(
    request: QuizSubmitRequest,
    user: CurrentUser,
    quiz_service: QuizServiceDep,
) -> QuizSubmitResponse:
    """Submit quiz responses.

    Creates UserSong records for artists/songs the user knows and
    stores their preferences (decades, genres, energy, vocal comfort,
    crowd pleaser preference). This data is used to generate
    personalized song recommendations.

    You can submit either:
    - known_artists: List of artist names (recommended, richer data)
    - known_song_ids: List of song IDs (legacy support)
    - manual_artists: Artists entered by user (used for genre inference)

    A user can submit the quiz multiple times - each submission
    will add new songs and update preferences.
    """
    # Convert Pydantic models to dataclasses for service layer
    manual_artists_data = (
        [
            ManualArtist(
                artist_id=a.artist_id,
                artist_name=a.artist_name,
                genres=a.genres,
            )
            for a in request.manual_artists
        ]
        if request.manual_artists
        else None
    )

    result = await quiz_service.submit_quiz(
        user_id=user.id,
        known_song_ids=request.known_song_ids,
        known_artists=request.known_artists,
        decade_preference=request.decade_preference,
        decade_preferences=request.decade_preferences,
        energy_preference=request.energy_preference,
        genres=request.genres,
        vocal_comfort_pref=request.vocal_comfort_pref,
        crowd_pleaser_pref=request.crowd_pleaser_pref,
        manual_artists=manual_artists_data,
    )

    return QuizSubmitResponse(
        message="Quiz completed successfully",
        songs_added=result.songs_added,
        recommendations_ready=result.recommendations_ready,
    )


# -----------------------------------------------------------------------------
# Get Quiz Status
# -----------------------------------------------------------------------------


@router.get("/status", response_model=QuizStatusResponse)
async def get_quiz_status(
    user: CurrentUser,
    quiz_service: QuizServiceDep,
) -> QuizStatusResponse:
    """Get user's quiz completion status.

    Returns whether the user has completed the quiz and how many
    songs they indicated knowing. This can be used to prompt users
    who haven't completed the quiz yet.
    """
    status = await quiz_service.get_quiz_status(user.id)

    return QuizStatusResponse(
        completed=status.completed,
        completed_at=status.completed_at.isoformat() if status.completed_at else None,
        songs_known_count=status.songs_known_count,
    )


# -----------------------------------------------------------------------------
# Submit Enjoy Singing Songs (Quiz Step 4)
# -----------------------------------------------------------------------------


@router.post("/enjoy-singing", response_model=QuizEnjoySingingResponse, status_code=status.HTTP_201_CREATED)
async def submit_enjoy_singing(
    request: QuizEnjoySingingRequest,
    user: CurrentUser,
    known_songs_service: KnownSongsServiceDep,
) -> QuizEnjoySingingResponse:
    """Submit songs the user enjoys singing (quiz step 4).

    This is an optional step in the quiz flow where users can add
    specific songs they already know they enjoy singing at karaoke,
    along with metadata about why they enjoy them.

    Each song can include:
    - singing_tags: Why they enjoy it (easy_to_sing, crowd_pleaser, etc.)
    - singing_energy: The mood/energy (upbeat_party, chill_ballad, etc.)
    - vocal_comfort: Comfort level (easy, comfortable, challenging)
    - notes: Free-form notes

    All metadata fields are optional. Users can add songs with minimal
    information and fill in details later.

    Songs are added to the user's library with `source="enjoy_singing"` if new,
    or updated with `enjoy_singing=True` if already in library.
    """
    songs_added = 0
    songs_updated = 0
    songs_failed = 0

    for song_entry in request.songs:
        try:
            result = await known_songs_service.set_enjoy_singing(
                user_id=user.id,
                song_id=song_entry.song_id,
                singing_tags=song_entry.singing_tags,
                singing_energy=song_entry.singing_energy,
                vocal_comfort=song_entry.vocal_comfort,
                notes=song_entry.notes,
            )
            if result.created_new:
                songs_added += 1
            else:
                songs_updated += 1
        except ValueError:
            songs_failed += 1

    return QuizEnjoySingingResponse(
        songs_added=songs_added,
        songs_updated=songs_updated,
        songs_failed=songs_failed,
    )
