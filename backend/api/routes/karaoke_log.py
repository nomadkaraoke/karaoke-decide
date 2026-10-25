"""Karaoke log routes (Phase 5 "Tonight" spec section 7.2).

* ``/api/my/karaoke-log`` (mounted at ``/my/karaoke-log``): the signed-in user's records.
  Bearer gen session token (``get_current_user``).
* ``/api/partner/kjbox/karaoke-log`` (mounted at ``/partner/kjbox/karaoke-log``): kjbox's
  history outbox, server to server. Header ``X-Kjbox-Secret`` must equal the
  ``DECIDE_KJBOX_SECRET`` setting (``hmac.compare_digest``). A missing or wrong secret is
  503 ``partner_auth`` and an unset one 503 ``partner_disabled``: both retryable, because
  kjbox's outbox drops a record on any 4xx but 429.

Errors use decide's usual shape, ``{"detail": "<code>"}``, with the karaoke-log codes
``invalid`` (400), ``not_owner`` (403) and ``not_found`` (404). A body that isn't a JSON
object, or an out-of-range ``limit``, is FastAPI's 422.
"""

from __future__ import annotations

import hmac
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Header, HTTPException, Query, Request, Response, status

from backend.api.deps import CurrentUser, FirestoreServiceDep, KaraokeLogServiceDep, Settings
from backend.models.karaoke_log import KaraokeLogEntry, KaraokeLogPage
from backend.services.auth_service import AuthService
from backend.services.firestore_service import FirestoreService
from backend.services.karaoke_log_service import (
    DEFAULT_LIST_LIMIT,
    MAX_LIST_LIMIT,
    KaraokeLogError,
    KaraokeLogInvalidError,
    KaraokeLogService,
    email_hash,
)
from karaoke_decide.core.models import User

logger = logging.getLogger(__name__)

router = APIRouter()  # /my/karaoke-log
partner_router = APIRouter()  # /partner/kjbox/karaoke-log

# Re-resolve the user's unresolved records at most this often (on GET, in the background).
RETRY_INTERVAL = timedelta(hours=24)
RETRY_MARKER = "karaoke_log_retry_at"  # on the decide_users doc

_STATUS = {"invalid": 400, "not_owner": 403, "not_found": 404}
# The API returns the user's own records; the owner ids stay server-side.
_PRIVATE = {"user_id", "email_hash"}
_PAGE_EXCLUDE: Any = {"entries": {"__all__": _PRIVATE}}

Patch = Annotated[dict[str, Any], Body()]


def _http(exc: KaraokeLogError) -> HTTPException:
    if exc.code not in _STATUS:
        logger.error("karaoke-log: unexpected %s", type(exc).__name__, exc_info=exc)
    else:
        logger.info("karaoke-log %s: %s", exc.code, exc)
    return HTTPException(status_code=_STATUS.get(exc.code, 500), detail=exc.code)


# ---------------------------------------------------------------- unresolved retry


def _user_doc_id(user: User) -> str:
    """decide_users doc id: sha256(lowercase email) for verified users, the user_id for guests."""
    return email_hash(user.email) if user.email else user.id


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


async def _run_retry(service: KaraokeLogService, user_id: str) -> None:
    try:
        resolved = await service.retry_unresolved(user_id)
        if resolved:
            logger.info("karaoke-log: resolved %d record(s) for %s", resolved, user_id)
    except Exception:
        logger.warning("karaoke-log: retry_unresolved failed for %s", user_id, exc_info=True)


async def _schedule_retry(
    user: User, firestore: FirestoreService, service: KaraokeLogService, background: BackgroundTasks
) -> None:
    """Queue ``retry_unresolved`` after the response, at most once per RETRY_INTERVAL per user.

    The marker is written before the retry runs, so concurrent GETs don't all retry."""
    doc_id = _user_doc_id(user)
    now = datetime.now(UTC)
    try:
        doc = await firestore.get_document(AuthService.USERS_COLLECTION, doc_id)
        if doc is None:
            return
        last = _parse(doc.get(RETRY_MARKER))
        if last is not None and now - last < RETRY_INTERVAL:
            return
        await firestore.update_document(AuthService.USERS_COLLECTION, doc_id, {RETRY_MARKER: now.isoformat()})
    except Exception:  # best effort: never fail the read over it
        logger.warning("karaoke-log: retry marker failed for %s", user.id, exc_info=True)
        return
    background.add_task(_run_retry, service, user.id)


