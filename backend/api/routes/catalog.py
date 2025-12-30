"""Catalog routes for browsing karaoke songs."""

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter()


class SongResponse(BaseModel):
    """Karaoke song in API response."""

    id: str
    artist: str
    title: str
    sources: list[str]
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
    total_artists: int
    last_updated: str | None = None


@router.get("/songs", response_model=CatalogSearchResponse)
async def search_catalog(
    q: str | None = Query(None, description="Search query (artist or title)"),
    artist: str | None = Query(None, description="Filter by artist"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(50, ge=1, le=100, description="Results per page"),
) -> CatalogSearchResponse:
    """Search and browse the karaoke catalog."""
    # TODO: Implement catalog search
    return CatalogSearchResponse(
        songs=[],
        total=0,
        page=page,
        per_page=per_page,
        has_more=False,
    )


@router.get("/songs/{song_id}", response_model=SongResponse)
async def get_song(song_id: str) -> SongResponse:
    """Get details for a specific song."""
    # TODO: Implement song lookup
    return SongResponse(
        id=song_id,
        artist="Unknown",
        title="Unknown",
        sources=[],
    )


@router.get("/stats", response_model=CatalogStatsResponse)
async def get_catalog_stats() -> CatalogStatsResponse:
    """Get catalog statistics."""
    # TODO: Implement stats
    return CatalogStatsResponse(
        total_songs=0,
        total_artists=0,
    )
