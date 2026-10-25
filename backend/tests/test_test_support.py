"""/api/test/* local-stack support routes: mounted only with DECIDE_TEST_API, never in production."""

from __future__ import annotations

import hashlib
import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.api.deps import get_catalog_store_dep, get_current_user, get_firestore
from backend.api.routes.admin_voice import get_recordings_bucket
from backend.config import BackendSettings
from backend.services.voice_recording_service import LocalBucket, user_prefix
from backend.tests.recs_v2.conftest import catalog_dir, reader  # noqa: F401  (fixtures)
from backend.tests.recs_v2.test_personas import _rock_artists
from karaoke_decide.catalog.reader import CatalogReader
from karaoke_decide.core.models import User

EMAIL = "Maestro@Example.com"
EMAIL_HASH = hashlib.sha256(EMAIL.lower().encode()).hexdigest()


class FakeFirestore:
    """In-memory Firestore covering the calls the test API and recs v2 make."""

    def __init__(self) -> None:
        self.cols: dict[str, dict[str, dict[str, Any]]] = {}

    async def get_document(self, col: str, doc_id: str) -> dict[str, Any] | None:
        d = self.cols.get(col, {}).get(doc_id)
        return None if d is None else {"id": doc_id, **d}

    async def set_document(self, col: str, doc_id: str, data: dict[str, Any], merge: bool = False) -> None:
        if merge:
            self.cols.setdefault(col, {}).setdefault(doc_id, {}).update(data)
        else:
            self.cols.setdefault(col, {})[doc_id] = dict(data)

    async def create_document(self, col: str, doc_id: str, data: dict[str, Any]) -> bool:
        if doc_id in self.cols.get(col, {}):
            return False
        self.cols.setdefault(col, {})[doc_id] = dict(data)
        return True

    async def update_document(self, col: str, doc_id: str, data: dict[str, Any]) -> None:
        self.cols[col][doc_id].update(data)

    async def delete_document(self, col: str, doc_id: str) -> None:
        self.cols.get(col, {}).pop(doc_id, None)

    async def batch_set(self, col: str, docs: dict[str, dict[str, Any]], merge: bool = False) -> None:
        for doc_id, data in docs.items():
            await self.set_document(col, doc_id, data, merge=merge)

    async def run_transaction(self, fn: Any) -> Any:
        writes: list[tuple[str, str, dict[str, Any] | None]] = []
        cols = self.cols

        class Txn:
            async def get(self, col: str, doc_id: str) -> dict[str, Any] | None:
                d = cols.get(col, {}).get(doc_id)
                return None if d is None else dict(d)

            def set(self, col: str, doc_id: str, data: dict[str, Any]) -> None:
                writes.append((col, doc_id, dict(data)))

            def delete(self, col: str, doc_id: str) -> None:
                writes.append((col, doc_id, None))

        result = await fn(Txn())
        for col, doc_id, data in writes:
            if data is None:
                cols.get(col, {}).pop(doc_id, None)
            else:
                cols.setdefault(col, {})[doc_id] = data
        return result

    async def batch_delete(self, col: str, doc_ids: list[str]) -> None:
        for doc_id in doc_ids:
            await self.delete_document(col, doc_id)

    async def query_documents(
        self,
        col: str,
        filters: list[tuple[str, str, Any]] | None = None,
        limit: int | None = None,
        offset: int | None = None,
        **_: Any,
    ) -> list[dict[str, Any]]:
        rows = [
            {"id": i, **d}
            for i, d in self.cols.get(col, {}).items()
            if all(_matches(d, f, op, v) for f, op, v in filters or [])
        ]
        rows = rows[offset or 0 :]
        return rows[:limit] if limit else rows

    async def count_documents(self, col: str, filters: list[tuple[str, str, Any]] | None = None) -> int:
        return len(await self.query_documents(col, filters=filters))


def _matches(doc: dict[str, Any], field: str, op: str, value: Any) -> bool:
    if op == "array_contains":
        return value in (doc.get(field) or [])
    return bool(doc.get(field) == value)