# ---------------------------------------------------------------- user routes


@router.get("", response_model=KaraokeLogPage, response_model_exclude=_PAGE_EXCLUDE)
async def list_karaoke_log(
    user: CurrentUser,
    service: KaraokeLogServiceDep,
    firestore: FirestoreServiceDep,
    background: BackgroundTasks,
    since: Annotated[str | None, Query(description="ISO time: records updated after it, deleted ones too")] = None,
    night_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIST_LIMIT)] = DEFAULT_LIST_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
    song: Annotated[str | None, Query(max_length=600, description="Library key: only that song's records")] = None,
) -> KaraokeLogPage:
    """The user's records, newest ``updated_at`` first; ``next_cursor`` pages on."""
    try:
        entries, next_cursor = await service.list_for_user(
            user.id, since=since, night_id=night_id, limit=limit, cursor=cursor, song=song
        )
    except KaraokeLogError as exc:
        raise _http(exc) from exc
    await _schedule_retry(user, firestore, service, background)
    return KaraokeLogPage(entries=entries, next_cursor=next_cursor)


@router.put("/{entry_id}", response_model=KaraokeLogEntry, response_model_exclude=_PRIVATE)
async def put_karaoke_log(
    entry_id: str, patch: Patch, user: CurrentUser, service: KaraokeLogServiceDep
) -> KaraokeLogEntry:
    """Upsert by id with field-level merge. ``kj_`` ids: only ``survey`` and ``deleted``."""
    try:
        return await service.upsert_user(user.id, entry_id, patch, email=user.email)
    except KaraokeLogError as exc:
        raise _http(exc) from exc


@router.delete("/{entry_id}", response_model=KaraokeLogEntry, response_model_exclude=_PRIVATE)
async def delete_karaoke_log(entry_id: str, user: CurrentUser, service: KaraokeLogServiceDep) -> KaraokeLogEntry:
    """Soft delete (a mistaken log); reverses its sung count."""
    try:
        return await service.delete(user.id, entry_id)
    except KaraokeLogError as exc:
        raise _http(exc) from exc


# ---------------------------------------------------------------- kjbox partner routes


async def require_kjbox_partner(settings: Settings, x_kjbox_secret: Annotated[str | None, Header()] = None) -> None:
    expected = settings.kjbox_secret
    if not expected:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="partner_disabled")
    if not hmac.compare_digest((x_kjbox_secret or "").encode(), expected.encode()):
        # 503, not 401: kjbox's outbox treats 4xx (but 429) as permanent and would drop the
        # night's history over a misconfigured or rotating secret. 503 keeps it retrying.
        logger.warning("karaoke-log partner: missing or wrong X-Kjbox-Secret")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="partner_auth")


Partner = Annotated[None, Depends(require_kjbox_partner)]


def _gen_email(value: Any) -> str:
    if not isinstance(value, str) or "@" not in value or len(value) > 320:
        raise _http(KaraokeLogInvalidError("gen_email is required"))
    return value.strip().lower()


@partner_router.put("/{entry_id}")
async def partner_put_karaoke_log(
    entry_id: str, record: Patch, _: Partner, service: KaraokeLogServiceDep
) -> dict[str, Any]:
    """One history-outbox record (spec 5.5) for the singer ``gen_email`` (profile created if new).

    Idempotent. The reply carries no survey or other user data."""
    email = _gen_email(record.get("gen_email"))
    try:
        entry = await service.upsert_partner(email, entry_id, record)
    except KaraokeLogError as exc:
        raise _http(exc) from exc
    return {"id": entry.id, "deleted": entry.deleted, "song_key_status": entry.song_key_status}


@partner_router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def partner_delete_karaoke_log(
    entry_id: str,
    request: Request,
    _: Partner,
    service: KaraokeLogServiceDep,
    gen_email: Annotated[str | None, Query()] = None,
) -> Response:
    """The host un-did Done. ``gen_email`` as a query parameter or in a JSON body. Idempotent:
    204 whether or not the record exists."""
    if gen_email is None:
        raw = await request.body()
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {}
        gen_email = body.get("gen_email") if isinstance(body, dict) else None
    email = _gen_email(gen_email)
    try:
        await service.delete_partner(email, entry_id)
    except KaraokeLogError as exc:
        raise _http(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
