"""Voice session: setup, next song, skips, attempts and the result (``/api/voice/sessions``).

Firestore:

* ``voice_sessions/{id}`` = ``{user_id, setup, state, created_at, updated_at, tired_count, skips,
  prior_profile, pending_song_key, last_offer, done_reason}``. ``skips`` entries are
  ``{song_key, reason, video_id, artist_key, link_out, at, done}`` (a retried skip is not appended
  twice); ``prior_profile`` is the 3c voice profile at setup (the prior, frozen for the session); ``last_offer`` is ``{song_key, reason_type, link_out}``.
* ``voice_attempts/{session_id}:{n}`` = ``{user_id, session_id, n, song_key, video_id, quiz, context,
  pitch, stopped_early, started_at, ended_at, recording_id, song}``; ``context`` is the setup at
  attempt time and ``song`` a snapshot of the song (artist, title, artist_key, stratum, demand) so the
  posterior can be rebuilt without voice.db.

The posterior is never stored: every call rebuilds it from the prior and all attempts (at most 12).
Session-state mapping onto C2's ``SessionState``:

* ``comfort_history`` = the prior's comfort estimate, then one entry after each attempt.
* ``range_attempts`` counts attempts carrying range evidence: Q2 (position) answered, which the API
  requires, and either the singer knew the song better than "barely" or the pitch summary is usable
  (``model.usable_pitch``). Every attempt still updates the posterior (with its context weight).
* ``tired_count`` = attempts whose Q7 ``voice_now`` is "tired".
* Done songs = attempted songs, plus "dont_know" / "something_else" skips, plus a "not_embeddable"
  skip once the song has no version left to try (see ``skip``). "no_candidates" is set here, when
  ``choose_next`` finds nothing; Finish is the client calling ``result``.

Outputs (Task C5, ``voice_session_outputs``): each attempt, once, in one transaction guarded by the
attempt doc's ``outputs_applied`` marker (so a retried POST, or a retry after a failure part-way,
counts it exactly once): the song's ``user_songs`` row (``choose_user_song_row``: the
``{user_id}:{kn_id}`` row when it exists, else ``loved_doc_id``) gets ``times_sung`` + 1 and the
session labels, and ``voice_version_stats/{video_id}`` its counters; a "loved" feel then calls
"Love it". ``result`` saves the session's voice profile (``should_save``) and lists the songs that
suit it (recs v2 ``singable`` + ``sort=fit``) for the saved profile, or the unsaved derived one.

Versions: voice.db carries the selection's oEmbed verdicts; ``voice_version_stats/{video_id}``
(``embeddable`` reported by the app) overrides them, read live for the picked song. A song with no
embeddable version is offered once more as a link-out (``version.embeddable = false``).

CPU work (pool index, posterior rebuild, candidate scoring) runs in ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from pydantic import ValidationError

from backend.models.voice_session import (
    AttemptIn,
    AttemptOut,
    LovedSongOut,
    NextDoneOut,
    NextSongOut,
    ResultOut,
    SessionCreated,
    SessionPatch,
    SessionSetup,
    SkipIn,
    SongRangeOut,
    VersionOut,
)
from backend.services.catalog_store import CatalogUnavailable
from backend.services.firestore_service import FirestoreService
from backend.services.recs_v2 import weights as recs_weights
from backend.services.recs_v2.context import HiddenKeysProvider, UserContextV2, build_context
from backend.services.recs_v2.filters import RecFilters
from backend.services.recs_v2.service import CatalogProvider, RecSong
from backend.services.song_feedback_service import USER_SONGS_COLLECTION, choose_user_song_row, kn_id_for_song_key
from backend.services.voice_data_store import VoiceReader
from backend.services.voice_profile_service import VoiceProfileIn, VoiceProfileOut, VoiceProfileUserNotFoundError
from backend.services.voice_session_outputs import (
    KeyHint,
    bump_version_stats,
    key_hint,
    session_profile,
    should_save,
    user_song_row,
)
from karaoke_decide.core.models import User
from karaoke_decide.voice_pool.models import Version
from karaoke_decide.voice_pool.versions import VersionStats, choose_versions
from karaoke_decide.voice_pool.voicedb import VERSION_STATS_COLLECTION
from karaoke_decide.voice_session.model import (
    Context,
    Evidence,
    PitchSummary,
    Posterior,
    Quiz,
    SongDemand,
    key_shift_direction,
    usable_pitch,
)
from karaoke_decide.voice_session.select import CandidateSong, SessionState, choose_next, know_weight
from karaoke_decide.voice_session.stopping import MAX_ATTEMPTS, DoneReason, ResultSummary, check, summary

logger = logging.getLogger(__name__)

SESSIONS_COLLECTION = "voice_sessions"
ATTEMPTS_COLLECTION = "voice_attempts"
SUITED_LIMIT = 15
ALTERNATIVES = 2
MAX_REPICKS = 5  # picks whose versions all turned out unembeddable (live stats) before offering a link-out
_CREATE_RETRIES = 3


class VoiceSessionError(Exception):
    status_code = 400
    detail = "bad_request"


class SessionNotFoundError(VoiceSessionError):
    status_code, detail = 404, "session_not_found"


class SessionNotActiveError(VoiceSessionError):
    status_code, detail = 409, "session_not_active"


class UnknownSongError(VoiceSessionError):
    status_code, detail = 422, "unknown_song"


class UnknownVersionError(VoiceSessionError):
    status_code, detail = 422, "unknown_version"


class AttemptLimitError(VoiceSessionError):
    status_code, detail = 409, "max_attempts"


class VoiceStoreProvider(Protocol):
    def require(self) -> VoiceReader: ...


class VoiceProfileProvider(Protocol):
    async def get(self, user_id: str) -> VoiceProfileOut | None: ...

    async def put(self, user_id: str, profile: VoiceProfileIn) -> VoiceProfileOut: ...


class SessionFeedbackProvider(HiddenKeysProvider, Protocol):
    """The slice of ``SongFeedbackService`` a session uses: hidden songs and "Love it"."""

    async def love(self, user_id: str, song_key: str, artist: str, title: str) -> None: ...


class SuitedRecommender(Protocol):
    async def recommend(
        self,
        user: User,
        f: RecFilters,
        sort: Any = ...,
        cursor: str | None = ...,
        *,
        profile: VoiceProfileOut | None = ...,
    ) -> Any: ...


# ---------------------------------------------------------------- pool index (per voice.db reader)


@dataclass(frozen=True)
class PoolEntry:
    song_key: str
    artist: str
    title: str
    artist_key: str
    stratum: str
    popularity: int
    duration_ms: int | None
    demand: SongDemand
    versions: tuple[Version, ...]


_index_lock = threading.Lock()
_index_cache: tuple[VoiceReader, dict[str, PoolEntry]] | None = None


def _build_index(reader: VoiceReader) -> dict[str, PoolEntry]:
    out: dict[str, PoolEntry] = {}
    for song in reader.pool_songs(reader.pool_keys()):
        a = reader.analysis(song.song_key)
        if a is None or None in (a.low, a.high, a.p10, a.p50, a.p90):
            continue
        assert a.low is not None and a.high is not None and a.p10 is not None  # for mypy
        assert a.p50 is not None and a.p90 is not None
        out[song.song_key] = PoolEntry(
            song_key=song.song_key,
            artist=song.artist,
            title=song.title,
            artist_key=song.artist_key,
            stratum=song.stratum or "",
            popularity=song.popularity or 0,
            duration_ms=song.duration_ms,
            demand=SongDemand(low=a.low, high=a.high, p10=a.p10, p50=a.p50, p90=a.p90),
            versions=tuple(reader.versions(song.song_key)),
        )
    return out


def pool_index(reader: VoiceReader) -> dict[str, PoolEntry]:
    """The offerable pool songs of this voice.db (built once per reader; a new run swaps the reader)."""
    global _index_cache
    with _index_lock:
        if _index_cache is None or _index_cache[0] is not reader:
            _index_cache = (reader, _build_index(reader))
        return _index_cache[1]


# ---------------------------------------------------------------- versions


def _effective_embeddable(v: Version, stats: dict[str, VersionStats]) -> bool | None:
    s = stats.get(v.video_id)
    return s.embeddable if s is not None and s.embeddable is not None else v.embeddable


def _playable(versions: Iterable[Version], stats: dict[str, VersionStats], skipped: set[str]) -> list[Version]:
    """Versions not known to be unembeddable and not reported unplayable in this session, in order."""
    return [v for v in versions if _effective_embeddable(v, stats) is not False and v.video_id not in skipped]


def _version_out(v: Version, *, embeddable: bool = True) -> VersionOut:
    return VersionOut(video_id=v.video_id, brand_code=v.brand_code, brand_name=v.brand_name, embeddable=embeddable)


def _stats_from_doc(doc: dict[str, Any]) -> VersionStats:
    def num(k: str) -> int:
        v = doc.get(k)
        return int(v) if isinstance(v, int | float) and not isinstance(v, bool) else 0

    emb = doc.get("embeddable")
    return VersionStats(
        good=num("good"),
        not_great=num("not_great"),
        early_exit=num("early_exit"),
        plays=num("plays"),
        embeddable=emb if isinstance(emb, bool) else None,
    )


# ---------------------------------------------------------------- state rebuild (pure)


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _matching_attempt(
    attempts: Sequence[dict[str, Any]], song_key: str, started: datetime | None
) -> dict[str, Any] | None:
    """The stored attempt a request retries: same song_key and started_at."""
    for a in attempts:
        if a["song_key"] == song_key and _as_datetime(a.get("started_at")) == started:
            return a
    return None


def _quiz(d: dict[str, Any]) -> Quiz:
    return Quiz(
        feel=d["feel"],
        position=d["position"],
        difficulty=d.get("difficulty"),
        knew=d.get("knew"),
        would_sing=d.get("would_sing"),
        would_work_if=frozenset(d.get("would_work_if") or ()),
        voice_now=d.get("voice_now"),
        version_quality=d.get("version_quality"),
        version_issues=frozenset(d.get("version_issues") or ()),
        tags=frozenset(d.get("tags") or ()),
    )


def _pitch(d: dict[str, Any] | None) -> PitchSummary | None:
    if not d:
        return None
    return PitchSummary(
        **{k: float(d[k]) for k in ("voiced_s", "held_count", "held_low", "held_high", "p10", "p50", "p90")}
    )


def _demand(d: dict[str, Any]) -> SongDemand:
    return SongDemand(**{k: float(d[k]) for k in ("low", "high", "p10", "p50", "p90")})


def has_range_evidence(quiz: Quiz, pitch: PitchSummary | None) -> bool:
    """Q2 answered (always, the API requires it) and the song known better than "barely" or pitch usable."""
    return quiz.position is not None and (quiz.knew != "barely" or usable_pitch(pitch))


def _prior(session: dict[str, Any]) -> Posterior:
    raw = session.get("prior_profile")
    profile: VoiceProfileOut | None = None
    if raw:
        try:
            profile = VoiceProfileOut.model_validate(raw)
        except ValidationError:
            logger.warning(
                "voice session %s: stored prior profile no longer validates; uniform prior", session.get("id")
            )
    return Posterior.from_profile(profile)


def _done_skip_keys(skips: Sequence[dict[str, Any]]) -> set[str]:
    return {s["song_key"] for s in skips if s.get("done")}


def rebuild_state(session: dict[str, Any], attempts: Sequence[dict[str, Any]]) -> SessionState:
    """C2's SessionState from the session doc and its attempt docs (see the module doc for the mapping)."""
    post = _prior(session)
    setup = session.get("setup") or {}
    state = SessionState(
        posterior=post,
        warmth=setup.get("warmth") or "cold",
        attempts=0,
        range_attempts=0,
        tired_count=0,
        comfort_history=[post.comfort_estimate()],
    )
    for a in sorted(attempts, key=lambda a: int(a["n"])):
        quiz, pitch = _quiz(a["quiz"]), _pitch(a.get("pitch"))
        c = a.get("context") or {}
        ctx = Context(posture=c.get("posture") or "standing", warmth=c.get("warmth") or "cold",
                      headphones=bool(c.get("headphones")))  # fmt: skip
        song = a["song"]
        ev = Evidence(_demand(song["demand"]), quiz, ctx, pitch, stopped_early=bool(a.get("stopped_early")))
        state.posterior = state.posterior.update(ev)
        state.attempts += 1
        state.range_attempts += int(has_range_evidence(quiz, pitch))
        state.tired_count += int(quiz.voice_now == "tired")
        state.done_keys.add(a["song_key"])
        state.recent_strata.append(song.get("stratum") or "")
        state.comfort_history.append(state.posterior.comfort_estimate())
    state.done_keys |= _done_skip_keys(session.get("skips") or [])
    return state


