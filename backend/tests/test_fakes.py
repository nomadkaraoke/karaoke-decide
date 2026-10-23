"""DECIDE_FAKE_EXTERNALS: the local stack's in-process fakes for ListenBrainz, MetaBrainz OAuth,
the Spotify embed page, Last.fm and Gemini, plus DECIDE_INLINE_TASKS.

The app runs in a real uvicorn server on a free loopback port with every external base URL
pointing back at its own ``/fake`` routes, so the real HTTP clients (ListenBrainzClient,
MetaBrainzOAuth, SpotifyEmbedClient, LastFmClient) and the inline Cloud Task POST are exercised
over the wire, exactly as on the local stack. Firestore is in memory.
"""

from __future__ import annotations

import asyncio
import copy
import socket
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from google.cloud import firestore

from backend import main
from backend.api.deps import get_catalog_lookup_dep, get_current_user, get_firestore
from backend.config import BackendSettings, get_backend_settings
from backend.fakes.state import STATE
from backend.services import cloud_tasks_service, track_matcher
from backend.services.fake_screenshot_extractor import FakeScreenshotExtractor
from backend.services.track_matcher import MatchedTrack
from karaoke_decide.core.models import User
from karaoke_decide.services.listenbrainz import ListenBrainzClient

USER = User(id="fixture-user-fakes", email="e2e-fakes@example.com")
TEST_ONLY_FERNET_KEY = "imC0NA2w3dNUv5rMVVWcaerh2hf6BOwbJNUWXbeAIEk="  # test-only, never a real key
FIXTURE_PLAYLIST_URL = "https://open.spotify.com/playlist/fixtureplaylist0000001"


# --------------------------------------------------------------------------- in-memory Firestore


