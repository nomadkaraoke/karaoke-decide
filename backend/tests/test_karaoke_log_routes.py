"""Karaoke-log HTTP routes: user (/api/my/karaoke-log) and kjbox partner (/api/partner/kjbox/karaoke-log).

In-memory Firestore, a fake catalog reader and a fake gen-session validator; no GCP.
"""

from __future__ import annotations

import hashlib
from collections.abc import Generator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import (
    get_auth_service_dep,
    get_catalog_lookup_dep,
    get_current_user,
    get_firestore,
    get_settings,
)
from backend.config import BackendSettings
from backend.main import app
from backend.services.auth_service import AuthService
from backend.services.catalog_lookup import CatalogLookup
from backend.services.gen_session import GenIdentity
from backend.services.karaoke_log_service import LOG_COLLECTION, KaraokeLogService, log_doc_id
from backend.services.song_feedback_service import loved_doc_id
from backend.tests.test_karaoke_log_service import (
    HELLO_KEY,
    KN_ID,
    FakeReader,
    FakeStore,
    MemoryFirestore,
    _app_entry,
    _kj_facts,
)
from karaoke_decide.core.models import User

SECRET = "partner-test-secret"
EMAIL = "singer@example.com"
EMAIL_HASH = hashlib.sha256(EMAIL.encode()).hexdigest()
USER_ID = "user_route_a"
KJ_ID = "kj_" + "b" * 24
APP_ID = "app_7d1f0c9e-2222-4333-8444-955556666777"
GEN_TOKEN = "g" * 43  # dot-less: a gen session token


@pytest.fixture(autouse=True)
def legacy_jwt_token_path() -> None:
    """Override the conftest autouse fixture: use the real gen-token detection here."""


class RouteFirestore(MemoryFirestore):
    """C1's in-memory Firestore plus the calls AuthService.resolve_gen_user makes."""

    async def create_document(self, collection: str, doc_id: str, data: dict[str, Any]) -> bool:
        col = self.cols.setdefault(collection, {})
        if doc_id in col:
            return False
        col[doc_id] = dict(data)
        return True


def _settings(secret: str = SECRET, **over: Any) -> BackendSettings:
    return BackendSettings(
        environment="development",
        google_cloud_project="test-project",
        jwt_secret="test-jwt-secret-key-for-testing-only",
        decide_kjbox_secret=secret,
        **over,
    )


@pytest.fixture
def fs() -> RouteFirestore:
    fs = RouteFirestore()
    fs.cols["decide_users"] = {
        EMAIL_HASH: {
            "user_id": USER_ID,
            "email": EMAIL,
            "is_guest": False,
            "gen_linked_at": "2026-10-01T00:00:00+00:00",
            "created_at": "2026-10-01T00:00:00+00:00",
            "updated_at": "2026-10-01T00:00:00+00:00",
        }
    }
    return fs


@pytest.fixture
def reader() -> FakeReader:
    r = FakeReader()
    r.add_kn(KN_ID, "The Lanterns", "Paper Harbour", HELLO_KEY)
    return r


def _client(fs: RouteFirestore, reader: FakeReader, settings: BackendSettings, user: User | None) -> TestClient:
    auth = AuthService(settings, fs, email_service=MagicMock())  # type: ignore[arg-type]
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_firestore] = lambda: fs
    app.dependency_overrides[get_auth_service_dep] = lambda: auth
    app.dependency_overrides[get_catalog_lookup_dep] = lambda: CatalogLookup(FakeStore(reader))  # type: ignore[arg-type]
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


@pytest.fixture
def client(fs: RouteFirestore, reader: FakeReader) -> Iterator[TestClient]:
    yield _client(fs, reader, _settings(), User(id=USER_ID, email=EMAIL))
    app.dependency_overrides.clear()


@pytest.fixture
def anon(fs: RouteFirestore, reader: FakeReader) -> Iterator[TestClient]:
    """No user override: real get_current_user, with a fake gen-session validator."""
    validator = MagicMock()
    validator.validate = AsyncMock(return_value=GenIdentity(email=EMAIL))
    with patch("backend.api.deps.get_gen_session_validator", return_value=validator):
        yield _client(fs, reader, _settings(), None)
    app.dependency_overrides.clear()


