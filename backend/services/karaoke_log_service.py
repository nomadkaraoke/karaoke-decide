"""Karaoke log: every song a Nomad Karaoke user sings, plus the optional post-song survey.

Records live in ``karaoke_log/{user_id}:{id}`` (namespaced per user, so a guessed ``kj_`` id
can never block or touch another singer's record); the API exposes ``id`` alone:

* ``kj_…`` ids come from kjbox (server to server, ``upsert_partner`` / ``delete_partner``).
  kjbox owns the facts (state, artist/title, times, night, media); the user may set only
  ``survey`` and ``deleted``. Either side may write first: a survey written before kjbox
  delivers creates a placeholder (``state`` None) that the facts fill in later.
* ``app_…`` ids are logged by the mobile app at any other bar (``upsert_user``); the user
  owns every field.

Counters. A record "counts" while its state is ``sung``/``sung_unplayed``, it isn't deleted
and it has artist + title. A counting record with a resolved ``song_key`` adds one to
``user_songs.times_sung``: the row ``{user_id}:{kn_id}`` when catalog.db maps the key to a
KaraokeNerds id and that row exists, else ``loved_doc_id(user_id, song_key)`` (created with
``source: karaoke_log`` when missing; the rule is ``song_feedback_service.choose_user_song_row``,
shared with "Love it", and a loved row is never deleted when its count drops to 0). Unresolved records count in
``user_karaoke_unresolved/{user_id}:{sha1(song_key(artist, title))[:24]}`` instead, so
nothing a singer sang goes missing. The log doc remembers which counter doc it added to
(``counted``) and every change runs in one Firestore transaction, so a transition counts
exactly once and retries, redo/undo and re-resolution never double-count.

Survey state (Phase 4b). The same transaction keeps the resolved song's ``user_songs`` row's
``sung_surveys`` map (``{entry_id: {feel, key, crowd_pleaser, at: {field: answered_at}, bucket}}``,
one entry per counting record that answers any of them; an edit restamps only the fields whose
value changed, and ``bucket`` is the voice bucket when that entry's key was set) and derives
``sung_feel_counts``, ``sung_feel_latest`` / ``sung_key_latest`` (per field, latest by
``answered_at``), ``sung_crowd_pleaser_count`` and ``crowd_bucket`` (the latest key entry's
bucket) from it on every write. The singer's one crowd vote per song (latest key + bucket, and
latest crowd-pleaser answer) moves in ``song_crowd_stats`` (``crowd_stats``) by its exact
before/after. Unresolved songs carry neither; a backfill record counts toward the crowd only
with a key answer.

Resolution (artist + title to ``song_key``) runs inline with a 300 ms budget and never
blocks the write; records left unresolved are retried by ``retry_unresolved(user_id)``.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from pydantic import ValidationError

from backend.models.karaoke_log import KaraokeLogEntry, LogMedia, LogNight, Survey
from backend.services.catalog_lookup import CatalogLookup, get_catalog_lookup
from backend.services.crowd_stats import CROWD_COLLECTION, Vote, apply_pleaser, apply_vote, voice_bucket
from backend.services.song_feedback_service import (
    USER_SONGS_COLLECTION,
    choose_user_song_row,
    is_loved,
    kn_id_for_song_key,
    resolve_song_keys,
)
from backend.services.voice_profile_service import FIELD as VOICE_PROFILE_FIELD
from backend.services.voice_profile_service import USERS_COLLECTION, find_user_doc
from karaoke_decide.song_search.text_normalize import song_key as make_song_key

logger = logging.getLogger(__name__)

LOG_COLLECTION = "karaoke_log"
UNRESOLVED_COLLECTION = "user_karaoke_unresolved"
SUNG_STATES = frozenset({"sung", "sung_unplayed"})
RESOLVE_BUDGET_S = 0.3
DEFAULT_LIST_LIMIT = 100
MAX_LIST_LIMIT = 500

# Who may write which fields (spec 7.2).
USER_APP_FIELDS = frozenset(
    {
        "state",
        "artist",
        "title",
        "song_key",
        "night",
        "sung_at",
        "ended_at",
        "media",
        "survey",
        "deleted",
        "origin",
        "times",
    }
)
USER_KJ_FIELDS = frozenset({"survey", "deleted"})
PARTNER_FIELDS = frozenset(
    {"state", "artist", "title", "sung_at", "ended_at", "night", "media", "singers", "box_id", "entry_id"}
)
# Outbox record keys (spec 5.5) folded into ``night``; identity keys the partner sends but we derive.
PARTNER_NIGHT_KEYS = frozenset({"night_id", "night_date", "venue"})
PARTNER_IGNORED = frozenset({"gen_email", "history_id", "source", "id", "user_id", "email_hash"})

Writer = Literal["user", "partner"]
Target = tuple[str, str]  # (collection, doc_id)


class KaraokeLogError(Exception):
    """Base class; ``code`` is the v1 error code the API maps it to."""

    code = "internal"


class KaraokeLogInvalidError(KaraokeLogError, ValueError):
    code = "invalid"


class NotOwnerError(KaraokeLogError, PermissionError):
    code = "not_owner"


class KaraokeLogNotFoundError(KaraokeLogError, LookupError):
    code = "not_found"


class Transaction(Protocol):
    """``FirestoreService.run_transaction``'s view (see ``TransactionView``)."""

    async def get(self, collection: str, doc_id: str) -> dict[str, Any] | None: ...
    def set(self, collection: str, doc_id: str, data: dict[str, Any]) -> None: ...
    def delete(self, collection: str, doc_id: str) -> None: ...