def _app_with(monkeypatch: pytest.MonkeyPatch, *, flag: bool, environment: str) -> TestClient:
    settings = BackendSettings(environment=environment, decide_test_api=flag, google_cloud_project="test-project")  # type: ignore[arg-type]
    monkeypatch.setattr(main, "get_backend_settings", lambda: settings)
    return TestClient(main.create_app())


@pytest.fixture
def fs() -> FakeFirestore:
    return FakeFirestore()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, fs: FakeFirestore) -> Iterator[TestClient]:
    c = _app_with(monkeypatch, flag=True, environment="development")
    c.app.dependency_overrides[get_firestore] = lambda: fs  # type: ignore[attr-defined]
    yield c
    c.app.dependency_overrides.clear()  # type: ignore[attr-defined]


def test_not_mounted_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _app_with(monkeypatch, flag=False, environment="development")
    assert c.post("/api/test/profile/reset", json={"email": EMAIL}).status_code == 404
    assert c.get("/api/test/profile", params={"email": EMAIL}).status_code == 404


def test_not_mounted_in_production_even_with_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _app_with(monkeypatch, flag=True, environment="production")
    assert c.post("/api/test/profile/seed", json={"email": EMAIL}).status_code == 404
    assert c.post("/api/test/profile/reset", json={"email": EMAIL}).status_code == 404
    assert c.get("/api/test/profile", params={"email": EMAIL}).status_code == 404


def test_flag_parses_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECIDE_TEST_API", "1")
    assert BackendSettings().decide_test_api is True


SEED = {
    "email": EMAIL,
    "genres": ["rock", "pop"],
    "decades": ["1990s"],
    "energy": "high",
    "artists": [{"name": "Invented Band", "affinity": "love"}, {"name": "Other Act", "affinity": "like"}],
    "loved": ["invented band|first song"],
    "hidden": ["other act|second song"],
}


def test_seed_then_read(client: TestClient, fs: FakeFirestore) -> None:
    r = client.post("/api/test/profile/seed", json=SEED)
    assert r.status_code == 200
    user_id = r.json()["user_id"]
    assert user_id.startswith("user_")
    assert fs.cols["decide_users"][EMAIL_HASH]["user_id"] == user_id

    body = client.get("/api/test/profile", params={"email": EMAIL}).json()
    assert body == {
        "user_id": user_id,
        "quiz_completed": True,
        "genres": ["rock", "pop"],
        "decades": ["1990s"],
        "energy": "high",
        "artists": [{"name": "Invented Band", "affinity": "love"}, {"name": "Other Act", "affinity": "like"}],
        "loved": ["invented band|first song"],
        "hidden": ["other act|second song"],
        "voice_profile": None,
    }


def test_seed_is_idempotent_and_replaces(client: TestClient) -> None:
    first = client.post("/api/test/profile/seed", json=SEED).json()["user_id"]
    second = client.post("/api/test/profile/seed", json={"email": EMAIL, "quiz_completed": False}).json()["user_id"]
    assert first == second
    body = client.get("/api/test/profile", params={"email": EMAIL}).json()
    assert body["quiz_completed"] is False
    assert (body["genres"], body["artists"], body["loved"], body["hidden"], body["energy"]) == ([], [], [], [], None)


def test_read_unknown_email_is_empty_profile(client: TestClient) -> None:
    body = client.get("/api/test/profile", params={"email": "nobody@example.com"}).json()
    assert body["user_id"] is None
    assert body["quiz_completed"] is False
    assert body["artists"] == []


def test_reset_clears(client: TestClient, fs: FakeFirestore) -> None:
    user_id = client.post("/api/test/profile/seed", json=SEED).json()["user_id"]
    fs.cols.setdefault("user_artists", {})[f"{user_id}:x"] = {"user_id": user_id, "artist_name": "X"}
    fs.cols.setdefault("user_songs", {})[f"{user_id}:s1"] = {"user_id": user_id, "source": "quiz"}
    fs.cols.setdefault("quiz_progress", {})[user_id] = {"user_id": user_id}
    fs.cols["user_songs"]["someone_else:s1"] = {"user_id": "someone_else"}

    assert client.post("/api/test/profile/reset", json={"email": EMAIL}).status_code == 204

    assert EMAIL_HASH not in fs.cols["decide_users"]
    assert not fs.cols.get("user_artists")
    assert not fs.cols.get("user_hidden_songs")
    assert not fs.cols.get("quiz_progress")
    assert list(fs.cols["user_songs"]) == ["someone_else:s1"]
    body = client.get("/api/test/profile", params={"email": EMAIL}).json()
    assert body["user_id"] is None