def _partner(secret: str = SECRET) -> dict[str, str]:
    return {"X-Kjbox-Secret": secret}


def _outbox(**over: Any) -> dict[str, Any]:
    return {"history_id": KJ_ID, "gen_email": EMAIL, "source": "kjbox", **_kj_facts(**over)}


# ---------------------------------------------------------------- auth


def test_routes_require_gen_token(anon: TestClient) -> None:
    for method, path in (
        ("GET", "/api/my/karaoke-log"),
        ("PUT", f"/api/my/karaoke-log/{APP_ID}"),
        ("DELETE", f"/api/my/karaoke-log/{APP_ID}"),
    ):
        r = anon.request(method, path, json=_app_entry() if method == "PUT" else None)
        assert r.status_code == 401, (method, path)

    bearer = {"Authorization": f"Bearer {GEN_TOKEN}"}
    r = anon.get("/api/my/karaoke-log", headers=bearer)
    assert r.status_code == 200
    assert r.json() == {"entries": [], "next_cursor": None}
    r = anon.put(f"/api/my/karaoke-log/{APP_ID}", json=_app_entry(), headers=bearer)
    assert r.status_code == 200 and r.json()["id"] == APP_ID


def test_partner_requires_secret(client: TestClient, fs: RouteFirestore) -> None:
    """A missing or wrong secret is 503 partner_auth: retryable, so kjbox's outbox keeps the
    night's records (it drops 4xx other than 429) until the secret is fixed."""
    path = f"/api/partner/kjbox/karaoke-log/{KJ_ID}"
    for r in (
        client.put(path, json=_outbox()),
        client.put(path, json=_outbox(), headers=_partner("wrong")),
        client.put(path, json=_outbox(), headers=_partner(SECRET + "x")),
        client.delete(path, params={"gen_email": EMAIL}, headers=_partner("wrong")),
    ):
        assert r.status_code == 503 and r.json() == {"detail": "partner_auth"}
    assert LOG_COLLECTION not in fs.cols
    assert client.put(path, json=_outbox(), headers=_partner()).status_code == 200


def test_partner_503_when_secret_unset(fs: RouteFirestore, reader: FakeReader) -> None:
    try:
        c = _client(fs, reader, _settings(secret=""), User(id=USER_ID, email=EMAIL))
        path = f"/api/partner/kjbox/karaoke-log/{KJ_ID}"
        assert c.put(path, json=_outbox(), headers=_partner("")).status_code == 503
        assert c.put(path, json=_outbox(), headers=_partner()).status_code == 503
        assert c.delete(path, params={"gen_email": EMAIL}, headers=_partner()).status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_local_stack_secret_default() -> None:
    assert _settings(secret="").kjbox_secret == ""
    assert _settings(secret="", decide_test_api=True).kjbox_secret == "local-stack-secret"
    assert _settings(secret="real", decide_test_api=True).kjbox_secret == "real"
    prod = BackendSettings(environment="production", google_cloud_project="p", decide_test_api=True)
    assert prod.kjbox_secret == ""


# ---------------------------------------------------------------- partner


def test_partner_creates_profile_for_new_email(client: TestClient, fs: RouteFirestore) -> None:
    new_email = "First.Timer@Example.com"
    new_hash = hashlib.sha256(new_email.lower().encode()).hexdigest()
    assert new_hash not in fs.cols["decide_users"]
    r = client.put(f"/api/partner/kjbox/karaoke-log/{KJ_ID}", json=_outbox(gen_email=new_email), headers=_partner())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"id": KJ_ID, "deleted": False, "song_key_status": "resolved"}
    profile = fs.cols["decide_users"][new_hash]
    new_user = profile["user_id"]
    assert new_user.startswith("user_") and profile["email"] == new_email.lower()
    record = fs.log(KJ_ID, new_user)
    assert (record["state"], record["artist"], record["song_key"]) == ("sung", "The Lanterns", HELLO_KEY)
    assert fs.user_songs()[loved_doc_id(new_user, HELLO_KEY)]["times_sung"] == 1


