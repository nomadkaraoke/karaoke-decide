"""Admin routes for the voice session: import Andrew's sheet labels.

Access: an admin decide user or the ``X-Admin-Token`` header (scripts/voice/import_sheet.py).
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.api.deps import AdminAccess, AuthServiceDep, FirestoreServiceDep
from backend.services.catalog_store import CatalogUnavailable, get_catalog_store
from karaoke_decide.catalog.reader import CatalogReader
from karaoke_decide.song_search.text_normalize import song_key
from karaoke_decide.voice_pool.queue import enqueue
from karaoke_decide.voice_session.labels import parse_good, parse_note

logger = logging.getLogger(__name__)

router = APIRouter()

LABELS_COLLECTION = "voice_labels"
MAX_ROWS = 500


class SheetRowIn(BaseModel):
    artist: str
    title: str
    duet: bool | None = None
    good: str | None = None
    sung: bool | None = None
    notes: str = ""


class ImportLabelsRequest(BaseModel):
    email: str
    rows: list[SheetRowIn] = Field(max_length=MAX_ROWS)
    enqueue: bool = True


class UnmatchedRow(BaseModel):
    artist: str
    title: str


class ImportLabelsResponse(BaseModel):
    imported: int
    matched: int
    unmatched: list[UnmatchedRow]
    enqueued: int


async def get_voice_catalog() -> CatalogReader:
    """The startup-loaded catalog.db reader (tests override this)."""
    try:
        return get_catalog_store().require()
    except CatalogUnavailable as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="warming_up") from e


def label_doc_id(user_id: str, key: str) -> str:
    return f"{user_id}:{hashlib.sha1(key.encode()).hexdigest()[:24]}"


def _in_catalog(reader: CatalogReader, key: str) -> bool:
    return reader.conn().execute("SELECT 1 FROM songs WHERE song_key = ?", (key,)).fetchone() is not None


def _has_community_version(reader: CatalogReader, key: str) -> bool:
    if not reader.has_availability:
        return False
    row = (
        reader.conn()
        .execute(
            "SELECT 1 FROM av.versions WHERE song_key = ? AND kind = 'community' AND youtube_url IS NOT NULL LIMIT 1",
            (key,),
        )
        .fetchone()
    )
    return row is not None


@router.post("/labels/import", response_model=ImportLabelsResponse)
async def import_labels(
    body: ImportLabelsRequest,
    _access: AdminAccess,
    firestore: FirestoreServiceDep,
    auth: AuthServiceDep,
    catalog: Annotated[CatalogReader, Depends(get_voice_catalog)],
) -> ImportLabelsResponse:
    """Store sheet rows as ``voice_labels`` (idempotent per user + song) and queue matched songs."""
    user = await auth.resolve_gen_user(body.email)
    now = datetime.now(UTC)
    unmatched: list[UnmatchedRow] = []
    to_queue: list[dict[str, Any]] = []
    matched = 0
    for row in body.rows:
        key = song_key(row.artist, row.title)
        is_match = _in_catalog(catalog, key)
        await firestore.set_document(
            LABELS_COLLECTION,
            label_doc_id(user.id, key),
            {
                "user_id": user.id,
                "song_key": key,
                "artist": row.artist,
                "title": row.title,
                "duet": row.duet,
                "good": parse_good(row.good),
                "sung": row.sung,
                "notes": row.notes,
                "tags": sorted(parse_note(row.notes)),
                "matched": is_match,
                "source": "sheet",
                "imported_at": now,
            },
        )
        if not is_match:
            unmatched.append(UnmatchedRow(artist=row.artist, title=row.title))
            continue
        matched += 1
        if body.enqueue and _has_community_version(catalog, key):
            to_queue.append({"song_key": key, "artist": row.artist, "title": row.title, "source": "andrew_sheet"})
    enqueued = await enqueue(firestore, to_queue) if to_queue else 0
    return ImportLabelsResponse(imported=len(body.rows), matched=matched, unmatched=unmatched, enqueued=enqueued)