def test_reset_unknown_email_is_noop(client: TestClient) -> None:
    assert client.post("/api/test/profile/reset", json={"email": "nobody@example.com"}).status_code == 204


def test_seeded_profile_drives_v2(client: TestClient, fs: FakeFirestore, reader: CatalogReader) -> None:  # noqa: F811
    class Store:
        run = "fixture-run"

        def require(self) -> CatalogReader:
            return reader

    artists = _rock_artists(reader, 2)
    seed = {
        "email": EMAIL,
        "genres": ["rock"],
        "decades": ["1990s"],
        "energy": "high",
        "artists": [{"name": a.name, "affinity": "love"} for a in artists.values()],
    }
    user_id = client.post("/api/test/profile/seed", json=seed).json()["user_id"]
    app = client.app
    app.dependency_overrides[get_current_user] = lambda: User(id=user_id)  # type: ignore[attr-defined]
    app.dependency_overrides[get_catalog_store_dep] = lambda: Store()  # type: ignore[attr-defined]

    r = client.get("/api/my/recommendations/v2")
    assert r.status_code == 200
    body = r.json()
    assert body["has_profile"] is True
    assert "artists_you_love" in [s["id"] for s in body["sections"]]


def test_read_karaoke_log(client: TestClient, fs: FakeFirestore) -> None:
    user_id = client.post("/api/test/profile/seed", json={"email": EMAIL}).json()["user_id"]
    log = fs.cols.setdefault("karaoke_log", {})
    log[f"{user_id}:kj_1"] = {
        "id": "kj_1",
        "user_id": user_id,
        "email_hash": EMAIL_HASH,
        "source": "kjbox",
        "state": "sung",
        "artist": "Invented Band",
        "title": "First Song",
        "song_key": "invented band|first song",
        "song_key_status": "resolved",
        "updated_at": "2026-10-21T21:00:00+00:00",
        "survey": {"feel": "great"},
        "counted": {"collection": "user_songs", "doc_id": "x"},
    }
    log[f"{user_id}:app_2"] = {
        "id": "app_2",
        "user_id": user_id,
        "source": "app",
        "state": "sung",
        "artist": "Other Act",
        "title": "Second Song",
        "song_key_status": "unresolved",
        "deleted": True,
        "updated_at": "2026-10-21T22:00:00+00:00",
    }
    log["someone_else:kj_1"] = {"id": "kj_1", "user_id": "someone_else", "source": "kjbox", "state": "sung"}
    fs.cols.setdefault("user_songs", {})[f"{user_id}:sk:1"] = {
        "user_id": user_id,
        "song_key": "invented band|first song",
        "artist": "Invented Band",
        "title": "First Song",
        "source": "karaoke_log",
        "times_sung": 1,
        "last_sung_at": "2026-10-21T21:00:00+00:00",
    }
    fs.cols.setdefault("user_karaoke_unresolved", {})[f"{user_id}:u1"] = {
        "user_id": user_id,
        "artist": "Other Act",
        "title": "Second Song",
        "times_sung": 1,
    }

    body = client.get("/api/test/karaoke-log", params={"email": EMAIL.upper()}).json()
    assert body["user_id"] == user_id
    assert [(e["id"], e["deleted"]) for e in body["entries"]] == [("app_2", True), ("kj_1", False)]
    assert body["entries"][1]["survey"]["feel"] == "great"
    assert body["sung"] == [
        {
            "song_key": "invented band|first song",
            "artist": "Invented Band",
            "title": "First Song",
            "times_sung": 1,
            "last_sung_at": "2026-10-21T21:00:00+00:00",
            "source": "karaoke_log",
        }
    ]
    assert body["unresolved"] == [{"artist": "Other Act", "title": "Second Song", "times_sung": 1}]

    empty = client.get("/api/test/karaoke-log", params={"email": "nobody@example.com"}).json()
    assert empty == {"user_id": None, "entries": [], "sung": [], "unresolved": []}

    assert client.post("/api/test/profile/reset", json={"email": EMAIL}).status_code == 204
    assert list(fs.cols["karaoke_log"]) == ["someone_else:kj_1"]
    assert not fs.cols["user_karaoke_unresolved"]


