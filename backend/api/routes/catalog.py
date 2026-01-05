"""Catalog routes for browsing karaoke songs."""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.services.karaoke_link_service import (
    KaraokeLinkService,
    get_karaoke_link_service,
)
from karaoke_decide.services.bigquery_catalog import BigQueryCatalogService

router = APIRouter()

# Lazy initialization for testability
_catalog_service: BigQueryCatalogService | None = None
_karaoke_link_service: KaraokeLinkService | None = None


def get_catalog_service() -> BigQueryCatalogService:
    """Get or create catalog service (lazy initialization)."""
    global _catalog_service
    if _catalog_service is None:
        _catalog_service = BigQueryCatalogService()
    return _catalog_service


def _get_karaoke_link_service() -> KaraokeLinkService:
    """Get or create karaoke link service (lazy initialization)."""
    global _karaoke_link_service
    if _karaoke_link_service is None:
        _karaoke_link_service = get_karaoke_link_service()
    return _karaoke_link_service


class SongResponse(BaseModel):
    """Karaoke song in API response."""

    id: int
    artist: str
    title: str
    brands: list[str]
    brand_count: int
    is_popular: bool = False


class CatalogSearchResponse(BaseModel):
    """Paginated catalog search response."""

    songs: list[SongResponse]
    total: int
    page: int
    per_page: int
    has_more: bool


class CatalogStatsResponse(BaseModel):
    """Catalog statistics."""

    total_songs: int
    unique_artists: int
    max_brand_count: int
    avg_brand_count: float


class KaraokeLinkResponse(BaseModel):
    """A karaoke link for a song."""

    type: str
    url: str
    label: str
    description: str


class SongLinksResponse(BaseModel):
    """Response containing karaoke links for a song."""

    song_id: int
    artist: str
    title: str
    links: list[KaraokeLinkResponse]


class ArtistSearchResult(BaseModel):
    """Artist search result for autocomplete."""

    artist_id: str
    artist_name: str
    popularity: int
    genres: list[str] = []


class ArtistSearchResponse(BaseModel):
    """Response containing artist search results."""

    artists: list[ArtistSearchResult]
    total: int


class TrackSearchResult(BaseModel):
    """Track search result for autocomplete."""

    track_id: str
    track_name: str
    artist_name: str
    artist_id: str
    popularity: int
    duration_ms: int
    explicit: bool


class TrackSearchResponse(BaseModel):
    """Response containing track search results."""

    tracks: list[TrackSearchResult]
    total: int


