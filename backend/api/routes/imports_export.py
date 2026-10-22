"""Spotify / Apple privacy-export aggregate ingestion (included in the ``/api/services`` router).

The phone parses the export zip and posts aggregates in chunks of up to 20,000 tracks. A chunk is
ingested with ``play_mode="max"`` so re-posting a chunk, or importing an Account export and then an
Extended one from the same account, never doubles play counts. On top of that each chunk is recorded
once per export (``user_imports/{user_id}:{source}.exports["{kind}:{generated_at}"]``) so a retried
chunk is a no-op that returns the stored totals.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, model_validator

from backend.api.deps import ImportIngestServiceDep, VerifiedUser
from backend.api.routes.import_models import ImportResultResponse
from backend.services.import_ingest_service import (
    ImportIngestService,
    ImportSource,
    IngestArtist,
    IngestTrack,
)
from karaoke_decide.song_search.text_normalize import identity_normalize

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_TRACKS = 20000
MAX_PLAYLIST_TRACKS = 10000
MAX_PLAYLISTS = 500
MAX_CHUNKS = 50
MAX_PLAYLIST_REFS = 20000  # across all playlists of one request
MAX_EXPORT_ARTISTS = 2000
EXPORT_ARTISTS_COLLECTION = "user_import_artists"
_TOTAL_FIELDS = ("artists_added", "songs_added", "songs_updated", "tracks_matched")


MAX_TS = 4_102_444_800  # 2100-01-01 UTC, Unix seconds (rejects millisecond timestamps)


class ExportTrack(BaseModel):
    artist: str = Field(min_length=1, max_length=300)
    title: str = Field(min_length=1, max_length=300)
    ms_played: int = Field(ge=0)
    plays: int = Field(ge=1)
    first_ts: int = Field(ge=0, le=MAX_TS)
    last_ts: int = Field(ge=0, le=MAX_TS)


class ExportRef(BaseModel):
    artist: str = Field(min_length=1, max_length=300)
    title: str = Field(min_length=1, max_length=300)


class ExportPlaylist(BaseModel):
    name: str = Field(max_length=200)
    tracks: list[ExportRef] = Field(max_length=MAX_PLAYLIST_TRACKS)


class AppleExportRequest(BaseModel):
    generated_at: date
    chunk_index: int = Field(ge=0)
    chunk_count: int = Field(ge=1, le=MAX_CHUNKS)
    tracks: list[ExportTrack] = Field(max_length=MAX_TRACKS)


class SpotifyExportRequest(AppleExportRequest):
    kind: Literal["account", "extended"]
    liked: list[ExportRef] = Field(max_length=MAX_TRACKS)
    playlists: list[ExportPlaylist] = Field(max_length=MAX_PLAYLISTS)

    @model_validator(mode="after")
    def _cap_playlist_refs(self) -> SpotifyExportRequest:
        if sum(len(p.tracks) for p in self.playlists) > MAX_PLAYLIST_REFS:
            raise ValueError(f"at most {MAX_PLAYLIST_REFS} playlist tracks per request")
        return self


class ExportImportResponse(ImportResultResponse):
    duplicate: bool
    chunks_done: int
    chunk_count: int


def _ingest_tracks(body: AppleExportRequest) -> list[IngestTrack]:
    out = [IngestTrack(t.artist, t.title, plays=t.plays, ms_played=t.ms_played, last_ts=t.last_ts) for t in body.tracks]
    refs: list[ExportRef] = []
    if isinstance(body, SpotifyExportRequest):
        refs.extend(body.liked)
        for p in body.playlists:
            refs.extend(p.tracks)
    # Liked / playlist membership carries no play count; one entry per distinct song is enough.
    seen: set[tuple[str, str]] = set()
    for r in refs:
        key = (identity_normalize(r.artist), identity_normalize(r.title))
        if key not in seen:
            seen.add(key)
            out.append(IngestTrack(r.artist, r.title, plays=None))
    return out


def _chunk_artist_plays(tracks: list[IngestTrack]) -> dict[str, tuple[str, int]]:
    """``{identity_normalize(artist): (display name, plays)}`` for one chunk (liked-only artists: 0)."""
    out: dict[str, tuple[str, int]] = {}
    for t in tracks:
        key = identity_normalize(t.artist)
        if key:
            name, plays = out.get(key, (t.artist.strip(), 0))
            out[key] = (name, plays + (t.plays or 0))
    return out


async def _cumulative_artists(
    ingest: ImportIngestService,
    user_id: str,
    source: ImportSource,
    export_key: str,
    chunk_index: int,
    chunk: dict[str, tuple[str, int]],
) -> list[IngestArtist]:
    """Add this chunk's per-artist plays to the export's running sums (once per chunk: a retried chunk
    adds nothing) and return the cumulative top artists, ranked by plays."""
    doc_id = f"{user_id}:{source}:{export_key}"
    final: dict[str, Any] = {}

    def add(cur: dict[str, Any] | None) -> dict[str, Any]:
        doc = dict(cur or {})
        done = list(doc.get("chunks_added", []))
        artists = {k: dict(v) for k, v in (doc.get("artists") or {}).items()}
        if chunk_index not in done:
            for key, (name, plays) in chunk.items():
                entry = artists.setdefault(key, {"n": name, "p": 0})
                entry["p"] = int(entry["p"]) + plays
            top = sorted(artists, key=lambda k: (-artists[k]["p"], k))[:MAX_EXPORT_ARTISTS]
            artists = {k: artists[k] for k in top}
            done = sorted([*done, chunk_index])
        doc.update(user_id=user_id, source=source, chunks_added=done, artists=artists)
        final.clear()
        final.update(artists)
        return doc

    await ingest.db.transform_document_atomically(EXPORT_ARTISTS_COLLECTION, doc_id, add)
    ranked = sorted(final, key=lambda k: (-final[k]["p"], k))
    return [IngestArtist(name=final[k]["n"], plays=final[k]["p"] or None, rank=i) for i, k in enumerate(ranked, 1)]


async def _ingest_chunk(
    ingest: ImportIngestService,
    user_id: str,
    source: ImportSource,
    export_key: str,
    body: AppleExportRequest,
) -> ExportImportResponse:
    doc_id = f"{user_id}:{source}"

    def response(entry: dict[str, Any], duplicate: bool) -> ExportImportResponse:
        return ExportImportResponse(
            source=source,
            **{f: int(entry.get(f, 0)) for f in _TOTAL_FIELDS},
            duplicate=duplicate,
            chunks_done=len(entry["chunks_done"]),
            chunk_count=int(entry["chunk_count"]),
        )

    def check(entry: dict[str, Any] | None) -> dict[str, Any] | None:
        if entry is not None and int(entry["chunk_count"]) != body.chunk_count:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_chunk")
        return entry

    existing = await ingest.get_import(user_id, source)
    entry = check(((existing or {}).get("exports") or {}).get(export_key))
    if entry is not None and body.chunk_index in entry["chunks_done"]:
        return response(entry, True)

    # Ingest first, record after: a failed ingest leaves the chunk unrecorded so the phone's retry
    # re-sends it. Two racing posts of one chunk both ingest, which "max" mode makes harmless.
    tracks = _ingest_tracks(body)
    artists = await _cumulative_artists(
        ingest, user_id, source, export_key, body.chunk_index, _chunk_artist_plays(tracks)
    )
    result = await ingest.ingest(user_id, source, tracks, artists=artists, play_mode="max")
    outcome: dict[str, Any] = {}

    def record(cur: dict[str, Any] | None) -> dict[str, Any]:
        doc = dict(cur or {})
        exports = dict(doc.get("exports") or {})
        prev = check(exports.get(export_key))
        done = list((prev or {}).get("chunks_done", []))
        if body.chunk_index in done:
            outcome.update(entry=prev, duplicate=True)
            return doc
        totals = {f: int((prev or {}).get(f, 0)) + getattr(result, f) for f in _TOTAL_FIELDS}
        new_entry = {"chunk_count": body.chunk_count, "chunks_done": sorted([*done, body.chunk_index]), **totals}
        exports[export_key] = new_entry
        outcome.update(entry=new_entry, duplicate=False)
        # Overall status and the summary figures ``record_import`` writes, kept with the chunk bookkeeping.
        complete = len(new_entry["chunks_done"]) >= int(new_entry["chunk_count"])
        doc.update(
            user_id=user_id,
            source=source,
            status="ok" if complete else "importing",
            last_import_at=datetime.now(UTC).isoformat(),
            artists=sum(int(e.get("artists_added", 0)) for e in exports.values()),
            songs=sum(int(e.get("songs_added", 0)) + int(e.get("songs_updated", 0)) for e in exports.values()),
            exports=exports,
        )
        return doc

    await ingest.db.transform_document_atomically(ingest.IMPORTS_COLLECTION, doc_id, record)
    return response(outcome["entry"], outcome["duplicate"])


@router.post("/spotify-export", response_model=ExportImportResponse)
async def spotify_export(
    body: SpotifyExportRequest, user: VerifiedUser, ingest: ImportIngestServiceDep
) -> ExportImportResponse:
    """One chunk of a Spotify Account data / Extended streaming history export (aggregates).

    Errors: 400 ``bad_chunk`` (index >= count, or count differs from the first chunk), 422 validation.
    """
    if body.chunk_index >= body.chunk_count:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_chunk")
    return await _ingest_chunk(ingest, user.id, "spotify_export", f"{body.kind}:{body.generated_at}", body)


@router.post("/apple-export", response_model=ExportImportResponse)
async def apple_export(
    body: AppleExportRequest, user: VerifiedUser, ingest: ImportIngestServiceDep
) -> ExportImportResponse:
    """One chunk of an Apple privacy-export play history (aggregates). Errors as ``spotify-export``."""
    if body.chunk_index >= body.chunk_count:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_chunk")
    return await _ingest_chunk(ingest, user.id, "apple_export", f"apple:{body.generated_at}", body)