# ---------------------------------------------------------------------------------------------
# Voice session: /api/test/voice/{sessions,recordings,reset}
# ---------------------------------------------------------------------------------------------


@pytest.fixture
def voice_client(
    monkeypatch: pytest.MonkeyPatch, fs: FakeFirestore, tmp_path: Path
) -> Iterator[tuple[TestClient, LocalBucket]]:
    bucket = LocalBucket(tmp_path / "recordings")
    c = _app_with(monkeypatch, flag=True, environment="development")
    c.app.dependency_overrides[get_firestore] = lambda: fs  # type: ignore[attr-defined]
    c.app.dependency_overrides[get_recordings_bucket] = lambda: bucket  # type: ignore[attr-defined]
    yield c, bucket
    c.app.dependency_overrides.clear()  # type: ignore[attr-defined]


def _seed_voice_user(c: TestClient, fs: FakeFirestore, bucket: LocalBucket) -> str:
    user_id = c.post("/api/test/profile/seed", json=SEED).json()["user_id"]
    other = "someone_else"
    fs.cols["voice_sessions"] = {
        "s1": {"user_id": user_id, "state": "done", "done_reason": "enough"},
        "s2": {"user_id": user_id, "state": "active"},
        "sx": {"user_id": other, "state": "active"},
    }
    fs.cols["voice_attempts"] = {
        "s1:1": {"user_id": user_id, "session_id": "s1", "n": 1},
        "s1:2": {"user_id": user_id, "session_id": "s1", "n": 2, "recording_id": "r1"},
        "sx:1": {"user_id": other, "session_id": "sx", "n": 1},
    }
    name = f"{user_prefix(user_id)}s1/2.webm"
    bucket.blob(name).upload_from_file(io.BytesIO(b"synthetic"))
    fs.cols["voice_recordings"] = {
        "r1": {"user_id": user_id, "gcs_path": name},
        "rx": {"user_id": other, "gcs_path": f"{user_prefix(other)}sx/1.webm"},
    }
    fs.cols["voice_labels"] = {"l1": {"user_id": user_id, "song_key": "a|b"}, "lx": {"user_id": other}}
    fs.cols["voice_recording_purges"] = {user_id: {"purged_at": "2026-10-01T00:00:00+00:00"}}
    fs.cols.setdefault("user_songs", {}).update(
        {
            f"{user_id}:only": {"user_id": user_id, "source": "voice_session", "sources": ["voice_session"]},
            f"{user_id}:mixed": {
                "user_id": user_id,
                "source": "spotify_export",
                "sources": ["spotify_export", "voice_session"],
                "times_sung": 1,
                "session_feel": "loved",
                "session_would_sing": True,
                "session_key_hint": "down",
                "session_tags": ["fun"],
                "vocal_comfort": "easy",
            },
            f"{user_id}:plain": {"user_id": user_id, "source": "quiz", "sources": ["quiz"]},
            f"{other}:only": {"user_id": other, "source": "voice_session", "sources": ["voice_session"]},
        }
    )
    return str(user_id)


def test_voice_test_api_not_mounted_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for flag, env in ((False, "development"), (True, "production")):
        c = _app_with(monkeypatch, flag=flag, environment=env)
        assert c.get("/api/test/voice/sessions", params={"email": EMAIL}).status_code == 404
        assert c.get("/api/test/voice/recordings", params={"email": EMAIL}).status_code == 404
        assert c.post("/api/test/voice/reset", json={"email": EMAIL}).status_code == 404


