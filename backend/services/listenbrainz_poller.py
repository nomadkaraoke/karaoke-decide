"""Daily ListenBrainz poller: folds each connected user's new listens into their profile.

Runs as the ``lb-poll`` Cloud Run Job (``python -m backend.jobs.lb_poll``), and on the local
stack through ``POST /api/test/listenbrainz/poll``. For every ``listenbrainz`` connection,
least recently polled first:

* **First poll** (no ``lb_last_listen_ts``): backfill newest-first with ``max_ts`` paging, at most
  ``FIRST_POLL_PAGES`` pages of ``PAGE_SIZE``, ingested with ``play_mode="max"`` (re-importing a
  history the user already brought in another way never doubles it).
* **Later polls**: ``min_ts=lb_last_listen_ts``, paging upwards while pages are full, at most
  ``LATER_POLL_PAGES``, ingested with ``play_mode="add"``.

Listens are summed per ``(identity_normalize(artist), identity_normalize(title))`` before ingest.
Then the connection gets ``lb_last_listen_ts`` / ``lb_last_listen_at`` (newest listen seen),
``lb_last_polled_at`` and ``lb_stale`` (no listen for ``STALE_AFTER``, counting from the connection
date when there has never been one); the services summary shows ``lb_stale`` as "stale". A
Spotify export handed to ListenBrainz (``lb_pending_import``) that isn't finished has its status
refreshed.

Every LB request passes a ``RateGate`` (25 per minute across the run). A 429 sleeps its
``retry_after`` once and retries; a second 429 skips the user until the next run. The run stops
starting new users once the budget is spent and reports how many were left.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any, TypeVar

from backend.config import BackendSettings
from backend.services.firestore_service import FirestoreService
from backend.services.import_ingest_service import ImportIngestService, IngestTrack
from backend.services.music_service_service import MusicServiceError, MusicServiceService
from karaoke_decide.core.exceptions import ExternalServiceError
from karaoke_decide.services.listenbrainz import (
    LB_IMPORT_TERMINAL_STATUSES,
    ListenBrainzClient,
    ListenBrainzListen,
    ListenBrainzRateLimitedError,
)
from karaoke_decide.song_search.text_normalize import identity_normalize

logger = logging.getLogger(__name__)

SERVICES_COLLECTION = MusicServiceService.SERVICES_COLLECTION
PAGE_SIZE = 1000
FIRST_POLL_PAGES = 20
LATER_POLL_PAGES = 10
REQUESTS_PER_MINUTE = 25
STALE_AFTER = timedelta(days=30)

_T = TypeVar("_T")


@dataclass
class PollReport:
    users_polled: int = 0
    listens_added: int = 0
    imports_checked: int = 0
    rate_limited: int = 0
    remaining: int = 0


class RateGate:
    """At most ``per_minute`` calls in any 60 s window (sliding window)."""

    def __init__(
        self,
        per_minute: int = REQUESTS_PER_MINUTE,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self.per_minute = per_minute
        self._clock = clock
        self._sleep = sleep
        self._stamps: deque[float] = deque()

    async def wait(self) -> None:
        while True:
            now = self._clock()
            while self._stamps and self._stamps[0] <= now - 60:
                self._stamps.popleft()
            if len(self._stamps) < self.per_minute:
                self._stamps.append(now)
                return
            await self._sleep(self._stamps[0] + 60 - now)


class _RateLimitedTwiceError(Exception):
    """Rate limited twice in a row: leave this user for the next run."""


class _IngestFailedError(Exception):
    """Writing the user's listens into their profile failed (not a ListenBrainz problem)."""


class _ConnectionGoneError(Exception):
    """The connection was removed while we polled it: write nothing."""


