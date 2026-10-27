"""Personal voice pool ("analyse my music", ``/api/voice/personal-pool``): eligibility, cap, budget, batches.

An eligible user asks for up to ``voice_personal_cap`` of their best-known songs to be fetched and analysed
(overnight, after the shared pool: ``fetch.submit_downloads``). Analysed songs join the shared pool: the queue
docs, voice.db and BigQuery rows carry ``source = 'personal'`` and a batch id, never the user.

* Eligible: admins; gen buyers (``gen_users/{email}``, else a query on ``email``: ``total_spent > 0`` or a
  a purchase in ``credit_transactions``: ``PAID_REASONS``); or rich history (sum of ``user_songs.playcount`` >=
  ``HISTORY_LISTENS`` or >= ``HISTORY_SONGS`` rows played at least once). Guests never (throwaway accounts).
* Picks (``pick_personal_songs``): the user's known songs by weight, then playcount, with a community version
  (availability.db), not hidden or "would not sing", not in voice.db and not in the fetch queue (any status).
* Cap: ``voice_personal_cap`` songs per user per rolling ``voice_personal_window_days`` (admins exempt). A request
  is trimmed to what is left; nothing left is 429 ``cap`` with ``next_allowed_at``.
* Budget: ``voice_personal_budget/{YYYY-MM}`` (UTC) = ``{month, est_cost_usd, songs, batches, alerted_80,
  alerted_80_budget, updated_at}``, the month's estimated personal spend. One transaction reads it, trims the
  not-yet-queued picks to what ``voice_monthly_budget_usd`` can still take (none fit and nothing free: 503
  ``budget_paused``, a WARNING) and writes the batch, the queue docs and the new total, so concurrent requests
  can't both slip past the budget. The first request each month that takes the total to
  ``BUDGET_ALERT_FRACTION`` of the budget logs ONE ERROR (``alerted_80``); ``alerted_80_budget`` is the budget it
  fired for, so raising the budget re-arms it.

Firestore:

* ``voice_personal_batches/{batch_id}`` = ``{user_id, song_keys, requested_at, est_cost_usd, month}``.
* ``voice_personal_users/{user_id}`` = ``{last_batch_id, updated_at}``: read in the same transaction, so two
  requests of one user commit one after the other; a pointer naming a batch the cap query didn't see means the
  query is stale, and the request is recomputed (the cap can't be overrun by a double tap).
* ``voice_pool_fetch/{pool_id}`` (``queue``): new songs ``source = 'personal'`` with ``batch_ids = [batch_id]``;
  a song a concurrent batch queued first gets the batch id appended and costs nothing again.
"""

from __future__ import annotations

import asyncio
import logging
import math
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from backend.config import BackendSettings
from backend.models.voice_personal_pool import (
    BatchStatus,
    EligibilityOut,
    EligibilityReason,
    PersonalBatchCreated,
    PersonalBatchOut,
    PersonalPoolOut,
    VoiceCostsOut,
)
from backend.services.firestore_service import FirestoreService
from backend.services.recs_v2 import weights as w
from backend.services.recs_v2.context import HiddenKeysProvider, UserContextV2, build_context
from backend.services.song_feedback_service import USER_SONGS_COLLECTION
from backend.services.voice_data_store import VoiceDataUnavailable, VoiceReader
from karaoke_decide.catalog.reader import CatalogReader
from karaoke_decide.core.models import User
from karaoke_decide.song_search.text_normalize import song_key
from karaoke_decide.voice_pool.queue import FETCH_COLLECTION, pool_id, queue_doc

logger = logging.getLogger(__name__)

BATCHES_COLLECTION = "voice_personal_batches"
BUDGET_COLLECTION = "voice_personal_budget"  # voice_personal_budget/{YYYY-MM}: the month's estimated spend
LAST_BATCH_COLLECTION = "voice_personal_users"  # voice_personal_users/{user_id}: the user's last batch
GEN_USERS_COLLECTION = "gen_users"  # gen's users (read only), as gen_session reads them
HISTORY_LISTENS = 1000
HISTORY_SONGS = 200
BUDGET_ALERT_FRACTION = 0.8
_REQUEST_RETRIES = 3
_DONE = frozenset({"analysed", "failed"})


class PersonalPoolError(Exception):
    """A refused request: HTTP ``status_code`` with the detail ``{"reason": reason, **extra}``."""

    status_code = 400
    reason = "bad_request"

    def __init__(self, **extra: Any) -> None:
        super().__init__(self.reason)
        self.extra = extra

    def detail(self) -> dict[str, Any]:
        return {"reason": self.reason, **self.extra}


