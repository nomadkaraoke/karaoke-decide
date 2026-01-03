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
    play_count: int  # Legacy: sync count
    playcount: int | None = None  # Actual play count from Last.fm
    rank: int | None = None  # Rank in user's top list
    spotify_popularity: int | None = None  # Spotify popularity (0-100)
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
    has_karaoke_version: bool = True
    is_classic: bool = False
    duration_ms: int | None = None
    explicit: bool = False


class RecommendationsResponse(BaseModel):
    """Response containing recommendations."""

    recommendations: list[RecommendationResponse]


class CategorizedRecommendationsResponse(BaseModel):
    """Response containing categorized recommendations with rich filters."""

    from_artists_you_know: list[RecommendationResponse]
    create_your_own: list[RecommendationResponse]
    crowd_pleasers: list[RecommendationResponse]
    total_count: int
    filters_applied: dict[str, str | int | bool | None]


class UserArtistResponse(BaseModel):
    """Artist from user's listening history."""

    id: str
    artist_name: str
    source: str
    rank: int
    time_range: str
    popularity: int | None = None
    genres: list[str] = []


class UserArtistsResponse(BaseModel):
    """Response containing user's artists."""

    artists: list[UserArtistResponse]
    total: int
    sources: dict[str, int]  # Count by source (spotify, lastfm)


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
                playcount=song.playcount,
                rank=song.rank,
                spotify_popularity=song.spotify_popularity,
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
                has_karaoke_version=rec.has_karaoke_version,
                is_classic=rec.is_classic,
                duration_ms=rec.duration_ms,
                explicit=rec.explicit,
            )
            for rec in recommendations
        ]
    )


@router.get("/recommendations/categorized", response_model=CategorizedRecommendationsResponse)
async def get_categorized_recommendations(
    user: CurrentUser,
    recommendation_service: RecommendationServiceDep,
    has_karaoke: bool | None = Query(
        None, description="Filter by karaoke availability (true=karaoke ready, false=generate only, null=all)"
    ),
    min_popularity: int | None = Query(None, ge=0, le=100, description="Minimum Spotify popularity (0-100)"),
    max_popularity: int | None = Query(None, ge=0, le=100, description="Maximum Spotify popularity (for hidden gems)"),
    exclude_explicit: bool = Query(False, description="Hide explicit content"),
    min_duration_ms: int | None = Query(None, ge=0, description="Minimum song duration in milliseconds"),
    max_duration_ms: int | None = Query(None, ge=0, description="Maximum song duration in milliseconds"),
    classics_only: bool = Query(False, description="Only show all-time classics (brand_count >= 20)"),
) -> CategorizedRecommendationsResponse:
    """Get categorized song recommendations with rich filters.

    Returns recommendations organized into three sections:
    - **From Artists You Know**: Karaoke songs by artists in your library (max 3 per artist)
    - **Create Your Own Karaoke**: Songs from your library without karaoke versions
    - **Crowd Pleasers**: Popular karaoke songs for discovery

    Filters can be applied across all sections:
    - **has_karaoke**: Show only karaoke-ready or generate-only songs
    - **popularity**: Filter by Spotify popularity (hidden gems vs chart toppers)
    - **explicit**: Hide explicit content
    - **duration**: Filter by song length
    - **classics_only**: Only show universally known karaoke songs (20+ brands)
    """
    categorized = await recommendation_service.get_categorized_recommendations(
        user_id=user.id,
        has_karaoke=has_karaoke,
        min_popularity=min_popularity,
        max_popularity=max_popularity,
        exclude_explicit=exclude_explicit,
        min_duration_ms=min_duration_ms,
        max_duration_ms=max_duration_ms,
        classics_only=classics_only,
    )

    def convert_recs(recs: list) -> list[RecommendationResponse]:
        return [
            RecommendationResponse(
                song_id=rec.song_id,
                artist=rec.artist,
                title=rec.title,
                score=rec.score,
                reason=rec.reason,
                reason_type=rec.reason_type,
                brand_count=rec.brand_count,
                popularity=rec.popularity,
                has_karaoke_version=rec.has_karaoke_version,
                is_classic=rec.is_classic,
                duration_ms=rec.duration_ms,
                explicit=rec.explicit,
            )
            for rec in recs
        ]

    return CategorizedRecommendationsResponse(
        from_artists_you_know=convert_recs(categorized.from_artists_you_know),
        create_your_own=convert_recs(categorized.create_your_own),
        crowd_pleasers=convert_recs(categorized.crowd_pleasers),
        total_count=categorized.total_count,
        filters_applied=categorized.filters_applied,
    )


# -----------------------------------------------------------------------------
# User's Artists
# -----------------------------------------------------------------------------


@router.get("/artists", response_model=UserArtistsResponse)
async def get_my_artists(
    user: CurrentUser,
    recommendation_service: RecommendationServiceDep,
    source: str | None = Query(None, description="Filter by source (spotify, lastfm)"),
    time_range: str | None = Query(None, description="Filter by time range"),
    limit: int = Query(100, ge=1, le=500, description="Max artists to return"),
) -> UserArtistsResponse:
    """Get user's top artists from listening history.

    Returns artists synced from Spotify and/or Last.fm, grouped by
    time range (short_term, medium_term, long_term for Spotify;
    7day, 1month, 3month, 6month, 12month, overall for Last.fm).
    """
    artists, sources = await recommendation_service.get_user_artists(
        user_id=user.id,
        source=source,
        time_range=time_range,
        limit=limit,
    )

    return UserArtistsResponse(
        artists=[
            UserArtistResponse(
                id=artist["id"],
                artist_name=artist["artist_name"],
                source=artist["source"],
                rank=artist.get("rank", 0),
                time_range=artist.get("time_range", ""),
                popularity=artist.get("popularity"),
                genres=artist.get("genres", []),
            )
            for artist in artists
        ],
        total=len(artists),
        sources=sources,
    )
