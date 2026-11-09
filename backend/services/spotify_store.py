"""Permanent store for every live Spotify search (Andrew, 2026-11-08: "make the spotify cache
indefinite with no deletion"; never waste data the API returns).

Two places, nothing is ever deleted:

- **Firestore ``spotify_search_cache/{sha256(normalised query)}``**: the query cache. It holds
  the track views the route returns plus Spotify's **raw items, verbatim**. A query seen before
  is answered from here forever, with no Spotify call. The one exception is a query that found
  nothing: it is checked again after ``EMPTY_RECHECK_S``, because a song can be released after
  someone first looked for it. The doc is updated in place, not deleted.
- **BigQuery ``karaoke_decide.spotify_live_tracks``**: append-only, one row per track per fetch,
  with the raw item JSON. gen's kn-data-sync adds these tracks to the kjbox song-identification
  index, so the on-device matcher knows every song we have ever looked up. The table is created
  on first write (the service account has project-wide ``bigquery.dataEditor``).

Writes are best-effort: a store failure never fails a search. The Firestore write is awaited
before the response (about 50 ms; Cloud Run may throttle CPU after a response, so a background
write could stall). The BigQuery log runs in the background, and the Firestore doc holds the same
raw items in the meantime.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import UTC, datetime
from typing import Any, Protocol

logger = logging.getLogger(__name__)

CACHE_COLLECTION = "spotify_search_cache"
BQ_TABLE = "spotify_live_tracks"
EMPTY_RECHECK_S = 24 * 3600

BQ_SCHEMA = [
    ("seen_at", "TIMESTAMP", "REQUIRED"),
    ("query", "STRING", "NULLABLE"),
    ("track_id", "STRING", "REQUIRED"),
    ("track_name", "STRING", "NULLABLE"),
    ("artist_name", "STRING", "NULLABLE"),
    ("artist_names", "STRING", "REPEATED"),
    ("artist_ids", "STRING", "REPEATED"),
    ("album_id", "STRING", "NULLABLE"),
    ("album_name", "STRING", "NULLABLE"),
    ("album_type", "STRING", "NULLABLE"),
    ("release_date", "STRING", "NULLABLE"),
    ("release_year", "INTEGER", "NULLABLE"),
    ("duration_ms", "INTEGER", "NULLABLE"),
    ("explicit", "BOOLEAN", "NULLABLE"),
    ("isrc", "STRING", "NULLABLE"),
    ("raw", "STRING", "NULLABLE"),  # the whole item as Spotify sent it
]


def query_key(normalised_query: str) -> str:
    return hashlib.sha256(normalised_query.encode()).hexdigest()


def bq_rows(query: str, items: list[dict[str, Any]], seen_at: datetime) -> list[dict[str, Any]]:
    """One row per raw track item (items without an id are skipped)."""
    rows = []
    for t in items:
        if not isinstance(t, dict) or not t.get("id"):
            continue
        album = t.get("album") or {}
        artists = [a for a in t.get("artists") or [] if isinstance(a, dict)]
        release = album.get("release_date") or ""
        rows.append(
            {
                "seen_at": seen_at.isoformat(),
                "query": query,
                "track_id": t["id"],
                "track_name": t.get("name"),
                "artist_name": artists[0].get("name") if artists else None,
                "artist_names": [a["name"] for a in artists if a.get("name")],
                "artist_ids": [a["id"] for a in artists if a.get("id")],
                "album_id": album.get("id"),
                "album_name": album.get("name"),
                "album_type": album.get("album_type"),
                "release_date": release or None,
                "release_year": int(release[:4]) if release[:4].isdigit() else None,
                "duration_ms": t.get("duration_ms"),
                "explicit": t.get("explicit"),
                "isrc": (t.get("external_ids") or {}).get("isrc"),
                "raw": json.dumps(t, separators=(",", ":"), ensure_ascii=False),
            }
        )
    return rows


class SearchStore(Protocol):
    async def get(self, query: str) -> list[dict[str, Any]] | None: ...

    async def save(self, query: str, tracks: list[dict[str, Any]], items: list[dict[str, Any]]) -> None: ...


class SpotifyStore:
    """Firestore query cache + BigQuery sighting log. ``fs`` is decide's ``FirestoreService``."""

    def __init__(self, fs: Any, project: str, *, bq_client: Any = None, clock: Any = time.time) -> None:
        self._fs = fs
        self._project = project
        self._bq = bq_client
        self._clock = clock
        self._table_ready = False
        self._tasks: set[asyncio.Task[None]] = set()

    async def get(self, query: str) -> list[dict[str, Any]] | None:
        """Cached track views for this normalised query, or None (never fetched, an empty result
        due a recheck, or Firestore unavailable)."""
        try:
            doc = await self._fs.get_document(CACHE_COLLECTION, query_key(query))
        except Exception:
            logger.warning("spotify-store: cache read failed", exc_info=True)
            return None
        if not doc or not isinstance(doc.get("tracks"), list):
            return None
        tracks = [t for t in doc["tracks"] if isinstance(t, dict)]
        if not tracks and self._clock() - float(doc.get("fetched_at_epoch") or 0) >= EMPTY_RECHECK_S:
            return None
        return tracks

    async def save(self, query: str, tracks: list[dict[str, Any]], items: list[dict[str, Any]]) -> None:
        """Persist a fresh fetch: the Firestore cache now, the BigQuery log in the background."""
        now = datetime.now(UTC)
        task = asyncio.ensure_future(self._save_bq(query, items, now))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        await self._save_cache(query, tracks, items, now)

    async def _save_cache(
        self, query: str, tracks: list[dict[str, Any]], items: list[dict[str, Any]], now: datetime
    ) -> None:
        from google.cloud import firestore

        try:
            await self._fs.set_document(
                CACHE_COLLECTION,
                query_key(query),
                {
                    "query": query,
                    "tracks": tracks,
                    "raw_items": items,
                    "fetched_at": now,
                    "fetched_at_epoch": self._clock(),
                    "fetch_count": firestore.Increment(1),
                },
                merge=True,
            )
        except Exception:
            logger.warning("spotify-store: cache write failed", exc_info=True)

    async def _save_bq(self, query: str, items: list[dict[str, Any]], now: datetime) -> None:
        rows = bq_rows(query, items, now)
        if not rows:
            return
        try:
            await asyncio.to_thread(self._insert, rows)
        except Exception:
            logger.warning("spotify-store: BigQuery log write failed (%d rows)", len(rows), exc_info=True)

    def _client(self) -> Any:
        if self._bq is None:
            from google.cloud import bigquery

            self._bq = bigquery.Client(project=self._project)
        return self._bq

    def _ensure_table(self) -> str:
        from google.cloud import bigquery

        table_id = f"{self._project}.karaoke_decide.{BQ_TABLE}"
        if not self._table_ready:
            table = bigquery.Table(table_id, schema=[bigquery.SchemaField(n, t, mode=m) for n, t, m in BQ_SCHEMA])
            table.time_partitioning = bigquery.TimePartitioning(field="seen_at")
            self._client().create_table(table, exists_ok=True)
            self._table_ready = True
        return table_id

    def _insert(self, rows: list[dict[str, Any]]) -> None:
        errors = self._client().insert_rows_json(self._ensure_table(), rows)
        if errors:
            raise RuntimeError(f"insert_rows_json errors: {errors[:3]}")
