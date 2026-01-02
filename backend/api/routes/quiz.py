"""Quiz routes for data-light user onboarding.

Provides quiz songs for users without streaming data and handles
quiz submission to create personalized song recommendations.
"""

from typing import Literal

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field

from backend.api.deps import CurrentUser, QuizServiceDep

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
    image_url: str | None = None


class QuizArtistsResponse(BaseModel):
    """Response containing quiz artists."""

    artists: list[QuizArtistResponse]


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
        description="User's preferred decade (e.g., '1980s', '1990s')",
    )
    energy_preference: Literal["chill", "medium", "high"] | None = Field(
        None,
        description="User's preferred energy level",
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
) -> QuizArtistsResponse:
    """Get quiz artists for onboarding.

    Returns a selection of popular karaoke artists for the user to
    indicate which ones they know. This provides a faster and richer
    onboarding experience than selecting individual songs.

    Artists are selected based on:
    - Total number of karaoke songs available
    - Combined brand coverage (popularity proxy)

    Requires authentication to track quiz completion.
    """
    artists = await quiz_service.get_quiz_artists(count)

    return QuizArtistsResponse(
        artists=[
            QuizArtistResponse(
                name=artist.name,
                song_count=artist.song_count,
                top_songs=artist.top_songs,
                total_brand_count=artist.total_brand_count,
                primary_decade=artist.primary_decade,
                image_url=artist.image_url,
            )
            for artist in artists
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
    stores their preferences (decade, energy). This data is used
    to generate personalized song recommendations.

    You can submit either:
    - known_artists: List of artist names (recommended, richer data)
    - known_song_ids: List of song IDs (legacy support)

    A user can submit the quiz multiple times - each submission
    will add new songs and update preferences.
    """
    result = await quiz_service.submit_quiz(
        user_id=user.id,
        known_song_ids=request.known_song_ids,
        known_artists=request.known_artists,
        decade_preference=request.decade_preference,
        energy_preference=request.energy_preference,
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