class MemoryFirestore:
    """The FirestoreService surface, in memory (one event loop: every call is atomic)."""

    def __init__(self) -> None:
        self.cols: dict[str, dict[str, dict[str, Any]]] = {}

    def col(self, name: str) -> dict[str, dict[str, Any]]:
        return self.cols.setdefault(name, {})

    @staticmethod
    def _apply(cur: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        out = dict(cur)
        for field, value in data.items():
            if isinstance(value, firestore.ArrayUnion):
                out[field] = list(dict.fromkeys([*out.get(field, []), *value.values]))
            else:
                out[field] = copy.deepcopy(value)
        return out

    async def get_document(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        doc = self.col(collection).get(doc_id)
        return None if doc is None else {"id": doc_id, **copy.deepcopy(doc)}

    async def set_document(self, collection: str, doc_id: str, data: dict[str, Any], merge: bool = False) -> None:
        cur = self.col(collection).get(doc_id, {}) if merge else {}
        self.col(collection)[doc_id] = self._apply(cur, data)

    async def create_document(self, collection: str, doc_id: str, data: dict[str, Any]) -> bool:
        if doc_id in self.col(collection):
            return False
        await self.set_document(collection, doc_id, data)
        return True

    async def batch_set(self, collection: str, docs: dict[str, dict[str, Any]], merge: bool = False) -> None:
        for doc_id, data in docs.items():
            await self.set_document(collection, doc_id, data, merge=merge)

    async def batch_delete(self, collection: str, doc_ids: list[str]) -> None:
        for doc_id in doc_ids:
            self.col(collection).pop(doc_id, None)

    async def get_many(self, collection: str, ids: list[str]) -> dict[str, dict[str, Any]]:
        return {i: copy.deepcopy(self.col(collection)[i]) for i in ids if i in self.col(collection)}

    async def update_document(self, collection: str, doc_id: str, data: dict[str, Any]) -> None:
        if doc_id not in self.col(collection):
            raise KeyError(f"{collection}/{doc_id} not found")
        self.col(collection)[doc_id] = self._apply(self.col(collection)[doc_id], data)

    async def delete_document(self, collection: str, doc_id: str) -> None:
        self.col(collection).pop(doc_id, None)

    @staticmethod
    def _match(doc: dict[str, Any], field: str, op: str, value: Any) -> bool:
        cur = doc.get(field)
        ops: dict[str, Callable[[], bool]] = {
            "==": lambda: cur == value,
            "!=": lambda: cur != value,
            "in": lambda: cur in value,
            "not-in": lambda: cur not in value,
            "array_contains": lambda: isinstance(cur, list) and value in cur,
            "<": lambda: cur is not None and cur < value,
            "<=": lambda: cur is not None and cur <= value,
            ">": lambda: cur is not None and cur > value,
            ">=": lambda: cur is not None and cur >= value,
        }
        return ops[op]()

    async def query_documents(
        self,
        collection: str,
        filters: list[tuple[str, str, Any]] | None = None,
        order_by: str | None = None,
        order_direction: str = "ASCENDING",
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[dict[str, Any]]:
        rows = [
            {"id": doc_id, **copy.deepcopy(doc)}
            for doc_id, doc in self.col(collection).items()
            if all(self._match(doc, f, op, v) for f, op, v in filters or [])
        ]
        if order_by:
            rows.sort(key=lambda d: str(d.get(order_by) or ""), reverse=order_direction == "DESCENDING")
        rows = rows[offset or 0 :]
        return rows[:limit] if limit else rows

    async def count_documents(self, collection: str, filters: list[tuple[str, str, Any]] | None = None) -> int:
        return len(await self.query_documents(collection, filters))

    async def delete_document_atomically(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        doc = self.col(collection).pop(doc_id, None)
        return None if doc is None else {"id": doc_id, **doc}

    async def transform_document_atomically(
        self, collection: str, doc_id: str, transform: Callable[[dict[str, Any] | None], dict[str, Any] | None]
    ) -> dict[str, Any] | None:
        cur = self.col(collection).get(doc_id)
        new = transform(copy.deepcopy(cur) if cur is not None else None)
        if new is None:
            self.col(collection).pop(doc_id, None)
        else:
            self.col(collection)[doc_id] = copy.deepcopy(new)
        return new

    async def run_transaction(self, fn: Any) -> Any:
        writes: list[tuple[str, str, dict[str, Any] | None]] = []
        store = self

        class Txn:
            async def get(self, collection: str, doc_id: str) -> dict[str, Any] | None:
                return await store.get_document(collection, doc_id)

            def set(self, collection: str, doc_id: str, data: dict[str, Any]) -> None:
                writes.append((collection, doc_id, dict(data)))

            def delete(self, collection: str, doc_id: str) -> None:
                writes.append((collection, doc_id, None))

        result = await fn(Txn())
        for collection, doc_id, data in writes:
            if data is None:
                self.col(collection).pop(doc_id, None)
            else:
                self.col(collection)[doc_id] = data
        return result


# --------------------------------------------------------------------------- the live stack


class NoCatalogMatcher:
    """Matches nothing (the in-test catalog is empty): imports still run end to end."""

    async def batch_match_tracks(self, tracks: list[dict[str, Any]]) -> list[MatchedTrack]:
        return [
            MatchedTrack(
                original_artist=t["artist"],
                original_title=t["title"],
                normalized_artist=t["artist"].lower(),
                normalized_title=t["title"].lower(),
                catalog_song=None,
                match_confidence=0.0,
                playcount=t.get("playcount"),
            )
            for t in tracks
        ]


class FixtureCatalogLookup:
    """Knows Muse and Adele's "Hello" (the fake screenshot's matched candidates)."""

    def lookup_song_keys(self, keys: list[str]) -> set[str]:
        return {k for k in keys if "hello" in k and "adele" in k}

    def lookup_artist_keys(self, keys: list[str]) -> dict[str, str]:
        return {k: "Muse" for k in keys if k == "muse"}


@dataclass
class Stack:
    base: str
    fs: MemoryFirestore
    http: httpx.Client


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _stack_env(base: str) -> dict[str, str]:
    """What scripts/local-stack.sh exports, for a server on ``base``."""
    return {
        "ENVIRONMENT": "development",
        "GOOGLE_CLOUD_PROJECT": "test-project",
        "DECIDE_TEST_API": "1",
        "BIGQUERY_DISABLED": "1",
        "DECIDE_FAKE_EXTERNALS": "1",
        "DECIDE_INLINE_TASKS": "1",
        "PUBLIC_API_BASE_URL": base,
        "LISTENBRAINZ_API_BASE": f"{base}/fake/listenbrainz/1",
        "METABRAINZ_BASE_URL": f"{base}/fake/metabrainz",
        "SPOTIFY_EMBED_BASE": f"{base}/fake/spotify",
        "LASTFM_API_BASE": f"{base}/fake/lastfm/2.0/",
        "LISTENBRAINZ_OAUTH_ENABLED": "1",
        "METABRAINZ_CLIENT_ID": "fake",
        "METABRAINZ_CLIENT_SECRET": "fake",
        "DECIDE_TOKEN_KEY": TEST_ONLY_FERNET_KEY,
    }


@pytest.fixture
def stack(monkeypatch: pytest.MonkeyPatch) -> Iterator[Stack]:
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    for key, value in _stack_env(base).items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    get_backend_settings.cache_clear()
    monkeypatch.setattr(cloud_tasks_service, "_cloud_tasks_service", None)
    monkeypatch.setattr(track_matcher, "_track_matcher", NoCatalogMatcher())

    fs = MemoryFirestore()
    app = main.create_app()
    app.dependency_overrides[get_firestore] = lambda: fs
    app.dependency_overrides[get_current_user] = lambda: USER
    app.dependency_overrides[get_catalog_lookup_dep] = FixtureCatalogLookup
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="off", log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "uvicorn did not start"
        time.sleep(0.02)
    STATE.reset()
    with httpx.Client(base_url=base, timeout=30) as http:
        yield Stack(base, fs, http)
    server.should_exit = True
    thread.join(10)
    STATE.reset()
    get_backend_settings.cache_clear()


def _wait_for(predicate: Callable[[], bool], timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.05)


# --------------------------------------------------------------------------- ListenBrainz


def test_fake_lb_listens_respect_min_ts(stack: Stack) -> None:
    listens = [{"artist": "Muse", "title": f"Song {i}", "listened_at": 1_000 + i} for i in range(10)]
    r = stack.http.post("/api/test/fake-listenbrainz/users", json={"username": "e2e-lb", "listens": listens})
    assert r.status_code == 200, r.text

    async def go() -> tuple[list[int], list[int], list[int], int]:
        client = ListenBrainzClient(get_backend_settings())
        try:
            after = await client.get_listens("e2e-lb", count=3, min_ts=1_004)
            before = await client.get_listens("e2e-lb", count=3, max_ts=1_004)
            newest = await client.get_listens("e2e-lb", count=100)
            return (
                [x.listened_at for x in after],
                [x.listened_at for x in before],
                [x.listened_at for x in newest],
                await client.get_user_listen_count("e2e-lb"),
            )
        finally:
            await client.close()

    after, before, newest, count = asyncio.run(go())
    # min_ts alone: the ``count`` listens just after it (newest first), like LB.
    assert after == [1_007, 1_006, 1_005]
    assert before == [1_003, 1_002, 1_001]
    assert newest == list(range(1_009, 999, -1))
    assert count == 10


def test_fake_lb_unknown_user_is_404(stack: Stack) -> None:
    assert stack.http.get("/fake/listenbrainz/1/user/nobody-here/listen-count").status_code == 404


def test_fake_lb_stats(stack: Stack) -> None:
    listens = [
        {"artist": "Muse", "title": "Uprising", "listened_at": 10},
        {"artist": "Muse", "title": "Uprising", "listened_at": 11},
        {"artist": "Adele", "title": "Hello", "listened_at": 12},
    ]
    stack.http.post("/api/test/fake-listenbrainz/users", json={"username": "e2e-stats", "listens": listens})
    artists = stack.http.get("/fake/listenbrainz/1/stats/user/e2e-stats/artists").json()["payload"]["artists"]
    assert [(a["artist_name"], a["listen_count"]) for a in artists] == [("Muse", 2), ("Adele", 1)]
    recs = stack.http.get("/fake/listenbrainz/1/stats/user/e2e-stats/recordings").json()["payload"]["recordings"]
    assert (recs[0]["track_name"], recs[0]["listen_count"]) == ("Uprising", 2)


# --------------------------------------------------------------------------- MetaBrainz OAuth


def _start_oauth(stack: Stack) -> str:
    r = stack.http.get("/api/services/listenbrainz/oauth/start", params={"return_to": "app"})
    assert r.status_code == 200, r.text
    return str(r.json()["auth_url"])


def test_capabilities_on(stack: Stack) -> None:
    assert stack.http.get("/api/services/capabilities").json() == {
        "screenshot_import": True,
        "listenbrainz_oauth": True,
        "listenbrainz_upload_max_bytes": 30 * 1024 * 1024,
    }


def test_fake_oauth_round_trip_connects_user(stack: Stack) -> None:
    auth_url = _start_oauth(stack)
    assert auth_url.startswith(f"{stack.base}/fake/metabrainz/oauth2/authorize?")

    authorize = stack.http.get(auth_url)
    assert authorize.status_code == 302
    callback = authorize.headers["location"]
    assert callback.startswith(f"{stack.base}/api/services/listenbrainz/oauth/callback?")
    assert parse_qs(urlparse(callback).query)["code"][0].startswith("fake-")

    done = stack.http.get(callback)
    assert done.status_code == 302
    assert done.headers["location"] == "nomadkaraoke://services/listenbrainz?status=connected"
    doc = stack.fs.col("music_services")[f"{USER.id}_listenbrainz"]
    assert doc["auth"] == "oauth"
    assert doc["service_username"] == "fixture-user"


def test_fake_oauth_denied_by_knob(stack: Stack) -> None:
    assert stack.http.put("/api/test/fake-externals", json={"knobs": {"metabrainz": "deny"}}).status_code == 200
    authorize = stack.http.get(_start_oauth(stack))
    assert parse_qs(urlparse(authorize.headers["location"]).query)["error"] == ["access_denied"]
    done = stack.http.get(authorize.headers["location"])
    assert done.headers["location"] == "nomadkaraoke://services/listenbrainz?status=error&reason=denied"
    assert f"{USER.id}_listenbrainz" not in stack.fs.col("music_services")


def test_fake_token_rejects_wrong_pkce_verifier(stack: Stack) -> None:
    authorize = stack.http.get(_start_oauth(stack))
    code = parse_qs(urlparse(authorize.headers["location"]).query)["code"][0]
    r = stack.http.post(
        "/fake/metabrainz/oauth2/token",
        data={"grant_type": "authorization_code", "code": code, "code_verifier": "not-the-verifier"},
        auth=("fake", "fake"),
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


def test_fake_import_listens_completes_on_second_status_check(stack: Stack) -> None:
    stack.http.get(stack.http.get(_start_oauth(stack)).headers["location"])  # connect via OAuth
    r = stack.http.post(
        "/api/services/listenbrainz/upload-export",
        files={"file": ("my_spotify_data.zip", b"PK\x03\x04fixture", "application/zip")},
    )
    assert r.status_code == 200, r.text
    import_id = r.json()["import_id"]
    token = next(iter(STATE.tokens))
    headers = {"Authorization": f"Token {token}"}
    statuses = [
        stack.http.get(f"/fake/listenbrainz/1/import-listens/{import_id}/", headers=headers).json()["metadata"][
            "status"
        ]
        for _ in range(3)
    ]
    assert statuses == ["in_progress", "completed", "completed"]
    assert stack.http.get(f"/fake/listenbrainz/1/import-listens/{import_id}/").status_code == 401


# --------------------------------------------------------------------------- Spotify embed


def test_fake_embed_import_end_to_end(stack: Stack) -> None:
    r = stack.http.post("/api/services/spotify-playlist/import", json={"url": FIXTURE_PLAYLIST_URL})
    assert r.status_code == 200, r.text
    assert r.json()["playlist_title"] == "Fixture Karaoke Mix"
    assert r.json()["tracks_fetched"] == 4
    assert stack.fs.col("user_imports")[f"{USER.id}:spotify_playlist"]["status"] == "ok"


def test_fake_embed_unknown_playlist_404_and_changed_shape_422(stack: Stack) -> None:
    other = "https://open.spotify.com/playlist/0000000000000000000000"
    assert stack.http.post("/api/services/spotify-playlist/import", json={"url": other}).status_code == 404
    stack.http.put("/api/test/fake-externals", json={"knobs": {"spotify_embed": "changed"}})
    r = stack.http.post("/api/services/spotify-playlist/import", json={"url": FIXTURE_PLAYLIST_URL})
    assert r.status_code == 422


# --------------------------------------------------------------------------- Last.fm + inline tasks


def test_fake_lastfm_sync_inline_task_completes(stack: Stack) -> None:
    seed = {
        "username": "e2e-lastfm",
        "top_tracks": [
            {"artist": "Muse", "title": "Hysteria", "playcount": 12},
            {"artist": "Adele", "title": "Hello", "playcount": 7},
        ],
        "top_artists": [{"name": "Muse", "playcount": 40}, {"name": "Adele", "playcount": 9}],
    }
    assert stack.http.post("/api/test/fake-lastfm/users", json=seed).status_code == 200
    connect = stack.http.post("/api/services/lastfm/connect", json={"username": "e2e-lastfm"})
    assert connect.status_code == 200, connect.text

    r = stack.http.post("/api/services/sync")
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]

    _wait_for(lambda: stack.fs.col("sync_jobs").get(job_id, {}).get("status") in ("completed", "failed"))
    job = stack.fs.col("sync_jobs")[job_id]
    assert job["status"] == "completed", job
    (result,) = job["results"]
    assert result["service_type"] == "lastfm"
    assert result["error"] is None
    assert result["tracks_fetched"] == 2
    assert result["artists_stored"] == 2


def test_fake_lastfm_unknown_user_cannot_connect(stack: Stack) -> None:
    r = stack.http.post("/api/services/lastfm/connect", json={"username": "nobody-here"})
    assert r.status_code == 422


# --------------------------------------------------------------------------- Gemini


def test_fake_screenshot_extractor_and_fail_knob(stack: Stack) -> None:
    png = ("shot.png", b"\x89PNG fixture", "image/png")
    r = stack.http.post("/api/services/screenshot/extract", files=[("images", png)])
    assert r.status_code == 200, r.text
    got = {(c["kind"], c["artist"], c["title"], c["confidence"], c["matched"]) for c in r.json()["candidates"]}
    assert got == {
        ("artist", "Muse", None, 0.95, True),
        ("song", "Adele", "Hello", 0.9, True),
        ("artist", "The Invented Band", None, 0.6, False),
    }
    stack.http.put("/api/test/fake-externals", json={"knobs": {"screenshot": "fail"}})
    r = stack.http.post("/api/services/screenshot/extract", files=[("images", png)])
    assert r.status_code == 503


def test_fake_screenshot_extractor_unit() -> None:
    STATE.reset()
    raw = asyncio.run(FakeScreenshotExtractor().extract([(b"x", "image/png")]))
    assert [(c.kind, c.artist, c.title, c.confidence) for c in raw] == [
        ("artist", "Muse", None, 0.95),
        ("song", "Adele", "Hello", 0.9),
        ("artist", "The Invented Band", None, 0.6),
    ]


# --------------------------------------------------------------------------- test API + startup guards


def test_put_fake_externals_resets_state(stack: Stack) -> None:
    stack.http.post("/api/test/fake-listenbrainz/users", json={"username": "e2e-gone", "listens": []})
    assert stack.http.get("/fake/listenbrainz/1/user/e2e-gone/listen-count").status_code == 200
    r = stack.http.put("/api/test/fake-externals", json={"knobs": {}})
    assert r.status_code == 200
    assert r.json() == {"knobs": {}}
    assert stack.http.get("/fake/listenbrainz/1/user/e2e-gone/listen-count").status_code == 404
    assert stack.http.get("/fake/listenbrainz/1/user/fixture-user/listen-count").status_code == 200


def _settings(**over: Any) -> BackendSettings:
    base: dict[str, Any] = {"environment": "development", "google_cloud_project": "test-project"}
    return BackendSettings(**{**base, **over})


def test_flag_refused_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    for flags in ({"decide_fake_externals": True}, {"inline_tasks": True}):
        settings = _settings(environment="production", **flags)
        monkeypatch.setattr(main, "get_backend_settings", lambda s=settings: s)
        with pytest.raises(RuntimeError, match="production"):
            main.create_app()


def test_flag_refused_in_staging(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(environment="staging", inline_tasks=True)
    monkeypatch.setattr(main, "get_backend_settings", lambda: settings)
    with pytest.raises(RuntimeError, match="DECIDE_INLINE_TASKS"):
        main.create_app()


def test_committed_test_token_key_refused_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    key = next(
        line.split('"')[1]
        for line in (Path(__file__).parents[2] / "scripts" / "local-stack.sh").read_text().splitlines()
        if line.startswith("LOCAL_STACK_TOKEN_KEY=")
    )
    settings = _settings(environment="production", decide_token_key=key)
    monkeypatch.setattr(main, "get_backend_settings", lambda: settings)
    with pytest.raises(RuntimeError, match="test-only"):
        main.create_app()
    # The same key is fine outside production (it is the local stack's key).
    ok = _settings(decide_token_key=key)
    monkeypatch.setattr(main, "get_backend_settings", lambda: ok)
    main.create_app()


def test_inline_task_notes_ignored_delay(caplog: pytest.LogCaptureFixture) -> None:
    service = cloud_tasks_service.CloudTasksService(_settings(inline_tasks=True))
    service._inline_transport = httpx.MockTransport(lambda r: httpx.Response(200, json={}))

    async def go() -> None:
        with caplog.at_level("INFO"):
            service.create_sync_task(job_id="job-2", user_id="u", delay_seconds=30)
        await asyncio.gather(*cloud_tasks_service._INLINE_TASKS)

    asyncio.run(go())
    assert "delay_seconds=30 ignored" in caplog.text


def test_fake_routes_not_mounted_without_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(decide_test_api=True)
    monkeypatch.setattr(main, "get_backend_settings", lambda: settings)
    client = TestClient(main.create_app())
    assert client.get("/fake/listenbrainz/1/user/fixture-user/listen-count").status_code == 404
    # The seeding routes exist with the test API, but refuse while the fakes are off.
    r = client.put("/api/test/fake-externals", json={"knobs": {}})
    assert r.status_code == 409


def test_inline_task_posts_to_public_api_base(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200, json={"status": "completed"})

    service = cloud_tasks_service.CloudTasksService(
        _settings(inline_tasks=True, public_api_base_url="http://127.0.0.1:9999/")
    )
    service._inline_transport = httpx.MockTransport(handler)

    async def go() -> str:
        name = service.create_sync_task(job_id="job-1", user_id="u-1")
        await asyncio.gather(*cloud_tasks_service._INLINE_TASKS)
        return name

    assert asyncio.run(go()) == "inline:job-1"
    assert seen == [("http://127.0.0.1:9999/internal/sync/process", {"job_id": "job-1", "user_id": "u-1"})]
