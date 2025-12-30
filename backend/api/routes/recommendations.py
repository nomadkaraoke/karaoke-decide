"""Recommendation routes for personalized song discovery.

Provides endpoints for user's song library and AI-generated
recommendations based on listening history and quiz responses.
"""

from fastapi import APIRouter, Query
from pydantic import BaseModel

from backend.api.deps import CurrentUser, RecommendationServiceDep

router = APIRouter()


# -----------------------------------------------------------------------------
# Request/Response Models
# -----------------------------------------------------------------------------


class UserSongResponse(BaseModel):
    """Song from user's library."""

    id: str
    song_id: str
    artist: str
    title: str
    source: str
    play_count: int
    is_saved: bool
    times_sung: int


class UserSongsResponse(BaseModel):
    """Response containing user's songs."""

    songs: list[UserSongResponse]
    total: int
    page: int
    per_page: int
    has_more: bool


class RecommendationResponse(BaseModel):
    """A song recommendation."""

    song_id: str
    artist: str
    title: str
    score: float
    reason: str
    reason_type: str
    brand_count: int
    popularity: int


class RecommendationsResponse(BaseModel):
    """Response containing recommendations."""

    recommendations: list[RecommendationResponse]


# -----------------------------------------------------------------------------
# User's Songs (Library)
# -----------------------------------------------------------------------------


@router.get("/songs", response_model=UserSongsResponse)
async def get_my_songs(
    user: CurrentUser,
    recommendation_service: RecommendationServiceDep,
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Results per page"),
) -> UserSongsResponse:
    """Get user's songs from listening history.

    Returns songs the user has synced from Spotify/Last.fm or
    indicated knowing in the quiz. Sorted by play count.
    """
    offset = (page - 1) * per_page
    songs, total = await recommendation_service.get_user_songs(
        user_id=user.id,
        limit=per_page,
        offset=offset,
    )

    return UserSongsResponse(
        songs=[
            UserSongResponse(
                id=song.id,
                song_id=song.song_id,
                artist=song.artist,
                title=song.title,
                source=song.source,
                play_count=song.play_count,
                is_saved=song.is_saved,
                times_sung=song.times_sung,
            )
            for song in songs
        ],
        total=total,
        page=page,
        per_page=per_page,
        has_more=(page * per_page) < total,
    )


# -----------------------------------------------------------------------------
# Personalized Recommendations
# -----------------------------------------------------------------------------


@router.get("/recommendations", response_model=RecommendationsResponse)
async def get_recommendations(
    user: CurrentUser,
    recommendation_service: RecommendationServiceDep,
    limit: int = Query(20, ge=1, le=50, description="Number of recommendations"),
    decade: str | None = Query(None, description="Filter by decade (e.g., '1980s')"),
    min_popularity: int | None = Query(None, ge=0, le=100, description="Minimum Spotify popularity"),
) -> RecommendationsResponse:
    """Get personalized song recommendations.

    Returns karaoke songs the user might enjoy based on:
    - Artists they listen to
    - Songs from their quiz responses
    - Their stated preferences (decade, energy)
    - Karaoke popularity (brand coverage)

    New users with no history get crowd-pleaser recommendations.
    """
    recommendations = await recommendation_service.get_recommendations(
        user_id=user.id,
        limit=limit,
        decade=decade,
        min_popularity=min_popularity,
    )

    return RecommendationsResponse(
        recommendations=[
            RecommendationResponse(
                song_id=rec.song_id,
                artist=rec.artist,
                title=rec.title,
                score=rec.score,
                reason=rec.reason,
                reason_type=rec.reason_type,
                brand_count=rec.brand_count,
                popularity=rec.popularity,
            )
            for rec in recommendations
        ]
    )