class LogFirestore(Protocol):
    """The slice of ``FirestoreService`` this service uses."""

    async def get_document(self, collection: str, doc_id: str) -> dict[str, Any] | None: ...
    async def query_documents(
        self, collection: str, filters: list[tuple[str, str, Any]] | None = None, *, limit: int | None = None
    ) -> list[dict[str, Any]]: ...
    async def run_transaction(self, fn: Callable[[Any], Awaitable[Any]]) -> Any: ...


UserResolver = Callable[[str], Awaitable[str]]
UserFinder = Callable[[str], Awaitable[str | None]]


# ---------------------------------------------------------------- pure helpers


def unresolved_doc_id(user_id: str, artist: str, title: str) -> str:
    """``user_karaoke_unresolved`` doc id: spelling variants of one song share it. Never has ``/``."""
    digest = hashlib.sha1(make_song_key(artist, title).encode()).hexdigest()[:24]  # noqa: S324  (id, not security)
    return f"{user_id}:{digest}"


def log_doc_id(user_id: str, entry_id: str) -> str:
    """``karaoke_log`` doc id: namespaced by user, so ids (guessable ``kj_`` ones included) never
    collide across users. The API still exposes ``id = entry_id``."""
    return f"{user_id}:{entry_id}"


def email_hash(email: str) -> str:
    """Same key as ``decide_users/{sha256(lowercase email)}``."""
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


def library_key(doc: dict[str, Any]) -> str:
    """The song's identity in the app's library: its catalog ``song_key`` once resolved,
    else ``"u:"`` + the normalized artist|title (same as the app's ``songKey``)."""
    if doc.get("song_key_status") == "resolved" and doc.get("song_key"):
        return str(doc["song_key"])
    return "u:" + make_song_key(doc.get("artist") or "", doc.get("title") or "")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        text = value.strip()
        if len(text) == 4 and text.isdigit():  # a backfill night's partial date: YYYY, YYYY-MM
            text += "-01-01"
        elif len(text) == 7 and text[4] == "-":
            text += "-01"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as exc:
            raise KaraokeLogInvalidError(f"not an ISO timestamp: {value!r}") from exc
    else:
        raise KaraokeLogInvalidError(f"not an ISO timestamp: {value!r}")
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _iso(value: Any) -> str | None:
    dt = _parse_dt(value)
    return dt.isoformat() if dt is not None else None


def _later(a: Any, b: Any) -> str | None:
    """The later of two timestamps (ISO strings or datetimes) as an ISO string."""
    try:
        da, db = _parse_dt(a), _parse_dt(b)
    except KaraokeLogInvalidError:  # a malformed legacy value loses to a good one
        da, db = None, _parse_dt(b)
    if da is None:
        return db.isoformat() if db else None
    if db is None:
        return da.isoformat()
    return max(da, db).isoformat()


def _check_id(entry_id: str) -> None:
    if not entry_id or "/" in entry_id or len(entry_id) > 128 or not entry_id.startswith(("kj_", "app_")):
        raise KaraokeLogInvalidError("id must start with kj_ or app_")


def _counts(doc: dict[str, Any]) -> bool:
    return doc.get("state") in SUNG_STATES and not doc.get("deleted") and bool(doc.get("artist") and doc.get("title"))


MAX_TIMES = 50
MAX_SINGERS = 6
MAX_SINGER_CHARS = 100


def _clean_singers(value: Any) -> list[str]:
    if value is None:
        return []
    if (
        not isinstance(value, list)
        or len(value) > MAX_SINGERS
        or not all(isinstance(s, str) and len(s) <= MAX_SINGER_CHARS for s in value)
    ):
        raise KaraokeLogInvalidError(
            f"singers must be a list of at most {MAX_SINGERS} names ({MAX_SINGER_CHARS} chars)"
        )
    return list(value)


def _merge_sub(cur: dict[str, Any] | None, patch: Any, field: str) -> dict[str, Any] | None:
    if patch is None:
        return None
    if not isinstance(patch, dict):
        raise KaraokeLogInvalidError(f"{field} must be an object")
    return {**(cur or {}), **patch}


def _clean_survey(survey: dict[str, Any] | None, now: str) -> dict[str, Any] | None:
    if survey is None:
        return None
    merged = {**survey, "answered_at": survey.get("answered_at") or now}
    try:
        model = Survey.model_validate(merged)
    except ValidationError as exc:
        raise KaraokeLogInvalidError(f"survey: {exc.errors()[0]['msg']}") from exc
    out = model.model_dump()
    out["answered_at"] = _iso(model.answered_at)
    return out


