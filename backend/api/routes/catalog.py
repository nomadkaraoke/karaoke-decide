"""Catalog routes for browsing karaoke songs."""

from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel

from karaoke_decide.services.bigquery_catalog import BigQueryCatalogService

router = APIRouter()

# Initialize service (could use dependency injection in production)
catalog_service = BigQueryCatalogService()


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

    if artist:
        results = catalog_service.get_songs_by_artist(artist, limit=per_page)
    elif q:
        results = catalog_service.search_songs(
            query=q,
            limit=per_page + 1,  # Get one extra to check has_more
            offset=offset,
            min_brands=min_brands,
        )
    else:
        # Default: popular songs
        results = catalog_service.get_popular_songs(
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
    results = catalog_service.get_popular_songs(limit=limit, min_brands=min_brands)
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
    result = catalog_service.get_song_by_id(song_id)
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


@router.get("/stats", response_model=CatalogStatsResponse)
async def get_catalog_stats() -> CatalogStatsResponse:
    """Get catalog statistics."""
    stats = catalog_service.get_stats()
    return CatalogStatsResponse(**stats)