class NotEligibleError(PersonalPoolError):
    status_code, reason = 403, "not_eligible"


class CapReachedError(PersonalPoolError):
    status_code, reason = 429, "cap"


class NothingToAddError(PersonalPoolError):
    status_code, reason = 409, "nothing_to_add"


class BudgetPausedError(PersonalPoolError):
    status_code, reason = 503, "budget_paused"

    def __init__(self, spent: float) -> None:
        super().__init__()
        self.spent = spent  # the month's estimate before the refused batch (for the WARNING)


class ConcurrentRequestError(PersonalPoolError):
    """Another request of the same user kept winning the race (``_REQUEST_RETRIES`` times)."""

    status_code, reason = 409, "concurrent_request"


class _StaleError(Exception):
    """The cap query missed a batch committed since: recompute the request."""


class VoiceStoreProvider(Protocol):
    def require(self) -> VoiceReader: ...


class CatalogReaderProvider(Protocol):
    def require(self) -> CatalogReader: ...


# ---------------------------------------------------------------- pure


def month_key(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y-%m")


def _next_month(month: str) -> str:
    year, mon = (int(p) for p in month.split("-"))
    return f"{year + mon // 12:04d}-{mon % 12 + 1:02d}"


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            return _as_utc(datetime.fromisoformat(value))
        except ValueError:
            return None
    return None


def songs_that_fit(spent: float, budget_usd: float, per_song: float) -> int:
    """How many more songs this month's budget can take (0: paused)."""
    if per_song <= 0:
        return 1_000_000
    return max(0, math.floor((budget_usd - spent) / per_song + 1e-9))


def _alerted_budget(month_doc: dict[str, Any], budget_usd: float) -> float | None:
    """The budget the month's 80% alert fired for (None: not yet). A raised budget re-arms the alert."""
    if not month_doc.get("alerted_80"):
        return None
    value = month_doc.get("alerted_80_budget")
    return float(value) if isinstance(value, int | float) else budget_usd


# gen's credit_transactions reasons for money in: Stripe checkout, in-app purchase (which adds no
# total_spent), and "purchase" from older docs.
PAID_REASONS = frozenset({"stripe_purchase", "iap_purchase", "purchase"})


def _paid(gen_user: dict[str, Any] | None) -> bool:
    if not gen_user:
        return False
    if (w.to_int(gen_user.get("total_spent")) or 0) > 0:
        return True
    txns = gen_user.get("credit_transactions")
    return isinstance(txns, list) and any(isinstance(t, dict) and t.get("reason") in PAID_REASONS for t in txns)


def eligibility_from(user: User, gen_user: dict[str, Any] | None, songs: Sequence[dict[str, Any]]) -> EligibilityOut:
    """Admin, else paid (gen), else history; guests are never eligible (see module doc)."""
    plays = [p for p in (w.to_int(d.get("playcount")) for d in songs) if p is not None and p >= 1]
    listens, played = sum(plays), len(plays)
    reason: EligibilityReason
    if user.is_admin:
        reason = "admin"
    elif user.is_guest:
        reason = "not_eligible"
    elif _paid(gen_user):
        reason = "paid"
    elif listens >= HISTORY_LISTENS or played >= HISTORY_SONGS:
        reason = "history"
    else:
        reason = "not_eligible"
    return EligibilityOut(eligible=reason != "not_eligible", reason=reason, listens=listens, songs_with_plays=played)


def cap_usage(
    batches: Sequence[dict[str, Any]], now: datetime, cap: int, window_days: int
) -> tuple[int, datetime | None]:
    """``(songs requested in the window, next_allowed_at)``; ``next_allowed_at`` is None while songs are
    left, else when enough of the oldest batches age out of the window for one more song."""
    start = now - timedelta(days=window_days)
    recent = sorted(
        ((at, len(b.get("song_keys") or [])) for b in batches if (at := _as_utc(b.get("requested_at"))) and at > start),
        key=lambda x: x[0],
    )
    used = sum(n for _, n in recent)
    if used < cap:
        return used, None
    left = used
    for at, n in recent:
        left -= n
        if left < cap:
            return used, at + timedelta(days=window_days)
    return used, None  # pragma: no cover  (left reaches 0 < cap)


def _kn_id(song_id: object) -> int | None:
    s = str(song_id or "")
    return int(s) if s.isdigit() else None


def song_playcounts(reader: CatalogReader, docs: Sequence[dict[str, Any]]) -> dict[str, int]:
    """``{song_key: playcount}`` (max over rows), keyed as ``build_context`` keys known songs."""
    kn_keys = reader.kn_song_keys([i for i in (_kn_id(d.get("song_id")) for d in docs) if i is not None])
    out: dict[str, int] = {}
    for d in docs:
        kn_id = _kn_id(d.get("song_id"))
        key = kn_keys.get(kn_id) if kn_id is not None else None
        if key is None and d.get("artist") and d.get("title"):
            key = song_key(d["artist"], d["title"])
        plays = w.to_int(d.get("playcount"))
        if key and plays:
            out[key] = max(out.get(key, 0), plays)
    return out


def personal_candidates(
    ctx: UserContextV2, reader: CatalogReader, voice: VoiceReader | None, playcounts: dict[str, int] | None = None
) -> list[tuple[str, str, str]]:
    """``(song_key, artist, title)`` of the user's known songs that could join the pool, best first (weight,
    then playcount): a community version, not hidden or "would not sing", not already in voice.db."""
    plays = playcounts or {}
    known = [k for k in ctx.known_songs if k not in ctx.hidden and k not in ctx.session_no]
    known.sort(key=lambda k: (-ctx.known_songs[k], -plays.get(k, 0), k))
    community = reader.community_songs(known)
    return [
        (k, *community[k]) for k in known if k in community and (voice is None or voice.song(k) is None)
    ]  # fmt: skip


def pick_personal_songs(
    ctx: UserContextV2,
    reader: CatalogReader,
    voice: VoiceReader | None,
    queued: set[str],
    n: int = 20,
    playcounts: dict[str, int] | None = None,
) -> list[str]:
    """The ``n`` best ``personal_candidates`` not in the fetch queue (``queued`` song keys, any status)."""
    return [k for k, _, _ in personal_candidates(ctx, reader, voice, playcounts) if k not in queued][:n]


def batch_progress(statuses: Sequence[str | None], ready: int) -> tuple[int, BatchStatus]:
    """``(ready, status)``: done when every song is analysed or failed (a missing queue doc counts as
    failed), running once any is past queued, else queued."""
    st = [s or "failed" for s in statuses]
    if all(s in _DONE for s in st):
        return ready, "done"
    return ready, "running" if any(s != "queued" for s in st) else "queued"


# ---------------------------------------------------------------- service


class VoicePoolService:
    def __init__(
        self,
        firestore: FirestoreService,
        voice: VoiceStoreProvider,
        catalog: CatalogReaderProvider,
        feedback: HiddenKeysProvider,
        settings: BackendSettings,
    ) -> None:
        self._fs = firestore
        self._voice = voice
        self._catalog = catalog
        self._feedback = feedback
        self._settings = settings

    def _voice_reader(self) -> VoiceReader | None:
        try:
            return self._voice.require()
        except VoiceDataUnavailable:
            return None

    async def _gen_user(self, email: str | None) -> dict[str, Any] | None:
        """gen's user doc for the email (id = lowercase email; legacy hash-keyed docs carry ``email``)."""
        if not email:
            return None
        email = email.strip().lower()
        doc = await self._fs.get_document(GEN_USERS_COLLECTION, email)
        if doc:
            return doc
        matches = await self._fs.query_documents(GEN_USERS_COLLECTION, filters=[("email", "==", email)], limit=1)
        return matches[0] if matches else None

    async def _user_songs(self, user_id: str) -> list[dict[str, Any]]:
        return await self._fs.query_documents(USER_SONGS_COLLECTION, filters=[("user_id", "==", user_id)])

    async def _eligibility(self, user: User, songs: Sequence[dict[str, Any]]) -> EligibilityOut:
        gen_user = None if user.is_admin or user.is_guest else await self._gen_user(user.email)
        return eligibility_from(user, gen_user, songs)

    async def eligibility(self, user: User) -> EligibilityOut:
        return await self._eligibility(user, await self._user_songs(user.id))

    async def _batches(self, user_id: str) -> list[dict[str, Any]]:
        return await self._fs.query_documents(BATCHES_COLLECTION, filters=[("user_id", "==", user_id)])

    # ------------------------------------------------------------ GET

    async def status(self, user: User) -> PersonalPoolOut:
        """``GET /api/voice/personal-pool``: eligibility, the cap, and each batch's progress (newest first)."""
        s = self._settings
        elig, batches = await asyncio.gather(self.eligibility(user), self._batches(user.id))
        used, next_at = cap_usage(batches, datetime.now(UTC), s.voice_personal_cap, s.voice_personal_window_days)
        batches.sort(key=lambda b: _as_utc(b.get("requested_at")) or datetime.min.replace(tzinfo=UTC), reverse=True)
        keys = list(dict.fromkeys(k for b in batches for k in b.get("song_keys") or []))
        docs = await self._fs.get_many(FETCH_COLLECTION, [pool_id(k) for k in keys])
        status_of = {k: (docs.get(pool_id(k)) or {}).get("status") for k in keys}
        reader = self._voice_reader()
        analysed = [k for k in keys if status_of[k] == "analysed"]

        def in_voice_db() -> set[str]:
            return {k for k in analysed if reader is not None and reader.analysis(k) is not None}

        ready_keys = await asyncio.to_thread(in_voice_db)
        out = []
        for b in batches:
            song_keys = list(b.get("song_keys") or [])
            ready, state = batch_progress(
                [status_of.get(k) for k in song_keys], sum(k in ready_keys for k in song_keys)
            )
            out.append(
                PersonalBatchOut(
                    batch_id=str(b["id"]),
                    requested_at=_as_utc(b.get("requested_at")) or datetime.now(UTC),
                    total=len(song_keys),
                    ready=ready,
                    status=state,
                )
            )
        return PersonalPoolOut(
            eligible=elig.eligible,
            reason=elig.reason,
            listens=elig.listens,
            songs_with_plays=elig.songs_with_plays,
            next_allowed_at=None if user.is_admin else next_at,
            cap=s.voice_personal_cap,
            used=used,
            batches=out,
        )

    # ------------------------------------------------------------ POST

    async def request(self, user: User) -> PersonalBatchCreated:
        """``POST /api/voice/personal-pool``: queue the user's next batch (see module doc).

        Raises ``NotEligibleError``, ``CapReachedError``, ``NothingToAddError``, ``BudgetPausedError``,
        ``CatalogUnavailable`` (503 warming_up)."""
        songs = await self._user_songs(user.id)
        elig = await self._eligibility(user, songs)
        if not elig.eligible:
            raise NotEligibleError()
        reader = await asyncio.to_thread(self._catalog.require)  # CatalogUnavailable -> 503 warming_up
        playcounts = await asyncio.to_thread(song_playcounts, reader, songs)
        for _ in range(_REQUEST_RETRIES):
            try:
                return await self._request_once(user, reader, playcounts)
            except _StaleError:
                continue
        raise ConcurrentRequestError()

    async def _request_once(
        self, user: User, reader: CatalogReader, playcounts: dict[str, int]
    ) -> PersonalBatchCreated:
        s = self._settings
        now = datetime.now(UTC)
        batches = await self._batches(user.id)
        used, next_at = cap_usage(batches, now, s.voice_personal_cap, s.voice_personal_window_days)
        allowance = s.voice_personal_cap if user.is_admin else s.voice_personal_cap - used
        if allowance <= 0:
            raise CapReachedError(next_allowed_at=next_at.isoformat() if next_at else None)

        ctx = await build_context(self._fs, reader, self._feedback, user.id)
        voice = self._voice_reader()
        candidates = await asyncio.to_thread(personal_candidates, ctx, reader, voice, playcounts)
        in_queue = await self._fs.get_many(FETCH_COLLECTION, [pool_id(k) for k, _, _ in candidates])
        queued = {k for k, _, _ in candidates if pool_id(k) in in_queue}
        picks = [c for c in candidates if c[0] not in queued][:allowance]
        if not picks:
            raise NothingToAddError()

        batch_id = uuid.uuid4().hex
        month = month_key(now)
        known_batches = {str(b["id"]) for b in batches}
        per_song, budget_usd = s.voice_est_cost_per_song_usd, s.voice_monthly_budget_usd

        async def create(txn: Any) -> tuple[list[str], float, float, bool]:
            budget = await txn.get(BUDGET_COLLECTION, month) or {}
            last = await txn.get(LAST_BATCH_COLLECTION, user.id)
            existing = [await txn.get(FETCH_COLLECTION, pool_id(k)) for k, _, _ in picks]
            if last is not None and str(last.get("last_batch_id")) not in known_batches:
                raise _StaleError
            spent = float(budget.get("est_cost_usd") or 0.0)
            # Songs not yet queued are trimmed to what the budget can still take (as the cap trims); songs a
            # concurrent batch queued meanwhile are free (batch id appended) and always go in.
            fits = songs_that_fit(spent, budget_usd, per_song)
            kept: list[tuple[tuple[str, str, str], dict[str, Any] | None]] = []
            new = 0
            for pick, doc in zip(picks, existing, strict=True):
                if doc is None:
                    if new >= fits:
                        continue
                    new += 1
                kept.append((pick, doc))
            if not kept:
                raise BudgetPausedError(spent)
            cost = round(new * per_song, 6)
            total = round(spent + cost, 6)
            alerted_for = _alerted_budget(budget, budget_usd)
            crossed = total >= BUDGET_ALERT_FRACTION * budget_usd and (alerted_for is None or budget_usd > alerted_for)
            for (key, artist, title), doc in kept:
                if doc is None:
                    item = {"song_key": key, "artist": artist, "title": title, "source": "personal"}
                    txn.set(FETCH_COLLECTION, pool_id(key), queue_doc({**item, "batch_ids": [batch_id]}, now))
                elif batch_id not in (doc.get("batch_ids") or []):
                    rest = {f: v for f, v in doc.items() if f != "id"}
                    txn.set(
                        FETCH_COLLECTION, pool_id(key), {**rest, "batch_ids": [*(doc.get("batch_ids") or []), batch_id]}
                    )
            txn.set(
                BUDGET_COLLECTION,
                month,
                {
                    "month": month,
                    "est_cost_usd": total,
                    "songs": int(budget.get("songs") or 0) + new,
                    "batches": int(budget.get("batches") or 0) + 1,
                    "alerted_80": alerted_for is not None or crossed,
                    "alerted_80_budget": budget_usd if crossed else alerted_for,
                    "updated_at": now,
                },
            )
            txn.set(
                BATCHES_COLLECTION,
                batch_id,
                {
                    "user_id": user.id,
                    "song_keys": [k for (k, _, _), _ in kept],
                    "requested_at": now,
                    "est_cost_usd": cost,
                    "month": month,
                },
            )
            txn.set(LAST_BATCH_COLLECTION, user.id, {"last_batch_id": batch_id, "updated_at": now})
            return [k for (k, _, _), _ in kept], cost, total, crossed

        try:
            keys, cost, total, crossed = await self._fs.run_transaction(create)
        except BudgetPausedError as e:
            logger.warning(
                "Voice personal pool: a %d-song request refused, the $%.2f monthly budget for %s is used up "
                "($%.2f est.)", len(picks), budget_usd, month, e.spent,
            )  # fmt: skip
            raise
        if crossed:
            logger.error(
                "Voice personal pool at %d%% of $%.2f monthly budget ($%.2f est.); requests pause at 100%%. "
                "Raise VOICE_MONTHLY_BUDGET_USD or wait for %s.",
                round(100 * total / budget_usd), budget_usd, total, _next_month(month),
            )  # fmt: skip
        if len(keys) < len(picks):
            logger.info(
                "Voice personal pool: %s's request trimmed to %d of %d songs by the %s budget", user.id, len(keys),
                len(picks), month,
            )  # fmt: skip
        return PersonalBatchCreated(batch_id=batch_id, songs=keys, est_cost_usd=cost)


async def voice_costs(firestore: FirestoreService, settings: BackendSettings) -> VoiceCostsOut:
    """``GET /api/admin/voice/costs``: this month's personal spend against the budget, and the shared pool."""
    month = month_key(datetime.now(UTC))
    budget, shared, sheet = await asyncio.gather(
        firestore.get_document(BUDGET_COLLECTION, month),
        firestore.count_documents(FETCH_COLLECTION, filters=[("status", "==", "analysed"), ("source", "==", "shared")]),
        firestore.count_documents(
            FETCH_COLLECTION, filters=[("status", "==", "analysed"), ("source", "==", "andrew_sheet")]
        ),
    )
    spent = float((budget or {}).get("est_cost_usd") or 0.0)
    per_song, budget_usd = settings.voice_est_cost_per_song_usd, settings.voice_monthly_budget_usd
    return VoiceCostsOut(
        month=month,
        personal_batches=int((budget or {}).get("batches") or 0),
        personal_songs=int((budget or {}).get("songs") or 0),
        est_cost_usd=round(spent, 4),
        budget_usd=budget_usd,
        paused=songs_that_fit(spent, budget_usd, per_song) == 0,
        shared_songs_analysed=shared + sheet,
        est_cost_per_song_usd=per_song,
    )