def test_partner_put_requires_gen_email(client: TestClient) -> None:
    body = _outbox()
    del body["gen_email"]
    r = client.put(f"/api/partner/kjbox/karaoke-log/{KJ_ID}", json=body, headers=_partner())
    assert r.status_code == 400 and r.json() == {"detail": "invalid"}
    r = client.put(f"/api/partner/kjbox/karaoke-log/{KJ_ID}", json=[1, 2], headers=_partner())
    assert r.status_code == 422


def test_partner_delete_undoes_and_is_idempotent(client: TestClient, fs: RouteFirestore) -> None:
    path = f"/api/partner/kjbox/karaoke-log/{KJ_ID}"
    assert client.put(path, json=_outbox(), headers=_partner()).status_code == 200
    assert client.delete(path, params={"gen_email": EMAIL}, headers=_partner()).status_code == 204
    assert fs.log(KJ_ID, USER_ID)["deleted"] is True
    assert loved_doc_id(USER_ID, HELLO_KEY) not in fs.user_songs()
    # Repeat, and the JSON-body form of the email, and an unknown id: all 204.
    assert client.request("DELETE", path, json={"gen_email": EMAIL}, headers=_partner()).status_code == 204
    other = f"/api/partner/kjbox/karaoke-log/kj_{'c' * 24}"
    assert client.delete(other, params={"gen_email": EMAIL}, headers=_partner()).status_code == 204
    assert client.delete(path, headers=_partner()).status_code == 400  # no email at all


def test_partner_delete_never_creates_a_profile(client: TestClient, fs: RouteFirestore) -> None:
    stranger = "never.seen@example.com"
    r = client.delete(f"/api/partner/kjbox/karaoke-log/{KJ_ID}", params={"gen_email": stranger}, headers=_partner())
    assert r.status_code == 204
    assert hashlib.sha256(stranger.encode()).hexdigest() not in fs.cols["decide_users"]
    assert LOG_COLLECTION not in fs.cols


def test_partner_singers_must_be_short_strings(client: TestClient, fs: RouteFirestore) -> None:
    path = f"/api/partner/kjbox/karaoke-log/{KJ_ID}"
    for bad in ("Sam", [1, 2], ["x" * 101], [f"s{i}" for i in range(7)], {"a": 1}):
        r = client.put(path, json=_outbox(singers=bad), headers=_partner())
        assert r.status_code == 400 and r.json() == {"detail": "invalid"}, bad
    assert LOG_COLLECTION not in fs.cols
    ok = client.put(path, json=_outbox(singers=["Sam", "y" * 100, "a", "b", "c", "d"]), headers=_partner())
    assert ok.status_code == 200
    assert fs.log(KJ_ID, USER_ID)["singers"][0] == "Sam"


def test_partner_nested_objects_must_be_objects(client: TestClient, fs: RouteFirestore) -> None:
    """A malformed outbox record is a 400 (kjbox stops retrying it), never a 500 (retried forever)."""
    path = f"/api/partner/kjbox/karaoke-log/{KJ_ID}"
    for bad in (
        {"venue": "Andrew"},
        {"venue": ["nomad", "Andrew"]},
        {"venue": {"kind": "nomad", "name": 7}},
        {"media": "NOMAD-0001"},
        {"media": ["NOMAD-0001"]},
        {"night": "abcd"},
        {"night_id": 12},
        {"sung_at": 12},
    ):
        r = client.put(path, json=_outbox(**bad), headers=_partner())
        assert r.status_code == 400 and r.json() == {"detail": "invalid"}, bad
    assert LOG_COLLECTION not in fs.cols
    ok = client.put(path, json=_outbox(venue=None), headers=_partner())
    assert ok.status_code == 200
    assert fs.log(KJ_ID, USER_ID)["night"]["kind"] == "nomad"


