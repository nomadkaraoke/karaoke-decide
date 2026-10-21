"""Tests for the gen session validator.

CASES mirrors karaoke-gen/backend/services/user_service.py ``validate_session``.
gen has no parity table of its own (backlog item in A4); keep this in step
with gen's validation order if gen changes.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.services.gen_session import (
    GenIdentity,
    GenSessionError,
    GenSessionValidator,
    _as_utc,
    is_gen_token,
)

NOW = datetime(2026, 10, 21, 12, 0, 0, tzinfo=UTC)
TOKEN = "tok_ABC-123"
EMAIL = "ann@x.com"


def _session(**overrides):
    doc = {
        "token": TOKEN,
        "user_email": EMAIL,
        "is_active": True,
        "expires_at": NOW + timedelta(days=30),
        "last_activity_at": NOW - timedelta(minutes=5),
    }
    doc.update(overrides)
    return doc


def _user(**overrides):
    doc = {"email": EMAIL, "is_active": True}
    doc.update(overrides)
    return doc


# (name, session_doc, user_doc, expected_reason_or_None). A None session_doc
# means no doc; a None user_doc means no user. A "hash" user is stored under
# a non-email doc id and found only by the ``email`` field query.
CASES = [
    ("missing_doc", None, _user(), "invalid"),
    ("inactive", _session(is_active=False), _user(), "revoked"),
    ("expired", _session(expires_at=NOW - timedelta(seconds=1)), _user(), "expired"),
    (
        "idle",
        _session(last_activity_at=NOW - timedelta(days=7, seconds=1)),
        _user(),
        "idle",
    ),
    (
        "idle_boundary",
        _session(last_activity_at=NOW - timedelta(days=6, hours=23)),
        _user(),
        None,
    ),
    ("tenant", _session(tenant_id="acme"), _user(), "tenant"),
    ("tenant_empty_string", _session(tenant_id=""), _user(), None),
    ("user_missing", _session(), None, "user_not_found"),
    ("user_disabled", _session(), _user(is_active=False), "user_disabled"),
    ("legacy_hash_keyed_user", _session(), "hash", None),
    ("mixed_case_email", _session(user_email="Ann@X.com"), _user(), None),
]


def _make(session_doc, user_doc, fs):
    async def get_document(collection, doc_id):
        if collection == "sessions":
            return session_doc
        if collection == "gen_users":
            return user_doc if isinstance(user_doc, dict) and doc_id == EMAIL else None
        return None

    async def query_documents(collection, filters=None, **kwargs):
        assert collection == "gen_users"
        assert filters == [("email", "==", EMAIL)]
        if user_doc == "hash":
            return [{"id": "somehash", **_user()}]
        return []

    fs.get_document = AsyncMock(side_effect=get_document)
    fs.query_documents = AsyncMock(side_effect=query_documents)


@pytest.fixture
def clock():
    state = {"now": NOW}

    def now():
        return state["now"]

    now.state = state
    return now


@pytest.mark.parametrize("name,session_doc,user_doc,reason", CASES, ids=[c[0] for c in CASES])
async def test_parity_table(mock_firestore_service, clock, name, session_doc, user_doc, reason):
    _make(session_doc, user_doc, mock_firestore_service)
    validator = GenSessionValidator(mock_firestore_service, now=clock)
    if reason is None:
        identity = await validator.validate(TOKEN)
        assert identity == GenIdentity("ann@x.com")
    else:
        with pytest.raises(GenSessionError) as exc:
            await validator.validate(TOKEN)
        assert exc.value.reason == reason


@pytest.mark.parametrize(
    "make_value",
    [
        lambda dt: dt.isoformat(),
        lambda dt: dt.replace(tzinfo=None).isoformat(),
        lambda dt: dt.replace(tzinfo=None),
        lambda dt: dt,
    ],
    ids=["iso_aware", "iso_naive", "naive_dt", "aware_dt"],
)
async def test_timestamp_shapes(mock_firestore_service, clock, make_value):
    future = _session(expires_at=make_value(NOW + timedelta(hours=1)))
    _make(future, _user(), mock_firestore_service)
    assert await GenSessionValidator(mock_firestore_service, now=clock).validate(TOKEN) == GenIdentity(EMAIL)

    past = _session(expires_at=make_value(NOW - timedelta(hours=1)))
    _make(past, _user(), mock_firestore_service)
    with pytest.raises(GenSessionError) as exc:
        await GenSessionValidator(mock_firestore_service, now=clock).validate(TOKEN)
    assert exc.value.reason == "expired"


async def test_slides_idle_window_hourly(mock_firestore_service, clock):
    _make(_session(last_activity_at=NOW - timedelta(hours=2)), _user(), mock_firestore_service)
    validator = GenSessionValidator(mock_firestore_service, now=clock)
    await validator.validate(TOKEN)
    mock_firestore_service.update_document.assert_awaited_once_with(
        "sessions", TOKEN, {"last_activity_at": NOW.isoformat()}
    )
    clock.state["now"] = NOW + timedelta(minutes=10)
    await validator.validate(TOKEN)
    assert mock_firestore_service.update_document.await_count == 1


async def test_recent_activity_not_written(mock_firestore_service, clock):
    _make(_session(last_activity_at=NOW - timedelta(minutes=30)), _user(), mock_firestore_service)
    await GenSessionValidator(mock_firestore_service, now=clock).validate(TOKEN)
    mock_firestore_service.update_document.assert_not_awaited()


async def test_positive_cache_60s(mock_firestore_service, clock):
    _make(_session(), _user(), mock_firestore_service)
    validator = GenSessionValidator(mock_firestore_service, now=clock)
    await validator.validate(TOKEN)
    clock.state["now"] = NOW + timedelta(seconds=59)
    await validator.validate(TOKEN)
    session_reads = lambda: [c for c in mock_firestore_service.get_document.await_args_list if c.args[0] == "sessions"]  # noqa: E731
    assert len(session_reads()) == 1
    clock.state["now"] = NOW + timedelta(seconds=61)
    await validator.validate(TOKEN)
    assert len(session_reads()) == 2


async def test_negative_cache_10s(mock_firestore_service, clock):
    _make(None, _user(), mock_firestore_service)
    validator = GenSessionValidator(mock_firestore_service, now=clock)
    for offset, expected_reads in ((0, 1), (9, 1), (11, 2)):
        clock.state["now"] = NOW + timedelta(seconds=offset)
        with pytest.raises(GenSessionError) as exc:
            await validator.validate(TOKEN)
        assert exc.value.reason == "invalid"
        assert mock_firestore_service.get_document.await_count == expected_reads


async def test_firestore_error_is_error_reason_and_not_cached(mock_firestore_service, clock):
    mock_firestore_service.get_document = AsyncMock(side_effect=RuntimeError("boom"))
    validator = GenSessionValidator(mock_firestore_service, now=clock)
    for expected_reads in (1, 2):
        with pytest.raises(GenSessionError) as exc:
            await validator.validate(TOKEN)
        assert exc.value.reason == "error"
        assert mock_firestore_service.get_document.await_count == expected_reads


async def test_clear_cache(mock_firestore_service, clock):
    _make(_session(), _user(), mock_firestore_service)
    validator = GenSessionValidator(mock_firestore_service, now=clock)
    await validator.validate(TOKEN)
    validator.clear_cache()
    await validator.validate(TOKEN)
    assert mock_firestore_service.get_document.await_count == 4  # session + user, twice


async def test_raw_token_not_stored_in_cache(mock_firestore_service, clock):
    _make(_session(), _user(), mock_firestore_service)
    validator = GenSessionValidator(mock_firestore_service, now=clock)
    await validator.validate(TOKEN)
    assert TOKEN not in validator._cache  # noqa: SLF001


def test_is_gen_token():
    assert is_gen_token("abc_DEF-123")
    assert not is_gen_token("a.b.c")


def test_as_utc():
    assert _as_utc(None) is None
    assert _as_utc("garbage") is None
    assert _as_utc("2026-01-01T00:00:00Z") == datetime(2026, 1, 1, tzinfo=UTC)


def test_get_gen_session_validator_singleton(monkeypatch):
    from backend.services import gen_session

    monkeypatch.setattr(gen_session, "_validator", None)
    monkeypatch.setattr(gen_session, "FirestoreService", MagicMock())
    monkeypatch.setattr(gen_session, "get_backend_settings", MagicMock())
    assert gen_session.get_gen_session_validator() is gen_session.get_gen_session_validator()