def test_voice_sessions_and_recordings_read(voice_client: tuple[TestClient, LocalBucket], fs: FakeFirestore) -> None:
    c, bucket = voice_client
    _seed_voice_user(c, fs, bucket)
    rows = {r["session_id"]: r for r in c.get("/api/test/voice/sessions", params={"email": EMAIL}).json()}
    assert rows == {
        "s1": {"session_id": "s1", "state": "done", "attempts": 2, "done_reason": "enough"},
        "s2": {"session_id": "s2", "state": "active", "attempts": 0, "done_reason": None},
    }
    assert c.get("/api/test/voice/recordings", params={"email": EMAIL}).json() == {"count": 1}


def test_voice_reads_for_unknown_email_are_empty(voice_client: tuple[TestClient, LocalBucket]) -> None:
    c, _ = voice_client
    assert c.get("/api/test/voice/sessions", params={"email": "nobody@example.com"}).json() == []
    assert c.get("/api/test/voice/recordings", params={"email": "nobody@example.com"}).json() == {"count": 0}
    assert c.post("/api/test/voice/reset", json={"email": "nobody@example.com"}).status_code == 204


def test_voice_reset_clears_everything(voice_client: tuple[TestClient, LocalBucket], fs: FakeFirestore) -> None:
    c, bucket = voice_client
    user_id = _seed_voice_user(c, fs, bucket)

    assert c.post("/api/test/voice/reset", json={"email": EMAIL}).status_code == 204

    assert list(fs.cols["voice_sessions"]) == ["sx"]
    assert list(fs.cols["voice_attempts"]) == ["sx:1"]
    assert list(fs.cols["voice_recordings"]) == ["rx"]
    assert list(fs.cols["voice_labels"]) == ["lx"]
    assert not fs.cols["voice_recording_purges"]  # a fresh run may upload again
    assert [b.name for b in bucket.list_blobs()] == []
    assert c.get("/api/test/voice/sessions", params={"email": EMAIL}).json() == []
    assert c.get("/api/test/voice/recordings", params={"email": EMAIL}).json() == {"count": 0}

    songs = fs.cols["user_songs"]
    assert f"{user_id}:only" not in songs  # only source was voice_session: row deleted
    assert "someone_else:only" in songs  # another user's row is untouched
    assert songs[f"{user_id}:plain"] == {"user_id": user_id, "source": "quiz", "sources": ["quiz"]}
    mixed = songs[f"{user_id}:mixed"]  # imported row survives: session fields and the source label go
    assert mixed["source"] == "spotify_export"
    assert mixed["sources"] == ["spotify_export"]
    assert not [k for k in mixed if k.startswith("session_")]
    assert mixed["times_sung"] == 1


def test_voice_reset_mixed_row_whose_source_is_voice_session(
    voice_client: tuple[TestClient, LocalBucket], fs: FakeFirestore
) -> None:
    c, bucket = voice_client
    user_id = _seed_voice_user(c, fs, bucket)
    fs.cols["user_songs"][f"{user_id}:mixed"]["source"] = "voice_session"
    c.post("/api/test/voice/reset", json={"email": EMAIL})
    mixed = fs.cols["user_songs"][f"{user_id}:mixed"]
    assert mixed["source"] == "spotify_export" and mixed["sources"] == ["spotify_export"]


def test_reset_clears_playlists(client: TestClient, fs: FakeFirestore) -> None:
    user_id = client.post("/api/test/profile/seed", json=SEED).json()["user_id"]
    fs.cols.setdefault("playlists", {})["pl_mine12345"] = {"id": "pl_mine12345", "user_id": user_id, "items": []}
    fs.cols["playlists"]["pl_other1234"] = {"id": "pl_other1234", "user_id": "someone_else"}

    assert client.post("/api/test/profile/reset", json={"email": EMAIL}).status_code == 204

    assert list(fs.cols["playlists"]) == ["pl_other1234"]