def test_partner_rejects_app_ids(client: TestClient) -> None:
    r = client.put(f"/api/partner/kjbox/karaoke-log/{APP_ID}", json=_outbox(), headers=_partner())
    assert r.status_code == 400


# ---------------------------------------------------------------- user


def test_list_paginates_newest_first(client: TestClient) -> None:
    ids = [f"app_0000000{i}-1111-4222-8333-944445555666" for i in range(5)]
    for i, entry_id in enumerate(ids):
        r = client.put(f"/api/my/karaoke-log/{entry_id}", json=_app_entry(title=f"Paper Harbour {i}"))
        assert r.status_code == 200, r.text
    r = client.get("/api/my/karaoke-log", params={"limit": 2})
    page1 = r.json()
    assert [e["id"] for e in page1["entries"]] == [ids[4], ids[3]]
    assert page1["next_cursor"]
    page2 = client.get("/api/my/karaoke-log", params={"limit": 2, "cursor": page1["next_cursor"]}).json()
    assert [e["id"] for e in page2["entries"]] == [ids[2], ids[1]]
    page3 = client.get("/api/my/karaoke-log", params={"limit": 2, "cursor": page2["next_cursor"]}).json()
    assert [e["id"] for e in page3["entries"]] == [ids[0]] and page3["next_cursor"] is None

    assert client.get("/api/my/karaoke-log", params={"cursor": "!!not-a-cursor"}).status_code == 400
    assert client.get("/api/my/karaoke-log", params={"limit": 0}).status_code == 422
    assert client.get("/api/my/karaoke-log", params={"since": "yesterday"}).status_code == 400


def test_list_filters_by_night_and_since(client: TestClient) -> None:
    client.put(f"/api/my/karaoke-log/{APP_ID}", json=_app_entry())
    other = "app_99999999-1111-4222-8333-944445555666"
    client.put(f"/api/my/karaoke-log/{other}", json=_app_entry(night={"id": "n2", "kind": "other"}))
    r = client.get("/api/my/karaoke-log", params={"night_id": "n2"}).json()
    assert [e["id"] for e in r["entries"]] == [other]
    since = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    client.delete(f"/api/my/karaoke-log/{other}")
    r = client.get("/api/my/karaoke-log", params={"since": since}).json()
    assert {e["id"]: e["deleted"] for e in r["entries"]} == {APP_ID: False, other: True}
    assert [e["id"] for e in client.get("/api/my/karaoke-log").json()["entries"]] == [APP_ID]


def test_list_filters_by_song(client: TestClient) -> None:
    client.put(f"/api/my/karaoke-log/{APP_ID}", json=_app_entry())
    other = "app_99999999-1111-4222-8333-944445555666"
    client.put(f"/api/my/karaoke-log/{other}", json=_app_entry(artist="Adele", title="Hello", song_key="adele|hello"))
    r = client.get("/api/my/karaoke-log", params={"song": "adele|hello"}).json()
    assert [e["id"] for e in r["entries"]] == [other]
    assert client.get("/api/my/karaoke-log", params={"song": "x" * 601}).status_code == 422


def test_app_cannot_write_kj_prefixed_facts(client: TestClient, fs: RouteFirestore) -> None:
    path = f"/api/partner/kjbox/karaoke-log/{KJ_ID}"
    assert client.put(path, json=_outbox(), headers=_partner()).status_code == 200

    r = client.put(f"/api/my/karaoke-log/{KJ_ID}", json={"state": "skipped"})
    assert r.status_code == 403 and r.json() == {"detail": "not_owner"}
    r = client.put(f"/api/my/karaoke-log/{KJ_ID}", json={"survey": {"feel": "great"}, "artist": "Someone Else"})
    assert r.status_code == 403
    assert fs.log(KJ_ID, USER_ID)["state"] == "sung" and fs.log(KJ_ID, USER_ID).get("survey") is None

    r = client.put(f"/api/my/karaoke-log/{KJ_ID}", json={"survey": {"feel": "great", "key": "too_high"}})
    assert r.status_code == 200
    body = r.json()
    assert body["survey"]["feel"] == "great" and body["state"] == "sung"
    assert client.put(f"/api/my/karaoke-log/{KJ_ID}", json={"deleted": True}).json()["deleted"] is True