def _clean_model(value: dict[str, Any] | None, model: type[LogNight] | type[LogMedia], field: str) -> Any:
    if value is None:
        return None
    try:
        return model.model_validate(value).model_dump()
    except ValidationError as exc:
        raise KaraokeLogInvalidError(f"{field}: {exc.errors()[0]['msg']}") from exc


def _partner_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """Keep kjbox-owned fields; fold the outbox's night_id/night_date/venue into ``night``."""
    out = {k: v for k, v in patch.items() if k in PARTNER_FIELDS}
    unknown = set(patch) - PARTNER_FIELDS - PARTNER_NIGHT_KEYS - PARTNER_IGNORED
    if unknown:  # kjbox may add fields before decide knows them; never fail its delivery for that
        logger.info("karaoke-log partner write: ignoring unknown fields %s", sorted(unknown))
    for field in ("night", "media"):  # merged field by field later; must be objects
        if out.get(field) is not None and not isinstance(out[field], dict):
            raise KaraokeLogInvalidError(f"{field} must be an object")
    if "night" not in out and PARTNER_NIGHT_KEYS & set(patch):
        venue = patch.get("venue")
        if venue is None:
            venue = {}
        elif not isinstance(venue, dict):
            raise KaraokeLogInvalidError("venue must be an object")
        out["night"] = {
            "id": patch.get("night_id"),
            "kind": venue.get("kind") or "nomad",
            "venue_name": venue.get("name"),
            "date": patch.get("night_date"),
        }
    return out


