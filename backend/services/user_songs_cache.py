"""One shared, bounded read of a user's whole ``user_songs`` collection, per instance.

Recs v2, get-made and the sources summary each need every row. For a user with 50k songs a full
read holds ~130 MiB for the tens of seconds it takes, and the app asks for all three on launch and
again on retry: on 2026-11-03 eleven overlapping reads took a 2 GiB instance past its limit.

So, per instance:

- concurrent readers of one user share one read (``SingleFlight``), and the rows stay for ``ttl_s``;
- a read selects only ``USER_SONGS_FIELDS`` (58 MiB rather than 132 MiB for 50k rows);
- at most ``max_heavy_reads`` reads of ``heavy_rows`` or more run at once, and the rest wait. A count
  query tells them apart, so users with small libraries never queue behind heavy ones;
- retained rows stay within ``max_cached_rows``, oldest out first.

A write through ``FirestoreService`` on this instance drops the user's rows, so readers see their own
writes. Another instance (or a job) only catches up when its ``ttl_s`` ends; callers that can't accept
that (voice sessions, which read back their own labels) pass ``fresh=True``.

The rows are shared between requests: treat them as read-only.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable, Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from backend.services.firestore_service import FirestoreService, add_write_listener

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

COLLECTION = "user_songs"

# Every field recs v2's context, get-made and the known-songs count read from a row
# (backend/tests/test_user_songs_cache.py fails when a consumer reads one that isn't here).
USER_SONGS_FIELDS = [
    "artist",
    "loved",
    "playcount",
    "rank",
    "session_feel",
    "session_would_sing",
    "song_id",
    "song_key",
    "source",
    "sources",
    "sung_feel_latest",
    "sung_key_latest",
    "times_sung",
    "title",
]

TTL_S = 60.0
MAX_CACHED_ROWS = 120_000  # ~140 MiB of projected rows
HEAVY_ROWS = 5_000
MAX_HEAVY_READS = 2


class SingleFlight(Generic[_T]):
    """Concurrent ``run`` calls with one key share one execution; nothing is kept once it finishes.

    The work runs in its own task: a caller that goes away doesn't cancel it for the others."""

    def __init__(self) -> None:
        self._tasks: dict[Hashable, asyncio.Task[_T]] = {}

    def _finished(self, key: Hashable, task: asyncio.Task[_T]) -> None:
        if self._tasks.get(key) is task:
            del self._tasks[key]
        if not task.cancelled():
            task.exception()  # retrieved, so a failure no caller stayed for isn't logged at ERROR

    async def run(self, key: Hashable, work: Callable[[], Awaitable[_T]]) -> _T:
        task = self._tasks.get(key)
        if task is None:

            async def call() -> _T:
                return await work()

            task = asyncio.create_task(call())
            self._tasks[key] = task
            task.add_done_callback(lambda t: self._finished(key, t))
        return await asyncio.shield(task)


@dataclass
class _Entry:
    rows: list[dict[str, Any]]
    expires_at: float


class UserSongsCache:
    def __init__(
        self,
        ttl_s: float = TTL_S,
        max_cached_rows: int = MAX_CACHED_ROWS,
        heavy_rows: int = HEAVY_ROWS,
        max_heavy_reads: int = MAX_HEAVY_READS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_s = ttl_s
        self._max_cached_rows = max_cached_rows
        self._heavy_rows = heavy_rows
        self._heavy_slots = asyncio.Semaphore(max_heavy_reads)
        self._clock = clock
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._flights: SingleFlight[list[dict[str, Any]]] = SingleFlight()
        self._generations: dict[str, int] = {}

    @property
    def cached_rows(self) -> int:
        return sum(len(e.rows) for e in self._entries.values())

    def generation(self, user_id: str) -> int:
        """Changes whenever this instance writes one of the user's rows."""
        return self._generations.get(user_id, 0)

    async def rows(self, firestore: FirestoreService, user_id: str, *, fresh: bool = False) -> list[dict[str, Any]]:
        if fresh:
            return await self._read(firestore, user_id)
        entry = self._entries.get(user_id)
        if entry is not None and entry.expires_at > self._clock():
            self._entries.move_to_end(user_id)
            return entry.rows
        generation = self.generation(user_id)
        # Keyed by generation: a reader arriving after a write must not join a read begun before it.
        return await self._flights.run((user_id, generation), lambda: self._load(firestore, user_id, generation))

    def on_write(self, collection: str, doc_ids: Sequence[str]) -> None:
        """Doc ids are ``{user_id}:...``."""
        if collection != COLLECTION:
            return
        for user_id in {doc_id.split(":", 1)[0] for doc_id in doc_ids}:
            self._generations[user_id] = self.generation(user_id) + 1
            self._entries.pop(user_id, None)

    async def _load(self, firestore: FirestoreService, user_id: str, generation: int) -> list[dict[str, Any]]:
        rows = await self._read(firestore, user_id)
        if self.generation(user_id) == generation:  # else a write landed mid-read: serve these, don't keep them
            self._retain(user_id, rows)
        return rows

    async def _read(self, firestore: FirestoreService, user_id: str) -> list[dict[str, Any]]:
        filters = [("user_id", "==", user_id)]
        try:
            count = await firestore.count_documents(COLLECTION, filters=filters)
        except Exception:
            logger.warning("user_songs count failed for %s; treating the read as heavy", user_id, exc_info=True)
            count = self._heavy_rows

        async def read() -> list[dict[str, Any]]:
            return await firestore.query_documents(COLLECTION, filters=filters, select=USER_SONGS_FIELDS)

        if count < self._heavy_rows:
            return await read()
        queued_at = time.monotonic()
        async with self._heavy_slots:
            started_at = time.monotonic()
            rows = await read()
        logger.info(
            "heavy user_songs read for %s: %d rows in %.1f s after %.1f s queued",
            user_id,
            len(rows),
            time.monotonic() - started_at,
            started_at - queued_at,
        )
        return rows

    def _retain(self, user_id: str, rows: list[dict[str, Any]]) -> None:
        if len(rows) > self._max_cached_rows:
            return
        entry = _Entry(rows, self._clock() + self._ttl_s)
        self._entries[user_id] = entry
        self._entries.move_to_end(user_id)
        while self.cached_rows > self._max_cached_rows:
            self._entries.popitem(last=False)
        asyncio.get_running_loop().call_later(self._ttl_s, self._expire, user_id, entry)

    def _expire(self, user_id: str, entry: _Entry) -> None:
        """Release the rows even if no request comes along to notice they expired."""
        if self._entries.get(user_id) is entry:
            del self._entries[user_id]


USER_SONGS = UserSongsCache()
add_write_listener(USER_SONGS.on_write)


async def read_user_songs(firestore: Any, user_id: str, *, fresh: bool = False) -> list[dict[str, Any]]:
    """Every ``user_songs`` row of the user, for read-only use. ``fresh`` skips the cache and the
    shared read (the heavy-read limit still applies)."""
    if isinstance(firestore, FirestoreService):
        return await USER_SONGS.rows(firestore, user_id, fresh=fresh)
    # Coherence relies on FirestoreService's write hook: anything else reads straight through.
    rows: list[dict[str, Any]] = await firestore.query_documents(COLLECTION, filters=[("user_id", "==", user_id)])
    return rows