def test_user_put_validation_and_delete(client: TestClient, fs: RouteFirestore) -> None:
    r = client.put(f"/api/my/karaoke-log/{APP_ID}", json=_app_entry(state="dancing"))
    assert r.status_code == 400 and r.json() == {"detail": "invalid"}
    r = client.put(f"/api/my/karaoke-log/{APP_ID}", json={"survey": {"notes": "x" * 2001}, **_app_entry()})
    assert r.status_code == 400
    assert client.put("/api/my/karaoke-log/nope_123", json=_app_entry()).status_code == 400
    assert client.put(f"/api/my/karaoke-log/{APP_ID}", json="a string").status_code == 422
    assert client.delete(f"/api/my/karaoke-log/{APP_ID}").status_code == 404

    r = client.put(f"/api/my/karaoke-log/{APP_ID}", json=_app_entry())
    assert r.status_code == 200
    entry = r.json()
    assert (entry["source"], entry["song_key"], entry["song_key_status"]) == ("app", HELLO_KEY, "resolved")
    assert "user_id" not in entry and "email_hash" not in entry
    assert fs.user_songs()[loved_doc_id(USER_ID, HELLO_KEY)]["times_sung"] == 1
    r = client.delete(f"/api/my/karaoke-log/{APP_ID}")
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert loved_doc_id(USER_ID, HELLO_KEY) not in fs.user_songs()


# ---------------------------------------------------------------- unresolved retry (C2-R1)


@pytest.fixture
def retry_spy() -> Generator[AsyncMock, None, None]:
    spy = AsyncMock(return_value=0)
    with patch.object(KaraokeLogService, "retry_unresolved", spy):
        yield spy


def test_get_retries_unresolved_once_per_day(client: TestClient, fs: RouteFirestore, retry_spy: AsyncMock) -> None:
    assert client.get("/api/my/karaoke-log").status_code == 200
    assert retry_spy.await_count == 1
    assert retry_spy.await_args is not None and retry_spy.await_args.args[-1] == USER_ID
    marker = fs.cols["decide_users"][EMAIL_HASH]["karaoke_log_retry_at"]
    assert isinstance(marker, str)

    client.get("/api/my/karaoke-log")
    assert retry_spy.await_count == 1  # within 24 h: no retry

    stale = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    fs.cols["decide_users"][EMAIL_HASH]["karaoke_log_retry_at"] = stale
    client.get("/api/my/karaoke-log")
    assert retry_spy.await_count == 2


def test_retry_moves_unresolved_count_on_get(client: TestClient, fs: RouteFirestore, reader: FakeReader) -> None:
    unknown = {"artist": "Nobody Known", "title": "Unheard Tune"}
    client.put(f"/api/my/karaoke-log/{APP_ID}", json=_app_entry(song_key=None, **unknown))
    assert fs.log(APP_ID, USER_ID)["song_key_status"] == "unresolved"
    reader.add_kn(99, "Nobody Known", "Unheard Tune", "nobody known|unheard tune")  # catalog refresh
    client.get("/api/my/karaoke-log")  # background retry runs before TestClient returns
    record = fs.log(APP_ID, USER_ID)
    assert (record["song_key_status"], record["song_key"]) == ("resolved", "nobody known|unheard tune")
    assert fs.user_songs()[loved_doc_id(USER_ID, "nobody known|unheard tune")]["times_sung"] == 1


def test_retry_failure_never_breaks_get(client: TestClient, retry_spy: AsyncMock) -> None:
    retry_spy.side_effect = RuntimeError("catalog exploded")
    r = client.get("/api/my/karaoke-log")
    assert r.status_code == 200


def test_log_doc_id_is_namespaced(client: TestClient, fs: RouteFirestore) -> None:
    client.put(f"/api/my/karaoke-log/{APP_ID}", json=_app_entry())
    assert log_doc_id(USER_ID, APP_ID) in fs.cols[LOG_COLLECTION]