def _user_patch(entry_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    allowed = USER_KJ_FIELDS if entry_id.startswith("kj_") else USER_APP_FIELDS
    extra = set(patch) - allowed
    if extra and entry_id.startswith("kj_") and extra <= USER_APP_FIELDS | PARTNER_FIELDS:
        raise NotOwnerError(f"kjbox owns {sorted(extra)} on this record; you may set survey and deleted")
    if extra:
        raise KaraokeLogInvalidError(f"unknown fields: {sorted(extra)}")
    if "times" in patch and (
        isinstance(patch["times"], bool) or not isinstance(patch["times"], int) or not 1 <= patch["times"] <= MAX_TIMES
    ):
        raise KaraokeLogInvalidError(f"times must be a whole number from 1 to {MAX_TIMES}")
    return dict(patch)


# ---------------------------------------------------------------- counter docs


def _times(doc: dict[str, Any] | None) -> int:
    """How many sings a log record counts for (backfill records may say more than one)."""
    try:
        return max(1, int((doc or {}).get("times") or 1))
    except (TypeError, ValueError):
        return 1


def _inc(doc: dict[str, Any] | None, target: Target, new: dict[str, Any], now: str, times: int = 1) -> dict[str, Any]:
    when = new.get("sung_at") or new.get("ended_at")
    if not when and new.get("origin") == "backfill":
        # An old remembered sing is dated by its night (or undated), never "now": `_later(old, None)`
        # keeps the row's real last_sung_at.
        when = (new.get("night") or {}).get("date") or None
        try:
            _parse_dt(when)
        except KaraokeLogInvalidError:  # night.date is free text: unparseable means undated
            when = None
    elif not when:
        when = now
    if doc is None:
        doc = {
            "user_id": new["user_id"],
            "artist": new["artist"],
            "title": new["title"],
            "times_sung": 0,
            "last_sung_at": None,
            "created_at": now,
        }
        if target[0] == USER_SONGS_COLLECTION:
            doc.update(
                {
                    "id": target[1],
                    "song_id": new["song_key"],
                    "source": "karaoke_log",
                    "play_count": 0,
                    "is_saved": False,
                }
            )
        else:
            doc["norm_key"] = make_song_key(new["artist"], new["title"])
    else:
        doc = dict(doc)
        doc["artist"] = doc.get("artist") or new["artist"]
        doc["title"] = doc.get("title") or new["title"]
    if target[0] == USER_SONGS_COLLECTION:
        doc["song_key"] = new["song_key"]
    doc["times_sung"] = int(doc.get("times_sung") or 0) + times
    doc["last_sung_at"] = _later(doc.get("last_sung_at"), when)
    doc["updated_at"] = now
    return doc


def _dec(doc: dict[str, Any], target: Target, now: str, times: int = 1) -> dict[str, Any] | None:
    """The decremented doc, or None to delete it (a counter doc the log alone created)."""
    left = max(0, int(doc.get("times_sung") or 0) - times)
    if left == 0 and (target[0] == UNRESOLVED_COLLECTION or (doc.get("source") == "karaoke_log" and not is_loved(doc))):
        return None
    out = {**doc, "times_sung": left, "updated_at": now}
    if left == 0:
        out["last_sung_at"] = None
    return out


# ---------------------------------------------------------------- per-song survey state (user_songs rows)

SURVEY_FIELDS = ("feel", "key", "crowd_pleaser")
FEELS = ("great", "ok", "bad")
Latest = tuple[str, str, Any]  # (answered_at, entry_id, value)


def _key_is_new(doc: dict[str, Any], prev: dict[str, Any] | None) -> bool:
    """True when the record's key answer is set and differs from its previous entry's (so the
    singer's bucket is recorded now)."""
    key = (doc.get("survey") or {}).get("key")
    return key is not None and (prev or {}).get("key") != key


def _survey_entry(doc: dict[str, Any], prev: dict[str, Any] | None, bucket: str | None) -> dict[str, Any] | None:
    """The record's ``sung_surveys`` entry, or None when it answers none of feel/key/crowd_pleaser.

    Per field (R3): a value unchanged from ``prev`` (the record's previous entry) keeps its old
    ``at``; a changed or newly set one gets the survey's ``answered_at``. ``bucket`` (the voice
    bucket when the key was set) likewise stays with an unchanged key and is ``bucket`` for a new one."""
    survey = doc.get("survey") or {}
    values: dict[str, Any] = {f: survey.get(f) for f in SURVEY_FIELDS}
    if all(v is None for v in values.values()):
        return None
    prev = prev or {}
    prev_at = prev.get("at") or {}
    answered = survey.get("answered_at")
    at = {
        f: prev_at[f] if prev.get(f) == v and prev_at.get(f) else answered for f, v in values.items() if v is not None
    }
    entry: dict[str, Any] = {**values, "at": at}
    if values["key"] is None:
        entry["bucket"] = None
    else:
        entry["bucket"] = bucket if _key_is_new(doc, prev) else prev.get("bucket")
    if doc.get("origin") == "backfill":
        entry["backfill"] = True
    return entry


def _crowd_eligible(entry: dict[str, Any]) -> bool:
    """A backfill record counts toward the crowd only when it carries a key answer."""
    return not entry.get("backfill") or entry.get("key") is not None


def _latest(
    surveys: dict[str, Any], field: str, eligible: Callable[[dict[str, Any]], bool] | None = None
) -> Latest | None:
    """The newest answer to ``field`` by its ``answered_at`` (ties: larger entry id)."""
    best: Latest | None = None
    for eid, entry in surveys.items():
        value = entry.get(field)
        if value is None or (eligible is not None and not eligible(entry)):
            continue
        cand = (str((entry.get("at") or {}).get(field) or ""), str(eid), value)
        if best is None or cand[:2] > best[:2]:
            best = cand
    return best


def _surveys(row: dict[str, Any] | None) -> dict[str, Any]:
    return dict((row or {}).get("sung_surveys") or {})


def _derive_survey_fields(row: dict[str, Any]) -> None:
    """Recompute the row's sung_* fields from its ``sung_surveys`` map (in place).

    ``crowd_bucket`` is the bucket stored with the latest key answer's entry, so falling back to
    an older answer uses that answer's bucket."""
    surveys = _surveys(row)
    counts = dict.fromkeys(FEELS, 0)
    for entry in surveys.values():
        if entry.get("feel") in counts:
            counts[entry["feel"]] += 1
    feel = _latest(surveys, "feel")
    key = _latest(surveys, "key")
    row["sung_surveys"] = surveys
    row["sung_feel_counts"] = counts
    row["sung_feel_latest"] = feel[2] if feel else None
    row["sung_key_latest"] = key[2] if key else None
    row["sung_crowd_pleaser_count"] = sum(1 for e in surveys.values() if e.get("crowd_pleaser") is True)
    row["crowd_bucket"] = surveys[key[1]].get("bucket") if key else None


def _needs_profile(pre: dict[str, Any] | None, patch_has_survey: bool) -> bool:
    """Whether a write may set a key answer on a song row (only then is the bucket needed)."""
    return patch_has_survey or ((pre or {}).get("survey") or {}).get("key") is not None


def _crowd_vote(row: dict[str, Any] | None) -> Vote | None:
    if row is None or row.get("sung_key_latest") is None:
        return None
    return row["sung_key_latest"], row.get("crowd_bucket")


def _crowd_pleaser(row: dict[str, Any] | None) -> bool | None:
    hit = _latest(_surveys(row), "crowd_pleaser", _crowd_eligible)
    return bool(hit[2]) if hit else None


def _encode_cursor(doc: dict[str, Any]) -> str:
    raw = json.dumps({"u": doc.get("updated_at"), "id": doc["id"]}).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        raw = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        dt = _parse_dt(raw["u"])
        if dt is None or not isinstance(raw["id"], str):
            raise ValueError("bad cursor")
        return dt, raw["id"]
    except (ValueError, KeyError, TypeError, binascii.Error, KaraokeLogInvalidError) as exc:
        raise KaraokeLogInvalidError("bad cursor") from exc


# ---------------------------------------------------------------- service


class KaraokeLogService:
    def __init__(
        self,
        firestore_service: LogFirestore,
        catalog: CatalogLookup | None = None,
        resolve_user: UserResolver | None = None,
        resolve_budget_s: float = RESOLVE_BUDGET_S,
        find_user: UserFinder | None = None,
    ) -> None:
        """``resolve_user`` maps a gen email to the decide user_id, creating the profile when
        missing (the API passes ``AuthService.resolve_gen_user``); only the partner calls need it."""
        self.db = firestore_service
        self._catalog = catalog
        self._resolve_user = resolve_user
        self._find_user = find_user
        self._budget = resolve_budget_s

    @property
    def catalog(self) -> CatalogLookup:
        if self._catalog is None:
            self._catalog = get_catalog_lookup()
        return self._catalog

    # ------------------------------------------------------------ resolution

    def _resolve_sync(self, artist: str, title: str) -> tuple[str | None, str]:
        if not (artist or "").strip() or not (title or "").strip():
            return None, "unresolved"
        reader = self.catalog.store.reader
        if reader is None:  # catalog.db still loading
            return None, "unresolved"
        hit = self.catalog.match(artist, title)
        (key,) = resolve_song_keys(reader, [(artist, title, hit.id if hit is not None else None)])
        return (key, "resolved") if key else (None, "unresolved")

    async def resolve(self, artist: str, title: str, budget_s: float | None = None) -> tuple[str | None, str]:
        """``(song_key, status)`` for artist + title. Never raises; over budget means unresolved."""
        try:
            work = asyncio.to_thread(self._resolve_sync, artist, title)
            if budget_s is None:
                return await work
            return await asyncio.wait_for(work, timeout=budget_s)
        except TimeoutError:
            logger.info("karaoke-log resolve over %.0f ms budget; left unresolved", (budget_s or 0) * 1000)
        except Exception:
            logger.warning("karaoke-log resolve failed; left unresolved", exc_info=True)
        return None, "unresolved"

    def _kn_id(self, song_key: str) -> int | None:
        return kn_id_for_song_key(self.catalog.store.reader, song_key)

    async def _resolution(
        self, pre: dict[str, Any] | None, patch: dict[str, Any], writer: Writer
    ) -> tuple[str, str, str | None, str] | None:
        """``(artist, title, song_key, status)`` to apply, or None to keep what the doc has."""
        if not ({"artist", "title", "song_key"} & set(patch)) and not (writer == "partner" and pre is None):
            return None
        artist = patch.get("artist", (pre or {}).get("artist")) or ""
        title = patch.get("title", (pre or {}).get("title")) or ""
        if writer == "user" and patch.get("song_key"):
            return artist, title, str(patch["song_key"]), "resolved"
        if (
            pre
            and "song_key" not in patch
            and pre.get("song_key_status") == "resolved"
            and (pre.get("artist"), pre.get("title")) == (artist, title)
        ):
            return None
        key, status = await self.resolve(artist, title, self._budget)
        return artist, title, key, status

    # ------------------------------------------------------------ the transaction

    async def _users_doc_id(self, user_id: str) -> str | None:
        """The singer's ``decide_users`` doc id, by ``voice_profile_service.find_user_doc`` (the
        one rule). A query, so it runs before the transaction."""
        _, doc_id = await find_user_doc(self.db, user_id)
        return doc_id

    @staticmethod
    async def _bucket(txn: Transaction, users_doc_id: str | None) -> str | None:
        """The singer's crowd bucket from their current voice profile (a transactional read)."""
        if not users_doc_id:
            return None
        user = await txn.get(USERS_COLLECTION, users_doc_id)
        return voice_bucket((user or {}).get(VOICE_PROFILE_FIELD))

    async def _transact(
        self,
        doc_id: str,
        build: Callable[[dict[str, Any] | None], dict[str, Any] | None],
        users_doc_id: str | None = None,
    ) -> Any:
        """Read the log doc, ``build`` its new contents (None = no change), move counters, keep the
        song row's survey state and the crowd counters, write.

        ``build`` must be pure: Firestore may run this more than once. ``users_doc_id`` is the
        singer's ``decide_users`` doc (``_users_doc_id``), resolved before the transaction when
        the write may set a key answer; without it a new key answer gets no bucket."""
        kn_cache: dict[str, int | None] = {}

        async def kn_for(key: str) -> int | None:
            if key not in kn_cache:  # SQLite: off the event loop
                kn_cache[key] = await asyncio.to_thread(self._kn_id, key)
            return kn_cache[key]

        async def body(txn: Transaction) -> dict[str, Any] | None:
            cur = await txn.get(LOG_COLLECTION, doc_id)
            new = build(cur)
            if new is None:
                return cur
            now = new["updated_at"]
            reads: dict[Target, dict[str, Any] | None] = {}
            new_target: Target | None = None
            if _counts(new):
                user_id = new["user_id"]
                if new.get("song_key_status") == "resolved" and new.get("song_key"):
                    kn = await kn_for(new["song_key"])
                    row_id, row = await choose_user_song_row(user_id, new["song_key"], kn, txn.get)
                    new_target = (USER_SONGS_COLLECTION, row_id)
                    reads[new_target] = row
                else:
                    new_target = (UNRESOLVED_COLLECTION, unresolved_doc_id(user_id, new["artist"], new["title"]))
            counted = (cur or {}).get("counted")
            old_target: Target | None = (counted["collection"], counted["doc_id"]) if counted else None
            old_times = int(counted.get("times") or 1) if counted else 1  # pre-backfill docs: 1
            new_times = _times(new)
            # 1. counters: the new contents of each counter row (None = delete it)
            final: dict[Target, dict[str, Any] | None] = {}
            if (old_target, old_times) != (new_target, new_times):
                for t in (old_target, new_target):
                    if t is not None and t not in reads:
                        reads[t] = await txn.get(*t)
                old_doc = reads[old_target] if old_target is not None else None
                dec = _dec(old_doc, old_target, now, old_times) if old_doc is not None and old_target else None
                if old_target is not None and old_target == new_target:
                    # same row, new times: one write (a row the log created must not vanish between)
                    final[old_target] = _inc(dec, old_target, new, now, new_times)
                else:
                    if old_target is not None and old_doc is not None:
                        final[old_target] = dec
                    if new_target is not None:
                        final[new_target] = _inc(reads[new_target], new_target, new, now, new_times)
            # 2. survey map on the user_songs rows (a survey-only edit still lands here)
            song_rows = [t for t in dict.fromkeys((old_target, new_target)) if t and t[0] == USER_SONGS_COLLECTION]
            prev = next((e for t in song_rows if (e := _surveys(reads.get(t)).get(new["id"])) is not None), None)
            entry = None
            if new_target and new_target[0] == USER_SONGS_COLLECTION:
                bucket = await self._bucket(txn, users_doc_id) if _key_is_new(new, prev) else None
                entry = _survey_entry(new, prev, bucket)
            staged: dict[Target, dict[str, Any]] = {}
            for t in song_rows:
                base = final[t] if t in final else reads.get(t)
                if base is None:  # row deleted by the counters, or never there
                    continue
                row = {**base, "sung_surveys": _surveys(base)}
                if t == new_target and entry is not None:
                    row["sung_surveys"][new["id"]] = entry
                else:
                    row["sung_surveys"].pop(new["id"], None)
                staged[t] = row
            for t, row in staged.items():
                _derive_survey_fields(row)
                if t in final or row != reads.get(t):
                    if t not in final:
                        row["updated_at"] = now
                    final[t] = row
            # 3. crowd: this singer's one vote per song, moved by its exact before/after
            crowd: dict[str, list[tuple[Vote | None, Vote | None, bool | None, bool | None]]] = {}
            for t in song_rows:
                if t not in final:
                    continue
                before, after = reads.get(t), final[t]
                moves = (_crowd_vote(before), _crowd_vote(after), _crowd_pleaser(before), _crowd_pleaser(after))
                song_key = (after or before or {}).get("song_key")
                if song_key and (moves[0] != moves[1] or moves[2] != moves[3]):
                    crowd.setdefault(str(song_key), []).append(moves)
            stats = {k: await txn.get(CROWD_COLLECTION, k) for k in crowd}
            # all reads done; writes from here on
            for t, doc in final.items():
                if doc is None:
                    txn.delete(*t)
                else:
                    txn.set(*t, doc)
            for k, changes in crowd.items():
                s: dict[str, Any] = {**(stats[k] or {}), "song_key": k}
                for old_vote, new_vote, old_pleaser, new_pleaser in changes:
                    s = apply_vote(s, old_vote, new_vote, now)
                    s = apply_pleaser(s, old_pleaser, new_pleaser, now)
                txn.set(CROWD_COLLECTION, k, s)
            if new_target:
                new["counted"] = {"collection": new_target[0], "doc_id": new_target[1], "times": new_times}
            else:
                new["counted"] = None
            txn.set(LOG_COLLECTION, doc_id, new)
            return new

        return await self.db.run_transaction(body)

    def _entry(self, doc: dict[str, Any] | None, entry_id: str) -> KaraokeLogEntry:
        if doc is None:
            raise KaraokeLogNotFoundError(entry_id)
        return KaraokeLogEntry.model_validate({**doc, "id": entry_id})

    @staticmethod
    def _apply_facts(
        cur: dict[str, Any],
        patch: dict[str, Any],
        resolution: tuple[str, str, str | None, str] | None,
        now: str,
    ) -> dict[str, Any]:
        new = dict(cur)
        for field in ("state", "artist", "title", "box_id", "entry_id"):
            if field in patch:
                new[field] = patch[field] if patch[field] is not None or field == "state" else ""
        for field in ("origin", "times"):
            if field in patch:
                new[field] = patch[field]
        for field in ("sung_at", "ended_at"):
            if field in patch:
                new[field] = _iso(patch[field])
        if "singers" in patch:
            new["singers"] = _clean_singers(patch["singers"])
        if "night" in patch:
            new["night"] = _clean_model(_merge_sub(cur.get("night"), patch["night"], "night"), LogNight, "night")
        if "media" in patch:
            new["media"] = _clean_model(_merge_sub(cur.get("media"), patch["media"], "media"), LogMedia, "media")
        if "survey" in patch:
            new["survey"] = _clean_survey(_merge_sub(cur.get("survey"), patch["survey"], "survey"), now)
        if resolution is not None:
            artist, title, key, status = resolution
            if (new.get("artist"), new.get("title")) == (artist, title):
                new["song_key"], new["song_key_status"] = key, status
            else:  # artist/title changed under us (concurrent write): resolve later
                new["song_key"], new["song_key_status"] = None, "unresolved"
        elif {"artist", "title"} & set(patch) and (cur.get("artist"), cur.get("title")) != (
            new.get("artist"),
            new.get("title"),
        ):
            new["song_key"], new["song_key_status"] = None, "unresolved"
        new["updated_at"] = now
        try:
            KaraokeLogEntry.model_validate({**new, "id": new["id"]})
        except ValidationError as exc:
            err = exc.errors()[0]
            raise KaraokeLogInvalidError(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}") from exc
        return new

    # ------------------------------------------------------------ user (app) writes

    async def upsert_user(
        self, user_id: str, entry_id: str, patch: dict[str, Any], *, email: str | None = None
    ) -> KaraokeLogEntry:
        """App write (``PUT /api/my/karaoke-log/{id}``), field-level merge.

        ``app_`` ids: every field. ``kj_`` ids: ``survey`` and ``deleted`` only (else ``NotOwnerError``)."""
        _check_id(entry_id)
        patch = _user_patch(entry_id, patch)
        if "deleted" in patch and not isinstance(patch["deleted"], bool):
            raise KaraokeLogInvalidError("deleted must be true or false")
        is_kj = entry_id.startswith("kj_")
        doc_id = log_doc_id(user_id, entry_id)
        pre = await self.db.get_document(LOG_COLLECTION, doc_id)
        resolution = None if is_kj else await self._resolution(pre, patch, "user")

        def build(cur: dict[str, Any] | None) -> dict[str, Any]:
            now = _now()
            if cur is None:
                if not is_kj and not (patch.get("state") and patch.get("artist") and patch.get("title")):
                    raise KaraokeLogInvalidError("a new app entry needs state, artist and title")
                cur = {
                    "id": entry_id,
                    "user_id": user_id,
                    "source": "kjbox" if is_kj else "app",
                    "state": None,
                    "artist": "",
                    "title": "",
                    "song_key": None,
                    "song_key_status": "unresolved",
                    "created_at": now,
                    "deleted": False,
                    "counted": None,
                }
            cur = dict(cur)
            if email and not cur.get("email_hash"):
                cur["email_hash"] = email_hash(email)
            if patch.get("deleted") is True:
                cur["deleted"], cur["deleted_by"] = True, "user"
            elif patch.get("deleted") is False and cur.get("deleted_by") != "partner":
                # Only the user's own delete is undone here: the host's undo (partner delete) wins
                # over a stale outbox replay or a full-record save that carries deleted: false.
                cur["deleted"], cur["deleted_by"] = False, None
            return self._apply_facts(cur, {k: v for k, v in patch.items() if k != "deleted"}, resolution, now)

        users_doc = await self._users_doc_id(user_id) if _needs_profile(pre, "survey" in patch) else None
        return self._entry(await self._transact(doc_id, build, users_doc), entry_id)

    async def delete(self, user_id: str, entry_id: str) -> KaraokeLogEntry:
        """Soft delete by the user (``DELETE /api/my/karaoke-log/{id}``); reverses its count."""
        _check_id(entry_id)

        def build(cur: dict[str, Any] | None) -> dict[str, Any]:
            if cur is None:
                raise KaraokeLogNotFoundError(entry_id)
            return {**cur, "deleted": True, "deleted_by": "user", "updated_at": _now()}

        return self._entry(await self._transact(log_doc_id(user_id, entry_id), build), entry_id)

    # ------------------------------------------------------------ kjbox (partner) writes

    async def _partner_user(self, email: str) -> str:
        if self._resolve_user is None:
            raise RuntimeError("KaraokeLogService needs resolve_user for partner writes")
        return await self._resolve_user(email.strip().lower())

    async def upsert_partner(self, email: str, entry_id: str, patch: dict[str, Any]) -> KaraokeLogEntry:
        """kjbox write (``PUT /api/partner/kjbox/karaoke-log/{id}``) for the singer with ``email``.

        Idempotent; leaves the user's ``survey`` alone; undoes a partner delete (Done again)
        but never a user's delete."""
        if not entry_id.startswith("kj_"):
            raise KaraokeLogInvalidError("kjbox writes kj_ ids only")
        _check_id(entry_id)
        user_id = await self._partner_user(email)
        ehash = email_hash(email)
        facts = _partner_patch(patch)
        doc_id = log_doc_id(user_id, entry_id)
        pre = await self.db.get_document(LOG_COLLECTION, doc_id)
        resolution = await self._resolution(pre, facts, "partner")
        users_doc = await self._users_doc_id(user_id) if _needs_profile(pre, False) else None

        def build(cur: dict[str, Any] | None) -> dict[str, Any]:
            now = _now()
            if cur is None:
                cur = {
                    "id": entry_id,
                    "user_id": user_id,
                    "state": None,
                    "artist": "",
                    "title": "",
                    "song_key": None,
                    "song_key_status": "unresolved",
                    "created_at": now,
                    "deleted": False,
                    "counted": None,
                }
            cur = {**cur, "source": "kjbox", "email_hash": ehash}
            if cur.get("deleted_by") == "partner":
                cur["deleted"], cur["deleted_by"] = False, None
            return self._apply_facts(cur, facts, resolution, now)

        return self._entry(await self._transact(doc_id, build, users_doc), entry_id)

    async def delete_partner(self, email: str, entry_id: str) -> KaraokeLogEntry | None:
        """kjbox un-done (``DELETE /api/partner/kjbox/karaoke-log/{id}``). Idempotent; None if unknown.

        Never creates a profile: with ``find_user`` set (the API), an email with no decide
        profile is a no-op."""
        if not entry_id.startswith("kj_"):
            raise KaraokeLogInvalidError("kjbox writes kj_ ids only")
        _check_id(entry_id)
        if self._find_user is not None:
            found = await self._find_user(email.strip().lower())
            if found is None:
                return None
            user_id = found
        else:
            user_id = await self._partner_user(email)

        def build(cur: dict[str, Any] | None) -> dict[str, Any] | None:
            if cur is None or cur.get("deleted"):
                return None
            return {**cur, "deleted": True, "deleted_by": "partner", "updated_at": _now()}

        doc = await self._transact(log_doc_id(user_id, entry_id), build)  # removal only: no bucket needed
        return self._entry(doc, entry_id) if doc is not None else None

    # ------------------------------------------------------------ retry + listing

    async def retry_unresolved(self, user_id: str) -> int:
        """Re-resolve the user's unresolved records (call after a catalog refresh or on
        the user's next sync). Moves their counts to ``user_songs``. Returns how many resolved."""
        docs = await self.db.query_documents(LOG_COLLECTION, filters=[("user_id", "==", user_id)])
        resolved = 0
        for doc in docs:
            if doc.get("song_key_status") == "resolved" or not (doc.get("artist") and doc.get("title")):
                continue
            artist, title = doc["artist"], doc["title"]
            key, status = await self.resolve(artist, title)
            if key is None:
                continue
            found: str = key

            def build(cur: dict[str, Any] | None, a: str = artist, t: str = title, k: str = found) -> Any:
                if (
                    cur is None
                    or cur.get("song_key_status") == "resolved"
                    or (cur.get("artist"), cur.get("title")) != (a, t)
                ):
                    return None
                return {**cur, "song_key": k, "song_key_status": "resolved", "updated_at": _now()}

            users_doc = await self._users_doc_id(user_id) if _needs_profile(doc, False) else None
            after = await self._transact(log_doc_id(user_id, doc["id"]), build, users_doc)
            if after is not None and after.get("song_key") == key:
                resolved += 1
        return resolved

    async def list_for_user(
        self,
        user_id: str,
        since: str | datetime | None = None,
        night_id: str | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
        cursor: str | None = None,
        song: str | None = None,
    ) -> tuple[list[KaraokeLogEntry], str | None]:
        """The user's records, newest ``updated_at`` first, and the next page's cursor.

        Without ``since``: live records only. With ``since`` (a sync pull): everything updated
        after it, deleted records included so the app can drop them. Survey-only placeholders
        (kjbox facts not delivered yet) are never listed. One equality query on ``user_id``
        (no composite index); filtering and paging happen here. ``song`` (a ``library_key``)
        keeps one song's records, applied before paging."""
        since_dt = _parse_dt(since)
        after = _decode_cursor(cursor) if cursor else None
        limit = max(1, min(int(limit), MAX_LIST_LIMIT))
        docs = await self.db.query_documents(LOG_COLLECTION, filters=[("user_id", "==", user_id)])
        rows: list[tuple[datetime, str, dict[str, Any]]] = []
        for d in docs:
            if d.get("state") is None:
                continue
            if night_id is not None and (d.get("night") or {}).get("id") != night_id:
                continue
            if song is not None and library_key(d) != song:
                continue
            updated = _parse_dt(d.get("updated_at")) or datetime.min.replace(tzinfo=UTC)
            if since_dt is not None:
                if updated <= since_dt:
                    continue
            elif d.get("deleted"):
                continue
            rows.append((updated, d["id"], d))
        rows.sort(key=lambda r: (r[0], r[1]), reverse=True)
        if after is not None:
            rows = [r for r in rows if (r[0], r[1]) < after]
        page = rows[:limit]
        next_cursor = _encode_cursor(page[-1][2]) if len(rows) > limit else None
        return [self._entry(r[2], r[1]) for r in page], next_cursor
