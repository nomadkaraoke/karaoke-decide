"""Fetch queue for the voice pool: Firestore ``voice_pool_fetch/{pool_id}``.

The selection job (``source='shared'``), Andrew's labelled sheet (``'andrew_sheet'``) and the
personal pool (``'personal'``) add songs; the fetch job takes them in ``(priority, added_at)``
order and advances ``status`` through ``STATUSES``. ``fs`` is the async ``FirestoreService``
(or anything with the same ``create_document`` / ``query_documents``).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from karaoke_decide.voice_pool.models import pool_id

__all__ = ["FETCH_COLLECTION", "PRIORITY", "STATUSES", "enqueue", "next_queued", "pool_id", "queue_doc"]

FETCH_COLLECTION = "voice_pool_fetch"
STATUSES = ("queued", "downloading", "fetched", "separating", "separated", "analysed", "failed")
PRIORITY = {"andrew_sheet": 0, "shared": 1, "personal": 2}


def queue_doc(item: dict[str, Any], now: datetime) -> dict[str, Any]:
    """The new queue doc for ``item`` (see ``enqueue``); ``batch_ids`` from the item (personal batches)."""
    return {
        "song_key": item["song_key"],
        "artist": item["artist"],
        "title": item["title"],
        "spotify_id": item.get("spotify_id"),
        "duration_ms": item.get("duration_ms"),
        "source": item["source"],
        "priority": PRIORITY[item["source"]],
        "batch_ids": list(item.get("batch_ids") or []),
        "status": "queued",
        "attempts": 0,
        "flacfetch_id": None,
        "audio_gcs": None,
        "vocals_gcs": None,
        "manifest_run": None,
        "error": None,
        "added_at": now,
        "updated_at": now,
        "stratum": item.get("stratum"),
        # The selection's oEmbed verdicts ({video_id: bool}; unknown ones omitted) for voice.db.
        "versions_embeddable": dict(item.get("versions_embeddable") or {}),
    }


async def enqueue(fs: Any, items: Iterable[dict[str, Any]]) -> int:
    """Queue songs for fetch; a doc that already exists is left alone. Returns the number added.

    Each item: ``song_key``, ``artist``, ``title``, ``source`` and optionally ``spotify_id``,
    ``duration_ms``, ``stratum``, ``versions_embeddable``, ``batch_ids``. Create-if-absent, so concurrent
    enqueues of one song add it once.
    """
    added = 0
    for item in items:
        if await fs.create_document(FETCH_COLLECTION, pool_id(item["song_key"]), queue_doc(item, datetime.now(UTC))):
            added += 1
    return added


async def next_queued(fs: Any, limit: int) -> list[dict[str, Any]]:
    """Up to ``limit`` queued docs (with ``id``), by ``(priority, added_at)``.

    Equality filter only, sorted here, so no composite index is needed.
    """
    docs: list[dict[str, Any]] = await fs.query_documents(FETCH_COLLECTION, filters=[("status", "==", "queued")])
    docs.sort(key=lambda d: (d.get("priority", PRIORITY["personal"]), d["added_at"]))
    return docs[:limit]
