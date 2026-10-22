"""Karaoke log: every song a Nomad Karaoke user sings, plus the optional post-song survey.

Records live in ``karaoke_log/{id}``:

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
``source: karaoke_log`` when missing). Unresolved and ambiguous records count in
``user_karaoke_unresolved/{user_id}:{sha1(song_key(artist, title))[:24]}`` instead, so
nothing a singer sang goes missing. The log doc remembers which counter doc it added to
(``counted``) and every change runs in one Firestore transaction, so a transition counts
exactly once and retries, redo/undo and re-resolution never double-count.

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
from backend.services.song_feedback_service import USER_SONGS_COLLECTION, loved_doc_id
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
    {"state", "artist", "title", "song_key", "night", "sung_at", "ended_at", "media", "survey", "deleted"}
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
        self, collection: str, filters: list[tuple[str, str, Any]] | None = None
    ) -> list[dict[str, Any]]: ...
    async def run_transaction(self, fn: Callable[[Any], Awaitable[Any]]) -> Any: ...


UserResolver = Callable[[str], Awaitable[str]]


# ---------------------------------------------------------------- pure helpers


def unresolved_doc_id(user_id: str, artist: str, title: str) -> str:
    """``user_karaoke_unresolved`` doc id: spelling variants of one song share it. Never has ``/``."""
    digest = hashlib.sha1(make_song_key(artist, title).encode()).hexdigest()[:24]  # noqa: S324  (id, not security)
    return f"{user_id}:{digest}"


def email_hash(email: str) -> str:
    """Same key as ``decide_users/{sha256(lowercase email)}``."""
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
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
    if "night" not in out and PARTNER_NIGHT_KEYS & set(patch):
        venue = patch.get("venue") or {}
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
    return dict(patch)


# ---------------------------------------------------------------- counter docs


def _inc(doc: dict[str, Any] | None, target: Target, new: dict[str, Any], now: str) -> dict[str, Any]:
    when = new.get("sung_at") or new.get("ended_at") or now
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
    doc["times_sung"] = int(doc.get("times_sung") or 0) + 1
    doc["last_sung_at"] = _later(doc.get("last_sung_at"), when)
    doc["updated_at"] = now
    return doc


def _dec(doc: dict[str, Any], target: Target, now: str) -> dict[str, Any] | None:
    """The decremented doc, or None to delete it (a counter doc the log alone created)."""
    times = max(0, int(doc.get("times_sung") or 0) - 1)
    if times == 0 and (target[0] == UNRESOLVED_COLLECTION or doc.get("source") == "karaoke_log"):
        return None
    out = {**doc, "times_sung": times, "updated_at": now}
    if times == 0:
        out["last_sung_at"] = None
    return out


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
    ) -> None:
        """``resolve_user`` maps a gen email to the decide user_id, creating the profile when
        missing (the API passes ``AuthService.resolve_gen_user``); only the partner calls need it."""
        self.db = firestore_service
        self._catalog = catalog
        self._resolve_user = resolve_user
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
        kn_key: str | None = None
        hit = self.catalog.match(artist, title)
        if hit is not None:
            kn_key = reader.kn_song_keys([hit.id]).get(hit.id)
        direct = make_song_key(artist, title)
        direct_ok = reader.has_karaoke_key(direct)
        if kn_key and direct_ok and kn_key != direct:
            return None, "ambiguous"  # two catalog songs compete; counted separately, retried later
        key = kn_key or (direct if direct_ok else None)
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
        reader = self.catalog.store.reader
        if reader is None:
            return None
        try:
            return reader.kn_ids_for_song_keys([song_key]).get(song_key)
        except Exception:
            logger.warning("kn_ids_for_song_keys failed; using the song_key row", exc_info=True)
            return None

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

    async def _transact(self, entry_id: str, build: Callable[[dict[str, Any] | None], dict[str, Any] | None]) -> Any:
        """Read the log doc, ``build`` its new contents (None = no change), move counters, write.

        ``build`` must be pure: Firestore may run this more than once."""
        kn_cache: dict[str, int | None] = {}

        def kn_for(key: str) -> int | None:
            if key not in kn_cache:
                kn_cache[key] = self._kn_id(key)
            return kn_cache[key]

        async def body(txn: Transaction) -> dict[str, Any] | None:
            cur = await txn.get(LOG_COLLECTION, entry_id)
            new = build(cur)
            if new is None:
                return cur
            now = new["updated_at"]
            reads: dict[Target, dict[str, Any] | None] = {}
            new_target: Target | None = None
            if _counts(new):
                user_id = new["user_id"]
                if new.get("song_key_status") == "resolved" and new.get("song_key"):
                    kn = kn_for(new["song_key"])
                    if kn is not None:
                        kn_target = (USER_SONGS_COLLECTION, f"{user_id}:{kn}")
                        reads[kn_target] = await txn.get(*kn_target)
                        if reads[kn_target] is not None:
                            new_target = kn_target
                    if new_target is None:
                        new_target = (USER_SONGS_COLLECTION, loved_doc_id(user_id, new["song_key"]))
                else:
                    new_target = (UNRESOLVED_COLLECTION, unresolved_doc_id(user_id, new["artist"], new["title"]))
            counted = (cur or {}).get("counted")
            old_target: Target | None = (counted["collection"], counted["doc_id"]) if counted else None
            if old_target != new_target:
                for t in (old_target, new_target):
                    if t is not None and t not in reads:
                        reads[t] = await txn.get(*t)
                # all reads done; writes from here on
                if old_target is not None and reads[old_target] is not None:
                    dec = _dec(reads[old_target], old_target, now)  # type: ignore[arg-type]
                    if dec is None:
                        txn.delete(*old_target)
                    else:
                        txn.set(*old_target, dec)
                if new_target is not None:
                    txn.set(*new_target, _inc(reads[new_target], new_target, new, now))
            new["counted"] = {"collection": new_target[0], "doc_id": new_target[1]} if new_target else None
            txn.set(LOG_COLLECTION, entry_id, new)
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
        for field in ("sung_at", "ended_at"):
            if field in patch:
                new[field] = _iso(patch[field])
        if "singers" in patch:
            new["singers"] = [str(s) for s in patch["singers"] or []]
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
        is_kj = entry_id.startswith("kj_")
        pre = await self.db.get_document(LOG_COLLECTION, entry_id)
        if pre is not None and pre.get("user_id") != user_id:
            raise NotOwnerError(entry_id)
        resolution = None if is_kj else await self._resolution(pre, patch, "user")

        def build(cur: dict[str, Any] | None) -> dict[str, Any]:
            now = _now()
            if cur is not None and cur.get("user_id") != user_id:
                raise NotOwnerError(entry_id)
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
            if "deleted" in patch:
                cur["deleted"] = bool(patch["deleted"])
                cur["deleted_by"] = "user" if cur["deleted"] else None
            return self._apply_facts(cur, {k: v for k, v in patch.items() if k != "deleted"}, resolution, now)

        return self._entry(await self._transact(entry_id, build), entry_id)

    async def delete(self, user_id: str, entry_id: str) -> KaraokeLogEntry:
        """Soft delete by the user (``DELETE /api/my/karaoke-log/{id}``); reverses its count."""
        _check_id(entry_id)

        def build(cur: dict[str, Any] | None) -> dict[str, Any]:
            if cur is None:
                raise KaraokeLogNotFoundError(entry_id)
            if cur.get("user_id") != user_id:
                raise NotOwnerError(entry_id)
            return {**cur, "deleted": True, "deleted_by": "user", "updated_at": _now()}

        return self._entry(await self._transact(entry_id, build), entry_id)

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
        pre = await self.db.get_document(LOG_COLLECTION, entry_id)
        if pre is not None and pre.get("user_id") != user_id:
            pre = None  # a placeholder someone else wrote; kjbox's attribution wins
        resolution = await self._resolution(pre, facts, "partner")

        def build(cur: dict[str, Any] | None) -> dict[str, Any]:
            now = _now()
            if cur is not None and cur.get("user_id") != user_id:
                logger.warning("karaoke-log %s: replacing a placeholder written by another user", entry_id)
                cur = {**cur, "user_id": user_id, "survey": None, "deleted": False, "deleted_by": None}
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

        return self._entry(await self._transact(entry_id, build), entry_id)

    async def delete_partner(self, email: str, entry_id: str) -> KaraokeLogEntry | None:
        """kjbox un-done (``DELETE /api/partner/kjbox/karaoke-log/{id}``). Idempotent; None if unknown."""
        if not entry_id.startswith("kj_"):
            raise KaraokeLogInvalidError("kjbox writes kj_ ids only")
        _check_id(entry_id)
        user_id = await self._partner_user(email)

        def build(cur: dict[str, Any] | None) -> dict[str, Any] | None:
            if cur is None or cur.get("user_id") != user_id or cur.get("deleted"):
                return None
            return {**cur, "deleted": True, "deleted_by": "partner", "updated_at": _now()}

        doc = await self._transact(entry_id, build)
        return self._entry(doc, entry_id) if doc is not None else None

    # ------------------------------------------------------------ retry + listing

    async def retry_unresolved(self, user_id: str) -> int:
        """Re-resolve the user's unresolved/ambiguous records (call after a catalog refresh or on
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

            after = await self._transact(doc["id"], build)
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
    ) -> tuple[list[KaraokeLogEntry], str | None]:
        """The user's records, newest ``updated_at`` first, and the next page's cursor.

        Without ``since``: live records only. With ``since`` (a sync pull): everything updated
        after it, deleted records included so the app can drop them. Survey-only placeholders
        (kjbox facts not delivered yet) are never listed. One equality query on ``user_id``
        (no composite index); filtering and paging happen here."""
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