class ListenBrainzPoller:
    def __init__(
        self,
        settings: BackendSettings,
        firestore: FirestoreService,
        music_service: MusicServiceService,
        ingest: ImportIngestService,
        client: ListenBrainzClient,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        utcnow: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.settings = settings
        self.db = firestore
        self.music_service = music_service
        self.ingest = ingest
        self.client = client
        self._clock = clock
        self._sleep = sleep
        self._utcnow = utcnow
        self._gate = RateGate(REQUESTS_PER_MINUTE, clock, sleep)

    async def run(self, budget_seconds: int) -> PollReport:
        """Poll every ListenBrainz connection until done or ``budget_seconds`` is spent; logs one
        INFO summary line."""
        deadline = self._clock() + budget_seconds
        report = PollReport()
        docs = await self.db.query_documents(SERVICES_COLLECTION, filters=[("service_type", "==", "listenbrainz")])
        # Never polled first, then least recently polled (ISO strings sort chronologically).
        docs.sort(key=lambda d: (d.get("lb_last_polled_at") is not None, str(d.get("lb_last_polled_at") or "")))
        failed = 0
        for i, doc in enumerate(docs):
            if self._clock() >= deadline:
                report.remaining = len(docs) - i
                break
            try:
                await self._poll_user(doc, report)
            except _RateLimitedTwiceError:
                report.rate_limited += 1
                logger.warning("ListenBrainz rate limited twice; user %s waits for the next poll", doc.get("user_id"))
            except _ConnectionGoneError:
                logger.debug("ListenBrainz connection for %s removed during the poll", doc.get("user_id"))
            except _IngestFailedError as e:
                failed += 1
                logger.warning("ListenBrainz ingest failed for user %s", doc.get("user_id"), exc_info=e.__cause__)
            except ExternalServiceError as e:  # LB 404 (renamed/deleted user), 5xx, network
                logger.warning("ListenBrainz poll skipped user %s: %s", doc.get("user_id"), e)
            except Exception:
                failed += 1
                logger.warning("ListenBrainz poll failed for user %s", doc.get("user_id"), exc_info=True)
        logger.info(
            "lb-poll: users_polled=%d listens_added=%d imports_checked=%d rate_limited=%d remaining=%d "
            "failed=%d of %d connections",
            report.users_polled,
            report.listens_added,
            report.imports_checked,
            report.rate_limited,
            report.remaining,
            failed,
            len(docs),
        )
        if failed:
            # One alert per run, not one per user.
            logger.error("lb-poll: %d of %d ListenBrainz users failed unexpectedly (see warnings)", failed, len(docs))
        return report

    # ------------------------------------------------------------------ one user

    async def _poll_user(self, doc: dict[str, Any], report: PollReport) -> None:
        user_id, username = doc.get("user_id"), doc.get("service_username")
        if not user_id or not username:
            return
        raw_cursor = doc.get("lb_last_listen_ts")
        cursor = int(raw_cursor) if raw_cursor is not None else None
        first = cursor is None
        # Never fails (returns None when it can't tell), and runs before the ingest: nothing that can
        # fail may sit between an "add" ingest and the cursor write, or the next run adds them again.
        status = await self._check_pending_import(doc)
        listens = await (self._backfill(username) if cursor is None else self._since(username, cursor))
        tracks = _aggregate(listens)
        if tracks:
            try:
                await self.ingest.ingest(user_id, "listenbrainz", tracks, play_mode="max" if first else "add")
            except Exception as e:
                raise _IngestFailedError from e

        now = self._utcnow()
        fields: dict[str, Any] = {"lb_last_polled_at": now.isoformat()}
        last_listen_at = doc.get("lb_last_listen_at")
        if listens:
            newest = max(x.listened_at for x in listens)
            if cursor is None or newest > cursor:
                last_listen_at = datetime.fromtimestamp(newest, UTC).isoformat()
                fields.update(lb_last_listen_ts=newest, lb_last_listen_at=last_listen_at)
        fields["lb_stale"] = _stale(last_listen_at or doc.get("connected_at") or doc.get("created_at"), now)
        pending_id = (doc.get("lb_pending_import") or {}).get("id")
        doc_id = str(doc["id"])

        def write(cur: dict[str, Any] | None) -> dict[str, Any]:
            if cur is None:
                raise _ConnectionGoneError
            new = {**cur, **fields}
            cur_pending = cur.get("lb_pending_import")
            # Only update the import we asked about: a new upload may have replaced it meanwhile.
            if status and isinstance(cur_pending, dict) and cur_pending.get("id") == pending_id:
                new["lb_pending_import"] = {**cur_pending, "status": status, "checked_at": now.isoformat()}
            return new

        await self.db.transform_document_atomically(SERVICES_COLLECTION, doc_id, write)
        report.users_polled += 1
        report.listens_added += sum(t.plays or 0 for t in tracks)
        if status:
            report.imports_checked += 1

    async def _backfill(self, username: str) -> list[ListenBrainzListen]:
        """Newest first: each page asks for listens before the oldest one already seen."""
        out: list[ListenBrainzListen] = []
        max_ts: int | None = None
        for _ in range(FIRST_POLL_PAGES):
            page = await self._call(partial(self.client.get_listens, username, count=PAGE_SIZE, max_ts=max_ts))
            out += page
            if len(page) < PAGE_SIZE:
                break
            max_ts = min(x.listened_at for x in page)
        return out

    async def _since(self, username: str, cursor: int) -> list[ListenBrainzListen]:
        """Listens after ``cursor``. With only ``min_ts`` LB returns the ``count`` listens just after
        it, so each further page starts after the newest one already seen."""
        out: list[ListenBrainzListen] = []
        min_ts = cursor
        for _ in range(LATER_POLL_PAGES):
            page = await self._call(partial(self.client.get_listens, username, count=PAGE_SIZE, min_ts=min_ts))
            out += page
            if len(page) < PAGE_SIZE:
                break
            min_ts = max(x.listened_at for x in page)
        return out

    async def _check_pending_import(self, doc: dict[str, Any]) -> str | None:
        """The latest status of an unfinished ``lb_pending_import``, or None (none, or can't tell)."""
        pending = doc.get("lb_pending_import")
        if (
            doc.get("auth") != "oauth"
            or not isinstance(pending, dict)
            or not pending.get("id")
            or pending.get("status") in LB_IMPORT_TERMINAL_STATUSES
        ):
            return None
        try:
            # The per-user refresh lease inside serialises token refreshes with the API.
            token = await self.music_service.get_listenbrainz_token(self.music_service.service_from_doc(doc))
            result = await self._call(partial(self.client.get_import_status, token, str(pending["id"])))
        except (MusicServiceError, ExternalServiceError, _RateLimitedTwiceError) as e:
            logger.warning(
                "Could not check ListenBrainz import %s for %s: %s: %s",
                pending["id"],
                doc.get("user_id"),
                type(e).__name__,
                e,
            )
            return None
        except Exception:
            logger.warning("ListenBrainz import check failed for %s", doc.get("user_id"), exc_info=True)
            return None
        return str(result.get("status") or "") or None

    async def _call(self, fn: Callable[[], Awaitable[_T]]) -> _T:
        """One LB request through the rate gate; a 429 waits ``retry_after`` and retries once."""
        await self._gate.wait()
        try:
            return await fn()
        except ListenBrainzRateLimitedError as e:
            await self._sleep(e.retry_after)
        await self._gate.wait()
        try:
            return await fn()
        except ListenBrainzRateLimitedError:
            raise _RateLimitedTwiceError


def _aggregate(listens: list[ListenBrainzListen]) -> list[IngestTrack]:
    """One track per (normalized artist, normalized title): plays counted, latest listen kept.
    The display names are the newest listen's."""
    tracks: dict[tuple[str, str], IngestTrack] = {}
    for x in sorted(listens, key=lambda x: -x.listened_at):
        key = (identity_normalize(x.artist_name), identity_normalize(x.track_name))
        if not key[0] or not key[1]:
            continue
        t = tracks.get(key)
        if t is None:
            tracks[key] = IngestTrack(artist=x.artist_name, title=x.track_name, plays=1, last_ts=x.listened_at)
        else:
            t.plays = (t.plays or 0) + 1
    return list(tracks.values())


def _stale(since: Any, now: datetime) -> bool:
    if not since:
        return False
    try:
        at = datetime.fromisoformat(str(since))
    except ValueError:
        return False
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return now - at > STALE_AFTER
