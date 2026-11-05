"""A heavy user's ``user_songs`` without reading 50k documents per request.

A 50k-song user's full read takes ~16 s on a 1 vCPU instance and mapping its rows to catalog keys
~7 s more, and recs, get-made and the sources summary each need the result at app launch. So for a
user with ``HEAVY_ROWS`` or more rows, the projected rows and their derived catalog keys are kept as
a gzip JSON blob (1.4 MB for 50k rows: 2 document reads instead of 50k), split into shards:

- ``user_songs_snapshots/{user_id}``: ``snapshot_id``, ``shards``, ``rev``, ``dirty_ids``, ``stale``,
  ``catalog_run``, ``built_at``, ``derived_at``, ``row_count``.
- ``user_songs_snapshot_shards/{user_id}:{snapshot_id}:{i}``: ``user_id``, ``data`` (bytes).

**Exactness.** Every ``user_songs`` write through ``FirestoreService`` adds to the meta doc before it
returns (``rev`` + the doc ids in ``dirty_ids``; ``stale`` for a bulk write), so any instance or job
sees it. A read costs one meta read; a new ``rev`` re-reads just the dirty rows. So readers see their
own writes, unlike the 60 s row cache this replaces for these users.

**Upkeep** runs as a Cloud Task, so it has CPU (the service throttles CPU outside requests); readers
keep the current snapshot meanwhile. Too many dirty ids or an old derivation rewrite the shards with
no row reads; ``stale`` or a ``MAX_AGE_S``-old snapshot re-reads every row. The first read of a heavy
user with no snapshot builds it in the request.

Users under ``HEAVY_ROWS`` have no meta doc and keep the normal read. Flows that need rows, not the
summary (voice sessions and the voice pool), read rows directly as before.
"""

from __future__ import annotations

import asyncio
import gzip
import inspect
import json
import logging
import time
import uuid
from collections import Counter, OrderedDict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from backend.services.firestore_service import (
    USER_SONGS_SNAPSHOT_META as META,
)
from backend.services.firestore_service import (
    FirestoreService,
    TransactionView,
)
from backend.services.user_songs_cache import COLLECTION, HEAVY_ROWS, USER_SONGS, USER_SONGS_FIELDS, SingleFlight
from karaoke_decide.catalog.reader import CatalogReader

logger = logging.getLogger(__name__)

SHARDS = "user_songs_snapshot_shards"
SHARD_BYTES = 900_000  # a document holds at most 1 MiB
DIRTY_COMPACT_AT = 100
MAX_AGE_S = 7 * 86400.0  # re-read every row at least this often: bounds any write the marker missed
DERIVE_MAX_AGE_S = 86400.0  # availability.db is rebuilt daily: has_karaoke goes stale at that pace
REFRESH_LEASE_S = 600.0
SMALL_TTL_S = 60.0  # how long this instance remembers a user is under HEAVY_ROWS
MAX_SMALL_REMEMBERED = 10_000
MAX_VIEW_ROWS = 120_000  # summaries kept per instance, by the rows behind them

# Row fields derived from the catalog and normalisation: the slow part of reading a row.
DERIVED_FIELDS = ("_k", "_hk", "_a", "_n")

Scheduler = Callable[[str], Awaitable[None] | None]


@dataclass
class SongsView:
    """What the launch endpoints need from a heavy user's rows. Shared between requests: read-only."""

    row_count: int
    song_map: Any  # recs_v2.context._SongMap
    names: dict[str, str]
    known_songs: int
    source_counts: Counter[str]
    own_rows: Any  # get_made_service.OwnRows


def derive_rows(reader: CatalogReader, rows: list[dict[str, Any]]) -> None:
    """Set each row's derived fields (in place). Synchronous SQLite: run off the event loop."""
    from backend.services.recs_v2.context import row_key, row_kn_keys
    from karaoke_decide.catalog.keys import artist_key
    from karaoke_decide.song_search.text_normalize import song_key

    for d in rows:
        for f in DERIVED_FIELDS:
            d.pop(f, None)
    kn_keys = row_kn_keys(reader, rows)
    for d in rows:
        keyed = row_key(reader, d, kn_keys)
        artist, title = d.get("artist") or "", d.get("title") or ""
        d["_k"], d["_hk"] = keyed if keyed is not None else (None, False)
        if artist:
            d["_a"] = artist_key(artist)
            if title:
                d["_n"] = song_key(artist, title)