def _ever_warm(session: dict[str, Any], attempts: Sequence[dict[str, Any]]) -> bool:
    if (session.get("setup") or {}).get("warmth") == "warm":
        return True
    return any((a.get("context") or {}).get("warmth") == "warm" for a in attempts)


def _top_p(state: SessionState) -> float:
    return max(state.posterior.type_marginal().values())


# ---------------------------------------------------------------- candidates (pure)


@dataclass(frozen=True)
class _Taste:
    known: set[str]
    loved: set[str]
    artists: dict[str, float]
    hidden: set[str]


def _taste(ctx: UserContextV2) -> _Taste:
    return _Taste(
        known=set(ctx.known_songs),
        loved={k for k, w in ctx.known_songs.items() if w >= recs_weights.SONG_MANUAL},
        artists={k: a.weight for k, a in ctx.artists.items()},
        hidden=set(ctx.hidden),
    )


def _skip_identity(s: dict[str, Any]) -> tuple[Any, ...]:
    """Skips with the same identity are retries of one request (see ``VoiceSessionService.skip``)."""
    return (s.get("song_key"), s.get("reason"), s.get("video_id"), bool(s.get("link_out")))


def _session_unplayable(skips: Sequence[dict[str, Any]], song_key: str | None = None) -> set[str]:
    """Video ids reported unplayable in this session (optionally for one song)."""
    return {
        str(s["video_id"])
        for s in skips
        if s.get("reason") == "not_embeddable" and s.get("video_id") and (song_key is None or s["song_key"] == song_key)
    }