@router.get("/songs", response_model=CatalogSearchResponse)
async def search_catalog(
    q: str | None = Query(None, description="Search query (artist or title)"),
    artist: str | None = Query(None, description="Filter by artist"),
    min_brands: int = Query(0, ge=0, description="Minimum brand count"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Results per page"),
) -> CatalogSearchResponse:
    """Search and browse the karaoke catalog.

    Songs can be filtered by:
    - q: Search term matching artist or title
    - artist: Exact artist match
    - min_brands: Minimum number of karaoke brands (popularity filter)
    """
    offset = (page - 1) * per_page

    service = get_catalog_service()
    if artist:
        results = service.get_songs_by_artist(artist, limit=per_page)
    elif q:
        results = service.search_songs(
            query=q,
            limit=per_page + 1,  # Get one extra to check has_more
            offset=offset,
            min_brands=min_brands,
        )
    else:
        # Default: popular songs
        results = service.get_popular_songs(
            limit=per_page + 1,
            min_brands=max(min_brands, 3),  # At least 3 brands for popular
        )

    has_more = len(results) > per_page
    songs = results[:per_page]

    return CatalogSearchResponse(
        songs=[
            SongResponse(
                id=s.id,
                artist=s.artist,
                title=s.title,
                brands=s.brands.split(",") if s.brands else [],
                brand_count=s.brand_count,
                is_popular=s.brand_count >= 5,
            )
            for s in songs
        ],
        total=len(songs),  # Would need separate count query for exact total
        page=page,
        per_page=per_page,
        has_more=has_more,
    )


@router.get("/songs/popular", response_model=list[SongResponse])
async def get_popular_songs(
    limit: int = Query(50, ge=1, le=200, description="Number of songs"),
    min_brands: int = Query(5, ge=1, description="Minimum brand count"),
) -> list[SongResponse]:
    """Get the most popular karaoke songs by brand coverage."""
    results = get_catalog_service().get_popular_songs(limit=limit, min_brands=min_brands)
    return [
        SongResponse(
            id=s.id,
            artist=s.artist,
            title=s.title,
            brands=s.brands.split(",") if s.brands else [],
            brand_count=s.brand_count,
            is_popular=True,
        )
        for s in results
    ]


@router.get("/songs/{song_id}", response_model=SongResponse)
async def get_song(song_id: int) -> SongResponse:
    """Get details for a specific song."""
    result = get_catalog_service().get_song_by_id(song_id)
    if not result:
        raise HTTPException(status_code=404, detail="Song not found")

    return SongResponse(
        id=result.id,
        artist=result.artist,
        title=result.title,
        brands=result.brands.split(",") if result.brands else [],
        brand_count=result.brand_count,
        is_popular=result.brand_count >= 5,
    )


@router.get("/songs/{song_id}/links", response_model=SongLinksResponse)
async def get_song_links(song_id: int) -> SongLinksResponse:
    """Get karaoke links for a specific song.

    Returns available links to watch or create karaoke videos:
    - YouTube search for existing karaoke videos
    - Nomad Karaoke Generator for creating custom videos
    """
    # First get the song to verify it exists and get artist/title
    song = get_catalog_service().get_song_by_id(song_id)
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")

    # Get karaoke links
    link_service = _get_karaoke_link_service()
    links = link_service.get_links(song.artist, song.title)

    return SongLinksResponse(
        song_id=song.id,
        artist=song.artist,
        title=song.title,
        links=[
            KaraokeLinkResponse(
                type=link.type.value,
                url=link.url,
                label=link.label,
                description=link.description,
            )
            for link in links
        ],
    )


@router.get("/stats", response_model=CatalogStatsResponse)
async def get_catalog_stats() -> CatalogStatsResponse:
    """Get catalog statistics."""
    stats = get_catalog_service().get_stats()
    return CatalogStatsResponse(**stats)


@router.get("/artists", response_model=ArtistSearchResponse)
async def search_artists(
    q: str = Query(..., min_length=2, description="Search query (min 2 characters)"),
    limit: int = Query(10, ge=1, le=50, description="Maximum results"),
) -> ArtistSearchResponse:
    """Search artists for autocomplete.

    Returns artists matching the query prefix, sorted by popularity.
    Uses the Spotify artist catalog for comprehensive artist data.

    This endpoint is public (no auth required) for quick autocomplete.
    """
    results = get_catalog_service().search_artists(q, limit=limit)
    return ArtistSearchResponse(
        artists=[
            ArtistSearchResult(
                artist_id=r.artist_id,
                artist_name=r.artist_name,
                popularity=r.popularity,
                genres=r.genres,
            )
            for r in results
        ],
        total=len(results),
    )


@router.get("/tracks", response_model=TrackSearchResponse)
async def search_tracks(
    q: str = Query(..., min_length=2, description="Search query (min 2 characters)"),
    limit: int = Query(10, ge=1, le=50, description="Maximum results"),
) -> TrackSearchResponse:
    """Search tracks for autocomplete.

    Returns tracks matching the query prefix (title or artist), sorted by popularity.
    Uses the Spotify track catalog for comprehensive track data.

    This endpoint is public (no auth required) for quick autocomplete.
    """
    results = get_catalog_service().search_tracks(q, limit=limit)
    return TrackSearchResponse(
        tracks=[
            TrackSearchResult(
                track_id=r.track_id,
                track_name=r.track_name,
                artist_name=r.artist_name,
                artist_id=r.artist_id,
                popularity=r.popularity,
                duration_ms=r.duration_ms,
                explicit=r.explicit,
            )
            for r in results
        ],
        total=len(results),
    )