def test_seed_karaoke_log(client: TestClient) -> None:
    entries = [
        {
            "id": "app_seed0001",
            "state": "sung",
            "artist": "Invented Band",
            "title": "First Song",
            "song_key": "invented band|first song",
            "times": 2,
            "origin": "backfill",
            "survey": {"feel": "great", "vibes": ["party"]},
        },
        {
            "id": "app_seed0002",
            "state": "sung",
            "artist": "Other Act",
            "title": "Second Song",
            "survey": {"feel": "bad", "reasons": ["too_hard"]},
        },
    ]
    resp = client.post("/api/test/karaoke-log/seed", json={"email": EMAIL, "entries": entries})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 2 and body["user_id"]

    got = client.get("/api/test/karaoke-log", params={"email": EMAIL}).json()
    assert got["user_id"] == body["user_id"]
    assert sorted(e["id"] for e in got["entries"]) == ["app_seed0001", "app_seed0002"]
    assert any(s["artist"] == "Invented Band" and s["times_sung"] == 2 for s in got["sung"])


def test_seed_karaoke_log_rejects_non_app_ids(client: TestClient) -> None:
    resp = client.post(
        "/api/test/karaoke-log/seed",
        json={"email": EMAIL, "entries": [{"id": "kj_1", "state": "sung", "artist": "A", "title": "B"}]},
    )
    assert resp.status_code == 400
    resp = client.post(
        "/api/test/karaoke-log/seed",
        json={"email": EMAIL, "entries": [{"id": "app_seed0003", "state": "sung", "artist": "A"}]},
    )
    assert resp.status_code == 400


def test_seed_history_creates_playable_recording(
    voice_client: tuple[TestClient, LocalBucket], fs: FakeFirestore
) -> None:
    c, bucket = voice_client
    quiz = {"feel": "loved", "position": "too_high", "knew": "well"}
    body = {
        "email": EMAIL,
        "sessions": [
            {"state": "done", "attempts": [{"song_key": "artist a|song a", "artist": "Artist A", "title": "Song A",
                                            "quiz": quiz, "recording": False}]},
            {
                "state": "done",
                "result_summary": {"voice_type": "baritone", "between": None, "confidence_label": "low", "top_p": 0.4,
                                   "low": 45, "high": 69, "comfort_low": 50, "comfort_high": 61, "attempts": 2,
                                   "evidence_attempts": 2, "profile_saved": False},
                "attempts": [
                    {"song_key": "artist b|song b", "artist": "Artist B", "title": "Song B", "quiz": quiz,
                     "pitch": {"voiced_s": 30, "held_count": 3, "held_low": 50, "held_high": 66, "p10": 52, "p50": 57,
                               "p90": 63}, "recording": True},
                    {"song_key": "artist c|song c", "artist": "Artist C", "title": "Song C", "quiz": quiz,
                     "recording": False, "excluded": True},
                ],
            },
        ],
    }  # fmt: skip
    r = c.post("/api/test/voice/seed-history", json=body)
    assert r.status_code == 200, r.text
    seeded = r.json()
    user_id = seeded["user_id"]
    first, second = seeded["sessions"]
    assert second["attempt_ids"] == [f"{second['session_id']}:1", f"{second['session_id']}:2"]
    (rid,) = second["recording_ids"]
    assert first["recording_ids"] == []
    assert [b.name for b in bucket.list_blobs()] == [f"{user_prefix(user_id)}{second['session_id']}/1.wav"]

    c.app.dependency_overrides[get_current_user] = lambda: User(id=user_id, email=EMAIL)  # type: ignore[attr-defined]
    history = c.get("/api/voice/history")
    assert history.status_code == 200, history.text
    sessions = history.json()["sessions"]
    assert [s["session_id"] for s in sessions] == [second["session_id"], first["session_id"]]  # newest first
    assert sessions[0]["result"]["voice_type"] == "baritone" and sessions[0]["result"]["session_id"] == rid[:-2]
    a1, a2 = sessions[0]["attempts"]
    assert a1["recording"]["recording_id"] == rid and a1["recording"]["content_type"] == "audio/wav"
    assert a2["excluded"] is True and a2["recording"] is None and a2["feel"] == "loved"
    audio = c.get(f"/api/voice/recordings/{rid}/audio")
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/wav"
    assert audio.content[:4] == b"RIFF" and len(audio.content) == a1["recording"]["bytes"]

    # seeding again replaces the user's voice history
    assert c.post("/api/test/voice/seed-history", json={"email": EMAIL, "sessions": []}).status_code == 200
    assert c.get("/api/voice/history").json() == {"sessions": []}
    assert list(bucket.list_blobs()) == []
