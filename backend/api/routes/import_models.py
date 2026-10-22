"""Response model shared by every import route."""

from __future__ import annotations

from pydantic import BaseModel

from backend.services.import_ingest_service import IngestResult


class ImportResultResponse(BaseModel):
    """Outcome of one import, shared by every import source."""

    source: str
    artists_added: int
    songs_added: int
    songs_updated: int
    tracks_matched: int

    @classmethod
    def from_result(cls, source: str, r: IngestResult) -> ImportResultResponse:
        return cls(
            source=source,
            artists_added=r.artists_added,
            songs_added=r.songs_added,
            songs_updated=r.songs_updated,
            tracks_matched=r.tracks_matched,
        )