def summarize(reader: CatalogReader, rows: list[dict[str, Any]]) -> SongsView:
    """Everything the endpoints read, from derived rows. Synchronous: run off the event loop."""
    from backend.services.get_made_service import own_rows
    from backend.services.music_service_service import known_song_count
    from backend.services.recs_v2.context import _artist_names, _map_songs

    return SongsView(
        row_count=len(rows),
        song_map=_map_songs(reader, rows),
        names=_artist_names(rows),
        known_songs=known_song_count(rows),
        source_counts=Counter(str(d["source"]) for d in rows if d.get("source")),
        own_rows=own_rows(reader, rows),
    )


def encode_shards(rows: list[dict[str, Any]]) -> list[bytes]:
    raw = gzip.compress(json.dumps(rows, separators=(",", ":"), default=str).encode(), 6)
    return [raw[i : i + SHARD_BYTES] for i in range(0, len(raw), SHARD_BYTES)] or [b""]


def decode_shards(shards: Sequence[bytes]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = json.loads(gzip.decompress(b"".join(shards)))
    return rows


def _project(doc_id: str, doc: dict[str, Any]) -> dict[str, Any]:
    return {"id": doc_id, **{f: doc[f] for f in USER_SONGS_FIELDS if f in doc}}


def _shard_id(user_id: str, snapshot_id: str, i: int) -> str:
    return f"{user_id}:{snapshot_id}:{i}"


async def _schedule_cloud_task(user_id: str) -> None:
    from backend.services.cloud_tasks_service import get_cloud_tasks_service

    tasks = get_cloud_tasks_service()
    if tasks.settings.inline_tasks:  # posts from this event loop (local stack)
        tasks.create_snapshot_refresh_task(user_id)
    else:  # a blocking gRPC call
        await asyncio.to_thread(tasks.create_snapshot_refresh_task, user_id)


class ShardsGone(RuntimeError):  # noqa: N818
    """A shard the meta doc named is gone: another instance replaced the snapshot mid-read."""


class UserSongsSnapshots:
    def __init__(self, clock: Callable[[], float] = time.time, schedule: Scheduler = _schedule_cloud_task) -> None:
        self._clock = clock
        self._schedule = schedule
        self._views: OrderedDict[str, tuple[tuple[str, int, str], SongsView]] = OrderedDict()
        self._small_until: dict[str, float] = {}
        self._flights: SingleFlight[SongsView | None] = SingleFlight()

    async def view(self, fs: Any, reader: CatalogReader, catalog_run: str | None, user_id: str) -> SongsView | None:
        """The user's summary, or None when they aren't heavy (read rows the normal way).

        A failure degrades to None as well: the normal read still answers, only slower."""
        if not isinstance(fs, FirestoreService):  # the marker lives in FirestoreService
            return None
        try:
            return await self._view(fs, reader, catalog_run or "", user_id)
        except Exception:
            logger.warning("user_songs snapshot failed for %s; reading rows", user_id, exc_info=True)
            return None

    async def _view(self, fs: FirestoreService, reader: CatalogReader, run: str, user_id: str) -> SongsView | None:
        if self._small_until.get(user_id, 0.0) > self._clock():
            return None
        meta = await fs.get_document(META, user_id)
        if meta is None or not meta.get("snapshot_id"):
            count = await fs.count_documents(COLLECTION, filters=[("user_id", "==", user_id)])
            if count < HEAVY_ROWS:
                if meta is not None:  # an abandoned first build, or the user shrank meanwhile
                    await self._forget(fs, user_id, meta)
                self._remember_small(user_id)
                return None
            return await self._flights.run((user_id, "build"), lambda: self._first_build(fs, reader, run, user_id))
        await self._maybe_schedule(fs, user_id, meta, run)
        key = (str(meta["snapshot_id"]), int(meta.get("rev") or 0), str(meta.get("catalog_run") or ""))
        held = self._views.get(user_id)
        if held is not None and held[0] == key:
            self._views.move_to_end(user_id)
            return held[1]
        return await self._flights.run((user_id, key), lambda: self._load(fs, reader, user_id, meta, key))

    def _remember_small(self, user_id: str) -> None:
        if len(self._small_until) >= MAX_SMALL_REMEMBERED:
            self._small_until.clear()  # only saves a count query or two: no need for anything finer
        self._small_until[user_id] = self._clock() + SMALL_TTL_S

    async def _load(
        self,
        fs: FirestoreService,
        reader: CatalogReader,
        user_id: str,
        meta: dict[str, Any],
        key: tuple[str, int, str],
    ) -> SongsView:
        rows = await self._current_rows(fs, reader, user_id, meta)
        view = await asyncio.to_thread(summarize, reader, rows)
        self._hold(user_id, key, view)
        return view

    async def _current_rows(
        self, fs: FirestoreService, reader: CatalogReader, user_id: str, meta: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """The snapshot's rows with the rows written since applied."""
        rows = await self._read_shards(fs, user_id, meta)
        dirty = [str(i) for i in meta.get("dirty_ids") or []]
        if not dirty:
            return rows
        found = await fs.get_many(COLLECTION, dirty)
        changed = [_project(i, doc) for i, doc in found.items()]
        await asyncio.to_thread(derive_rows, reader, changed)
        gone = set(dirty)
        kept = [d for d in rows if d["id"] not in gone]
        return [*kept, *changed]

    async def _read_shards(self, fs: FirestoreService, user_id: str, meta: dict[str, Any]) -> list[dict[str, Any]]:
        snapshot_id, n = str(meta["snapshot_id"]), int(meta.get("shards") or 0)
        ids = [_shard_id(user_id, snapshot_id, i) for i in range(n)]
        docs = await fs.get_many(SHARDS, ids)
        if len(docs) != n:
            raise ShardsGone(f"{user_id}: {len(docs)} of {n} shards of {snapshot_id}")
        return await asyncio.to_thread(decode_shards, [docs[i]["data"] for i in ids])

    def _hold(self, user_id: str, key: tuple[str, int, str], view: SongsView) -> None:
        self._views[user_id] = (key, view)
        self._views.move_to_end(user_id)
        while len(self._views) > 1 and sum(v.row_count for _, v in self._views.values()) > MAX_VIEW_ROWS:
            self._views.popitem(last=False)

    # ------------------------------------------------------------------ upkeep

    def _needs_refresh(self, meta: dict[str, Any], run: str) -> bool:
        now = self._clock()
        return (
            bool(meta.get("stale"))
            or len(meta.get("dirty_ids") or []) > DIRTY_COMPACT_AT
            or str(meta.get("catalog_run") or "") != run
            or now - float(meta.get("built_at") or 0) > MAX_AGE_S
            or now - float(meta.get("derived_at") or 0) > DERIVE_MAX_AGE_S
        )

    async def _maybe_schedule(self, fs: FirestoreService, user_id: str, meta: dict[str, Any], run: str) -> None:
        """Ask for a refresh at most once per ``REFRESH_LEASE_S`` across instances. Never fails the read."""
        if not self._needs_refresh(meta, run):
            return
        if self._clock() - float(meta.get("refresh_requested_at") or 0) < REFRESH_LEASE_S:
            return
        try:
            await fs.update_document(META, user_id, {"refresh_requested_at": self._clock()})
            scheduled = self._schedule(user_id)
            if inspect.isawaitable(scheduled):
                await scheduled
        except Exception:
            logger.warning("user_songs snapshot refresh not scheduled for %s", user_id, exc_info=True)

    async def refresh(self, fs: FirestoreService, reader: CatalogReader, catalog_run: str | None, user_id: str) -> str:
        """The Cloud Task. Idempotent; a read that's due nothing leaves things as they are."""
        run = catalog_run or ""
        meta = await fs.get_document(META, user_id)
        if meta is None:
            return "no_snapshot"
        if not meta.get("snapshot_id"):
            await self._build(fs, reader, run, user_id, meta)
            return "built"
        if not self._needs_refresh(meta, run):
            return "fresh"
        now = self._clock()
        if meta.get("stale") or now - float(meta.get("built_at") or 0) > MAX_AGE_S:
            await self._build(fs, reader, run, user_id, meta)
            return "rebuilt"
        rows = await self._current_rows(fs, reader, user_id, meta)
        derived_at = float(meta.get("derived_at") or 0)
        if str(meta.get("catalog_run") or "") != run or now - derived_at > DERIVE_MAX_AGE_S:
            await asyncio.to_thread(derive_rows, reader, rows)
            derived_at = now
        await self._publish(fs, user_id, meta, rows, run, float(meta.get("built_at") or now), derived_at)
        return "compacted"

    async def _first_build(
        self, fs: FirestoreService, reader: CatalogReader, run: str, user_id: str
    ) -> SongsView | None:
        # Before the read: a write that lands during it must find the marker to report to.
        base: dict[str, Any] = {"user_id": user_id, "rev": 0, "dirty_ids": [], "stale": False}
        await fs.create_document(META, user_id, base)
        meta = await fs.get_document(META, user_id) or base
        if meta.get("snapshot_id"):  # another instance finished first
            return await self._view(fs, reader, run, user_id)
        return await self._build(fs, reader, run, user_id, meta)

    async def _build(
        self, fs: FirestoreService, reader: CatalogReader, run: str, user_id: str, meta: dict[str, Any]
    ) -> SongsView | None:
        rows = await USER_SONGS.rows(fs, user_id, fresh=True)
        if len(rows) < HEAVY_ROWS:
            await self._forget(fs, user_id, meta)
            self._remember_small(user_id)
            return None
        await asyncio.to_thread(derive_rows, reader, rows)
        now = self._clock()
        await self._publish(fs, user_id, meta, rows, run, now, now)
        return await asyncio.to_thread(summarize, reader, rows)

    async def _publish(
        self,
        fs: FirestoreService,
        user_id: str,
        meta: dict[str, Any],
        rows: list[dict[str, Any]],
        run: str,
        built_at: float,
        derived_at: float,
    ) -> None:
        """Write new shards, then point the meta doc at them; rows written since ``meta`` stay dirty."""
        shards = await asyncio.to_thread(encode_shards, rows)
        snapshot_id = uuid.uuid4().hex[:12]
        await fs.batch_set(
            SHARDS,
            {_shard_id(user_id, snapshot_id, i): {"user_id": user_id, "data": s} for i, s in enumerate(shards)},
        )
        rev0 = int(meta.get("rev") or 0)
        old: dict[str, Any] = {}

        async def point(tx: TransactionView) -> bool:
            cur = await tx.get(META, user_id)
            if cur is None:  # the account was deleted meanwhile
                return False
            old.clear()
            old.update(cur)
            moved = int(cur.get("rev") or 0) != rev0
            tx.set(
                META,
                user_id,
                {
                    **cur,
                    "snapshot_id": snapshot_id,
                    "shards": len(shards),
                    "catalog_run": run,
                    "built_at": built_at,
                    "derived_at": derived_at,
                    "row_count": len(rows),
                    # This refresh is done, so a later write may ask for the next. Writes during it wait out
                    # the lease: an import in progress must not trigger one full read after another.
                    "refresh_requested_at": cur.get("refresh_requested_at") or 0.0 if moved else 0.0,
                    # Re-reading a row twice is harmless, so a rev that moved keeps every dirty id.
                    "dirty_ids": cur.get("dirty_ids") or [] if moved else [],
                    "stale": bool(cur.get("stale")) if moved else False,
                },
            )
            return True

        kept = await fs.run_transaction(point)
        stale_shards = old if kept else {"snapshot_id": snapshot_id, "shards": len(shards)}
        await self._delete_shards(fs, user_id, stale_shards)

    async def _delete_shards(self, fs: FirestoreService, user_id: str, meta: dict[str, Any]) -> None:
        if meta.get("snapshot_id"):
            ids = [_shard_id(user_id, str(meta["snapshot_id"]), i) for i in range(int(meta.get("shards") or 0))]
            await fs.batch_delete(SHARDS, ids)

    async def _forget(self, fs: FirestoreService, user_id: str, meta: dict[str, Any]) -> None:
        await fs.delete_document(META, user_id)
        await self._delete_shards(fs, user_id, meta)
        self._views.pop(user_id, None)


SNAPSHOTS = UserSongsSnapshots()