def build_candidates(
    index: dict[str, PoolEntry],
    taste: _Taste,
    state: SessionState,
    skips: Sequence[dict[str, Any]],
    exclude: set[str],
) -> list[CandidateSong]:
    """Pool songs minus hidden / done / excluded ones and songs with no playable version (voice.db verdicts)."""
    dont_know = Counter(s.get("artist_key") or "" for s in skips if s.get("reason") == "dont_know")
    unplayable = _session_unplayable(skips)
    artists = set(taste.artists)
    out = []
    for key, e in index.items():
        if key in taste.hidden or key in state.done_keys or key in exclude:
            continue
        if not _playable(e.versions, {}, unplayable):
            continue
        out.append(
            CandidateSong(
                song_key=key,
                artist_key=e.artist_key,
                demand=e.demand,
                stratum=e.stratum,
                know=know_weight(
                    key,
                    e.artist_key,
                    known_songs=taste.known,
                    loved_songs=taste.loved,
                    artists=artists,
                    dont_know_skips=dont_know,
                ),  # fmt: skip
                pref=taste.artists.get(e.artist_key, 0.0),
                popularity=e.popularity,
                loved=key in taste.loved,
            )
        )
    return out


# ---------------------------------------------------------------- service


class VoiceSessionService:
    def __init__(
        self,
        firestore: FirestoreService,
        voice: VoiceStoreProvider,
        catalog: CatalogProvider,
        feedback: SessionFeedbackProvider,
        profiles: VoiceProfileProvider,
        recs: SuitedRecommender,
    ) -> None:
        self._fs = firestore
        self._voice = voice
        self._catalog = catalog
        self._feedback = feedback
        self._profiles = profiles
        self._recs = recs

    # ------------------------------------------------------------ docs

    async def _owned(self, user_id: str, session_id: str) -> dict[str, Any]:
        doc = await self._fs.get_document(SESSIONS_COLLECTION, session_id)
        if not doc or doc.get("user_id") != user_id:
            raise SessionNotFoundError(session_id)
        return doc

    async def _active(self, user_id: str, session_id: str) -> dict[str, Any]:
        doc = await self._owned(user_id, session_id)
        if doc.get("state") != "active":
            raise SessionNotActiveError(session_id)
        return doc

    async def _attempts(self, user_id: str, session_id: str) -> list[dict[str, Any]]:
        return await self._fs.query_documents(
            ATTEMPTS_COLLECTION, filters=[("session_id", "==", session_id), ("user_id", "==", user_id)]
        )

    async def _update(self, session_id: str, data: dict[str, Any]) -> None:
        await self._fs.update_document(SESSIONS_COLLECTION, session_id, {**data, "updated_at": datetime.now(UTC)})

    async def _index(self) -> dict[str, PoolEntry]:
        reader = self._voice.require()  # VoiceDataUnavailable -> 503 warming_up
        return await asyncio.to_thread(pool_index, reader)

    async def _version_stats(self, versions: Sequence[Version]) -> dict[str, VersionStats]:
        docs = await asyncio.gather(*(self._fs.get_document(VERSION_STATS_COLLECTION, v.video_id) for v in versions))
        return {v.video_id: _stats_from_doc(d) for v, d in zip(versions, docs, strict=True) if d}

    async def _context(self, user_id: str) -> UserContextV2:
        """3a's user context; without catalog.db only the hidden songs are known."""
        try:
            reader = await asyncio.to_thread(self._catalog.require)
        except CatalogUnavailable:
            try:
                hidden = set(await self._feedback.hidden_keys(user_id))
            except Exception:
                logger.warning("hidden_keys failed for %s; continuing with none hidden", user_id, exc_info=True)
                hidden = set()
            return UserContextV2(user_id=user_id, hidden=hidden)
        return await build_context(self._fs, reader, self._feedback, user_id)

    # ------------------------------------------------------------ setup

    async def create(self, user: User, setup: SessionSetup) -> SessionCreated:
        """A new active session; the user's other active sessions become ``abandoned``."""
        now = datetime.now(UTC)
        active = await self._fs.query_documents(
            SESSIONS_COLLECTION, filters=[("user_id", "==", user.id), ("state", "==", "active")]
        )
        for d in active:
            await self._fs.update_document(SESSIONS_COLLECTION, d["id"], {"state": "abandoned", "updated_at": now})
        profile = await self._profiles.get(user.id)
        session_id = uuid.uuid4().hex
        await self._fs.set_document(
            SESSIONS_COLLECTION,
            session_id,
            {
                "user_id": user.id,
                "setup": setup.model_dump(),
                "state": "active",
                "created_at": now,
                "updated_at": now,
                "tired_count": 0,
                "skips": [],
                "prior_profile": profile.model_dump(mode="json", by_alias=True) if profile else None,
                "pending_song_key": None,
                "last_offer": None,
                "done_reason": None,
            },
        )
        return SessionCreated(session_id=session_id, state="active")

    async def patch(self, user: User, session_id: str, patch: SessionPatch) -> None:
        session = await self._active(user.id, session_id)
        changes = {k: v for k, v in patch.model_dump(exclude_unset=True).items() if v is not None or k == "device"}
        await self._update(session_id, {"setup": {**(session.get("setup") or {}), **changes}})

    # ------------------------------------------------------------ next

    async def next_song(self, user: User, session_id: str) -> NextSongOut | NextDoneOut:
        session = await self._active(user.id, session_id)
        index = await self._index()
        attempts, ctx = await asyncio.gather(self._attempts(user.id, session_id), self._context(user.id))
        state = await asyncio.to_thread(rebuild_state, session, attempts)
        done = check(state)
        if done is not None:
            return await self._done(session_id, done)

        skips: list[dict[str, Any]] = session.get("skips") or []
        taste = _taste(ctx)
        pending = session.get("pending_song_key")
        if pending in index and pending not in state.done_keys and pending not in taste.hidden:
            last = session.get("last_offer") or {}
            reason = last.get("reason_type") if last.get("song_key") == pending else "explore"
            offer = await self._offer(session_id, index[pending], reason or "explore", skips, link_out_ok=True)
            assert offer is not None  # link_out_ok always offers
            return offer

        exclude: set[str] = set()
        for attempt in range(MAX_REPICKS + 1):
            cands = await asyncio.to_thread(build_candidates, index, taste, state, skips, exclude)
            pick = await asyncio.to_thread(choose_next, state, cands)
            if pick is None:
                return await self._done(session_id, "no_candidates")
            offer = await self._offer(
                session_id, index[pick.song_key], pick.reason_type, skips, link_out_ok=attempt == MAX_REPICKS
            )
            if offer is not None:
                return offer
            exclude.add(pick.song_key)  # live stats: no embeddable version after all
        raise AssertionError("unreachable: the last pick always offers")  # pragma: no cover

    async def _done(self, session_id: str, reason: DoneReason) -> NextDoneOut:
        await self._update(session_id, {"done_reason": reason})
        return NextDoneOut(reason=reason)

    async def _offer(
        self, session_id: str, e: PoolEntry, reason_type: Any, skips: Sequence[dict[str, Any]], *, link_out_ok: bool
    ) -> NextSongOut | None:
        """The song with its best playable version (live stats), a link-out offer, or None (no version)."""
        stats = await self._version_stats(e.versions)
        ordered = choose_versions(list(e.versions), stats)
        playable = _playable(ordered, stats, _session_unplayable(skips, e.song_key))
        if not playable and not link_out_ok:
            return None
        link_out = not playable
        version = _version_out(ordered[0], embeddable=False) if link_out else _version_out(playable[0])
        await self._update(
            session_id, {"last_offer": {"song_key": e.song_key, "reason_type": reason_type, "link_out": link_out}}
        )
        d = e.demand
        return NextSongOut(
            song_key=e.song_key,
            artist=e.artist,
            title=e.title,
            duration_s=round(e.duration_ms / 1000) if e.duration_ms else None,
            reason_type=reason_type,
            version=version,
            alternatives=[_version_out(v) for v in playable[1 : 1 + ALTERNATIVES]],
            song_range=SongRangeOut(low=d.low, high=d.high, p10=d.p10, p50=d.p50, p90=d.p90),
        )

    # ------------------------------------------------------------ skips

    async def skip(self, user: User, session_id: str, body: SkipIn) -> None:
        """Record a skip. "dont_know" / "something_else" finish the song for this session.

        "not_embeddable" marks ``voice_version_stats/{video_id}.embeddable = false`` and keeps the song
        (the next call offers its next version) while a playable version is left; with none left the song
        is offered once more as a link-out, and a "not_embeddable" skip of that link-out finishes it.
        """
        await self._active(user.id, session_id)
        index = await self._index()
        e = index.get(body.song_key)
        if e is None:
            raise UnknownSongError(body.song_key)
        stats: dict[str, VersionStats] = {}
        if body.reason == "not_embeddable":
            if body.video_id not in {v.video_id for v in e.versions}:
                raise UnknownVersionError(str(body.video_id))
            await self._fs.set_document(VERSION_STATS_COLLECTION, body.video_id, {"embeddable": False}, merge=True)
            stats = await self._version_stats(e.versions)

        async def record(txn: Any) -> None:
            session = await txn.get(SESSIONS_COLLECTION, session_id)
            if not session or session.get("user_id") != user.id:
                raise SessionNotFoundError(session_id)
            if session.get("state") != "active":
                raise SessionNotActiveError(session_id)
            skips: list[dict[str, Any]] = list(session.get("skips") or [])
            last = session.get("last_offer") or {}
            entry: dict[str, Any] = {
                "song_key": e.song_key,
                "reason": body.reason,
                "video_id": body.video_id,
                "artist_key": e.artist_key,
                # A not_embeddable skip of the link-out offer is a new skip, not a retry of the one before it.
                "link_out": last.get("song_key") == e.song_key and bool(last.get("link_out")),
                "at": datetime.now(UTC),
                "done": True,
            }
            if any(_skip_identity(s) == _skip_identity(entry) for s in skips):
                return  # a retried request: already recorded
            pending = session.get("pending_song_key")
            if body.reason == "not_embeddable":
                unplayable = _session_unplayable(skips, e.song_key) | {str(body.video_id)}
                keep = bool(_playable(e.versions, stats, unplayable)) or not entry["link_out"]
                entry["done"] = not keep
                pending = e.song_key if keep else (None if pending == e.song_key else pending)
            elif pending == e.song_key:
                pending = None
            txn.set(
                SESSIONS_COLLECTION,
                session_id,
                {**session, "skips": [*skips, entry], "pending_song_key": pending, "updated_at": datetime.now(UTC)},
            )

        await self._fs.run_transaction(record)

    # ------------------------------------------------------------ attempts

    async def add_attempt(self, user: User, session_id: str, body: AttemptIn) -> AttemptOut:
        """Store an attempt and return the rebuilt state. Idempotent by (session, song_key, started_at)."""
        session = await self._active(user.id, session_id)
        attempts = await self._attempts(user.id, session_id)
        started = _as_datetime(body.started_at)
        if (same := _matching_attempt(attempts, body.song_key, started)) is not None:
            return await self._attempt_out(user.id, session, attempts, same)

        index = await self._index()
        e = index.get(body.song_key)
        if e is None:
            raise UnknownSongError(body.song_key)
        if body.video_id not in {v.video_id for v in e.versions}:
            raise UnknownVersionError(body.video_id)
        d = e.demand
        doc: dict[str, Any] = {
            "user_id": user.id,
            "session_id": session_id,
            "song_key": e.song_key,
            "video_id": body.video_id,
            "quiz": body.quiz.model_dump(),
            "context": dict(session.get("setup") or {}),
            "pitch": body.pitch.model_dump() if body.pitch else None,
            "stopped_early": body.stopped_early,
            "started_at": body.started_at,
            "ended_at": body.ended_at,
            "recording_id": None,
            "song": {
                "artist": e.artist,
                "title": e.title,
                "artist_key": e.artist_key,
                "stratum": e.stratum,
                "demand": {"low": d.low, "high": d.high, "p10": d.p10, "p50": d.p50, "p90": d.p90},
            },
        }
        for _ in range(_CREATE_RETRIES):
            n = max((int(a["n"]) for a in attempts), default=0) + 1
            if n > MAX_ATTEMPTS:
                raise AttemptLimitError(session_id)
            doc_id = f"{session_id}:{n}"
            if await self._fs.create_document(ATTEMPTS_COLLECTION, doc_id, {**doc, "n": n}):
                attempts.append({"id": doc_id, **doc, "n": n})
                break
            attempts = await self._attempts(user.id, session_id)  # a concurrent attempt took n
            if (same := _matching_attempt(attempts, body.song_key, started)) is not None:
                return await self._attempt_out(user.id, session, attempts, same)  # ...this very attempt, retried
        else:
            raise AttemptLimitError(session_id)  # pragma: no cover  (3 concurrent writers)

        tired = sum(1 for a in attempts if (a.get("quiz") or {}).get("voice_now") == "tired")
        pending = session.get("pending_song_key")
        await self._update(
            session_id,
            {"tired_count": tired, "pending_song_key": None if pending == e.song_key else pending},
        )
        return await self._attempt_out(user.id, session, attempts, attempts[-1])

    async def _attempt_out(
        self, user_id: str, session: dict[str, Any], attempts: list[dict[str, Any]], attempt: dict[str, Any]
    ) -> AttemptOut:
        """Apply the attempt's outputs (once) and return the rebuilt state."""
        await self._apply_outputs(user_id, session, attempts, attempt)
        state = await asyncio.to_thread(rebuild_state, session, attempts)
        done = check(state)
        return AttemptOut(
            attempt_id=str(attempt.get("id") or f"{attempt['session_id']}:{attempt['n']}"),
            n=int(attempt["n"]),
            done=done is not None,
            done_reason=done,
            top_p=round(_top_p(state), 4),
        )

    # ------------------------------------------------------------ outputs

    async def _kn_id(self, song_key: str) -> int | None:
        try:
            reader = await asyncio.to_thread(self._catalog.require)
        except CatalogUnavailable:
            return None
        return await asyncio.to_thread(kn_id_for_song_key, reader, song_key)

    def _key_hint(
        self, session: dict[str, Any], attempts: Sequence[dict[str, Any]], attempt: dict[str, Any]
    ) -> KeyHint | None:
        """The attempt's key hint; the direction is judged on the posterior up to and including it."""

        def direction() -> Literal["down", "up"]:
            upto = [a for a in attempts if int(a["n"]) <= int(attempt["n"])]
            post = rebuild_state(session, upto).posterior
            return key_shift_direction(post, _demand(attempt["song"]["demand"]))

        return key_hint(attempt["quiz"], direction)

    async def _apply_outputs(
        self, user_id: str, session: dict[str, Any], attempts: Sequence[dict[str, Any]], attempt: dict[str, Any]
    ) -> None:
        """user_songs row + version stats for one attempt, exactly once; then "Love it" for a loved song."""
        if attempt.get("outputs_applied"):
            return
        attempt_id = f"{attempt['session_id']}:{attempt['n']}"
        song_key, video_id = attempt["song_key"], attempt["video_id"]
        hint = await asyncio.to_thread(self._key_hint, session, attempts, attempt)
        kn_id = await self._kn_id(song_key)

        async def body(txn: Any) -> bool:
            current = await txn.get(ATTEMPTS_COLLECTION, attempt_id)
            if current is None or current.get("outputs_applied"):
                return False
            row_id, row = await choose_user_song_row(user_id, song_key, kn_id, txn.get)
            stats = await txn.get(VERSION_STATS_COLLECTION, video_id)
            now = datetime.now(UTC).isoformat()  # all reads done; writes from here on
            txn.set(
                USER_SONGS_COLLECTION,
                row_id,
                user_song_row(row, user_id=user_id, row_id=row_id, attempt=attempt, hint=hint, now=now),
            )
            txn.set(
                VERSION_STATS_COLLECTION,
                video_id,
                bump_version_stats(stats, attempt["quiz"], bool(attempt.get("stopped_early"))),
            )
            txn.set(
                ATTEMPTS_COLLECTION,
                attempt_id,
                {**{k: v for k, v in current.items() if k != "id"}, "outputs_applied": True},
            )
            return True

        applied = await self._fs.run_transaction(body)
        if applied and attempt["quiz"].get("feel") == "loved":
            song = attempt.get("song") or {}
            try:
                await self._feedback.love(user_id, song_key, song.get("artist", ""), song.get("title", ""))
            except Exception:
                logger.warning("voice session: love failed for %s on %s", user_id, song_key, exc_info=True)

    # ------------------------------------------------------------ result

    async def result(self, user: User, session_id: str) -> ResultOut:
        """The result for any session state (zero attempts: the prior, partial); marks the session done."""
        session = await self._owned(user.id, session_id)
        attempts = await self._attempts(user.id, session_id)
        state = await asyncio.to_thread(rebuild_state, session, attempts)
        done_reason: DoneReason | None = check(state) or session.get("done_reason")
        # summary() reads "not warm now" as "never warm"; warmth can step down mid-session, so use ever-warm.
        state.warmth = "warm" if _ever_warm(session, attempts) else state.warmth
        s = await asyncio.to_thread(summary, state, done_reason)
        profile, saved = await self._save_profile(user.id, session_id, s, state.range_attempts)
        suited = await self._suited(user, profile)
        loved: dict[str, LovedSongOut] = {}
        for a in sorted(attempts, key=lambda a: int(a["n"])):
            if (a.get("quiz") or {}).get("feel") == "loved":
                song = a.get("song") or {}
                loved.setdefault(
                    a["song_key"],
                    LovedSongOut(song_key=a["song_key"], artist=song.get("artist", ""), title=song.get("title", "")),
                )
        await self._update(session_id, {"state": "done", "done_reason": done_reason})
        return ResultOut(
            voice_type=s.voice_type,
            between=s.between,
            confidence_label=s.confidence_label,
            top_p=round(s.top_p, 4),
            low=s.full[0],
            high=s.full[1],
            comfort_low=s.comfort[0],
            comfort_high=s.comfort[1],
            attempts=state.attempts,
            partial=done_reason != "confident",
            improve_hint=s.improve_hint,
            profile_saved=saved,
            suited=suited,
            loved=list(loved.values()),
        )

    async def _save_profile(
        self, user_id: str, session_id: str, s: ResultSummary, evidence_count: int
    ) -> tuple[VoiceProfileOut | None, bool]:
        """(the profile the result stands for, whether it was saved).

        The session's profile is saved over no profile or a self-reported one, and over a measured one
        unless its confidence is 'low'. Unsaved, it is still the result's profile (for ``suited``); with
        no range evidence there is none and the stored profile stands."""
        existing = await self._profiles.get(user_id)
        now = datetime.now(UTC)
        derived = session_profile(s, evidence_count, session_id, now)
        if derived is None:
            return existing, False
        if should_save(s.confidence_label, existing):
            try:
                return await self._profiles.put(user_id, derived), True
            except VoiceProfileUserNotFoundError:
                logger.warning("voice session %s: no decide_users doc for %s; profile not saved", session_id, user_id)
        return VoiceProfileOut(**derived.model_dump(by_alias=True), updated_at=now), False

    async def _suited(self, user: User, profile: VoiceProfileOut | None) -> list[RecSong]:
        """recs v2 ``singable`` + ``sort=fit`` for ``profile``; [] without one."""
        if profile is None:
            return []
        try:
            res = await self._recs.recommend(user, RecFilters(singable=True), sort="fit", profile=profile)
        except Exception:
            logger.warning("voice session result: suited songs unavailable for %s", user.id, exc_info=True)
            return []
        songs: list[RecSong] = [s for sec in res.sections for s in sec.songs]
        return songs[:SUITED_LIMIT]
