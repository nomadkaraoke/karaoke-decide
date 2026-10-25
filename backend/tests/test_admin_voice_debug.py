"""Admin voice debug endpoints: coverage, a user's songs (markers + labels + attempts), recording audio."""

from __future__ import annotations

import asyncio
import hashlib
import io
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_auth_service_dep, get_firestore, get_optional_user, get_settings
from backend.api.routes import admin_voice
from backend.api.routes.admin_voice import (
    LABELS_COLLECTION,
    fetch_spotify_budget,
    get_recordings_bucket,
    get_voice_store,
    label_doc_id,
)
from backend.config import BackendSettings
from backend.main import app
from backend.services.auth_service import AuthService
from backend.services.voice_data_store import VoiceDataStore
from backend.services.voice_recording_service import recording_object_name
from backend.tests.test_karaoke_log_service import MemoryFirestore
from karaoke_decide.core.models import User
from karaoke_decide.voice_pool import fixtures
from karaoke_decide.voice_pool.queue import FETCH_COLLECTION

TOKEN = "script-token-1"
EMAIL = "andrew@example.com"
USER_ID = "user_andrew"
STARLIGHT = fixtures.STARLIGHT
UNPOOLED = "nobody known|invented tune"
REC_ID = "rec1"
REC_PATH = f"voice-recordings/{USER_ID}/sess1/1.m4a"
AUDIO = b"\x00\x01fake-aac" * 50_000  # > one chunk


class FakeBlob:
    def __init__(self, bucket: FakeBucket, name: str) -> None:
        self.bucket, self.name = bucket, name

    def open(self, mode: str = "rb", **_: Any) -> io.BytesIO:
        assert mode == "rb"
        if self.name not in self.bucket.objects:
            raise FileNotFoundError(self.name)
        return io.BytesIO(self.bucket.objects[self.name])


class FakeBucket:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(self, name)


@pytest.fixture(scope="module")
def voice_store(tmp_path_factory: pytest.TempPathFactory) -> Iterator[VoiceDataStore]:
    db = fixtures.build(tmp_path_factory.mktemp("voice"))
    store = VoiceDataStore()
    store.load(BackendSettings(voice_local_dir=str(db.parent)))
    assert store.status == "ready"
    yield store
    store.close()


@pytest.fixture
def fs() -> MemoryFirestore:
    fs = MemoryFirestore()
    fs.cols["decide_users"] = {
        hashlib.sha256(EMAIL.encode()).hexdigest(): {"user_id": USER_ID, "email": EMAIL, "is_guest": False}
    }
    return fs


@pytest.fixture
def bucket() -> FakeBucket:
    b = FakeBucket()
    b.objects[REC_PATH] = AUDIO
    return b


@pytest.fixture
def settings() -> BackendSettings:
    return BackendSettings(
        environment="development",
        google_cloud_project="test-project",
        jwt_secret="test-jwt-secret-key-for-testing-only",
        admin_tokens=[TOKEN],
        flacfetch_api_url="",
        flacfetch_api_key="",
    )


@pytest.fixture
def client(
    fs: MemoryFirestore, voice_store: VoiceDataStore, bucket: FakeBucket, settings: BackendSettings
) -> Iterator[TestClient]:
    auth = AuthService(settings, fs, email_service=MagicMock())  # type: ignore[arg-type]
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_firestore] = lambda: fs
    app.dependency_overrides[get_auth_service_dep] = lambda: auth
    app.dependency_overrides[get_optional_user] = lambda: None
    app.dependency_overrides[get_voice_store] = lambda: voice_store
    app.dependency_overrides[get_recordings_bucket] = lambda: bucket
    yield TestClient(app)
    app.dependency_overrides.clear()


def _tok() -> dict[str, str]:
    return {"X-Admin-Token": TOKEN}


URLS = (
    "/api/admin/voice/coverage",
    f"/api/admin/voice/users/{EMAIL}/songs",
    f"/api/admin/voice/recordings/{REC_ID}/audio",
)


def test_admin_endpoints_403_for_users(client: TestClient) -> None:
    for url in URLS:
        assert client.get(url).status_code == 403, url
    app.dependency_overrides[get_optional_user] = lambda: User(id="plain", email="p@example.com")
    for url in URLS:
        assert client.get(url).status_code == 403, url
    app.dependency_overrides[get_optional_user] = lambda: User(id="adm", email="a@example.com", is_admin=True)
    assert client.get(URLS[0]).status_code == 200


# ---------------------------------------------------------------- coverage


def test_coverage_counts_strata_and_queue(client: TestClient, fs: MemoryFirestore) -> None:
    fs.cols[FETCH_COLLECTION] = {
        "a": {"status": "queued"},
        "b": {"status": "queued"},
        "c": {"status": "analysed"},
        "d": {"status": "failed"},
    }
    res = client.get("/api/admin/voice/coverage", headers=_tok())
    assert res.status_code == 200
    body = res.json()
    n = fixtures.PER_TYPE * 6
    assert body["run"] == "fixture" and body["songs"] == n and body["analysed"] == n
    assert body["queue"] == {"queued": 2, "analysed": 1, "failed": 1}
    assert body["budget"] is None  # flacfetch not configured on the API
    dims = body["per_stratum_dimension"]
    assert dims["other"]["fixture"] == {"songs": n, "analysed": n}
    assert dims["other"]["baritone"] == {"songs": fixtures.PER_TYPE, "analysed": fixtures.PER_TYPE}


def test_coverage_splits_keyed_strata() -> None:
    rows = [("gender=male|register=low", True), ("gender=female|register=low", False), (None, False)]
    assert admin_voice._per_dimension(rows) == {
        "gender": {"male": {"songs": 1, "analysed": 1}, "female": {"songs": 1, "analysed": 0}},
        "register": {"low": {"songs": 2, "analysed": 1}},
        "other": {"none": {"songs": 1, "analysed": 0}},
    }


def test_coverage_without_voice_db(client: TestClient) -> None:
    app.dependency_overrides[get_voice_store] = lambda: VoiceDataStore()
    body = client.get("/api/admin/voice/coverage", headers=_tok()).json()
    assert body == {"run": None, "songs": 0, "analysed": 0, "per_stratum_dimension": {}, "queue": {}, "budget": None}


def test_coverage_includes_flacfetch_budget(
    client: TestClient, settings: BackendSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_budget(s: BackendSettings, **_: Any) -> dict[str, Any]:
        assert s is settings
        return {"batch_s": 100, "batch_allowed_now": True}

    monkeypatch.setattr(admin_voice, "fetch_spotify_budget", fake_budget)
    body = client.get("/api/admin/voice/coverage", headers=_tok()).json()
    assert body["budget"] == {"batch_s": 100, "batch_allowed_now": True}


def test_fetch_spotify_budget(caplog: pytest.LogCaptureFixture) -> None:
    configured = BackendSettings(flacfetch_api_url="https://ff.example/", flacfetch_api_key="k")
    seen: list[httpx.Request] = []

    def ok(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"batch_s": 5})

    got = asyncio.run(fetch_spotify_budget(configured, transport=httpx.MockTransport(ok)))
    assert got == {"batch_s": 5}
    assert str(seen[0].url) == "https://ff.example/spotify/budget" and seen[0].headers["X-API-Key"] == "k"

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    assert asyncio.run(fetch_spotify_budget(configured, transport=httpx.MockTransport(down))) is None
    bad = httpx.MockTransport(lambda r: httpx.Response(500, text="oops"))
    assert asyncio.run(fetch_spotify_budget(configured, transport=bad)) is None
    assert asyncio.run(fetch_spotify_budget(BackendSettings(flacfetch_api_url="", flacfetch_api_key=""))) is None
    assert (
        asyncio.run(fetch_spotify_budget(BackendSettings(flacfetch_api_url="https://x", flacfetch_api_key=""))) is None
    )
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


# ---------------------------------------------------------------- user songs


def test_admin_songs_joins_markers_labels_attempts(client: TestClient, fs: MemoryFirestore) -> None:
    fs.cols[LABELS_COLLECTION] = {
        label_doc_id(USER_ID, STARLIGHT): {
            "user_id": USER_ID,
            "song_key": STARLIGHT,
            "artist": "Muse",
            "title": "Starlight",
            "good": "yes",
            "tags": ["good_if_warm"],
            "notes": "good if warm",
        },
        label_doc_id(USER_ID, UNPOOLED): {
            "user_id": USER_ID,
            "song_key": UNPOOLED,
            "artist": "Nobody Known",
            "title": "Invented Tune",
            "good": "maybe",
            "tags": [],
            "notes": "",
        },
        label_doc_id("someone_else", STARLIGHT): {"user_id": "someone_else", "song_key": STARLIGHT, "good": "no"},
    }
    t1 = datetime(2026, 10, 22, 20, 0, tzinfo=UTC)
    t2 = datetime(2026, 10, 22, 20, 5, tzinfo=UTC)
    quiz = {"feel": "loved", "position": "comfortable"}
    fs.cols["voice_attempts"] = {
        "sess1:2": {
            "user_id": USER_ID,
            "session_id": "sess1",
            "n": 2,
            "song_key": STARLIGHT,
            "video_id": "FIXTURE1",
            "quiz": quiz,
            "context": {"posture": "standing"},
            "pitch": {"p50": 55.0},
            "started_at": t2,
            "recording_id": REC_ID,
        },
        "sess1:1": {
            "user_id": USER_ID,
            "session_id": "sess1",
            "n": 1,
            "song_key": STARLIGHT,
            "video_id": "FIXTURE1",
            "quiz": quiz,
            "context": None,
            "pitch": None,
            "started_at": t1,
            "recording_id": None,
        },
        "other:1": {"user_id": "someone_else", "session_id": "other", "n": 1, "song_key": STARLIGHT, "started_at": t1},
    }
    res = client.get(f"/api/admin/voice/users/{EMAIL}/songs", headers=_tok())
    assert res.status_code == 200
    songs = {s["song_key"]: s for s in res.json()}
    assert set(songs) == {STARLIGHT, UNPOOLED}

    star = songs[STARLIGHT]
    assert star["artist"] == "Muse" and star["title"] == "Starlight"
    assert star["markers"]["song_key"] == STARLIGHT and star["markers"]["p50"] is not None
    assert star["label"] == {"good": "yes", "tags": ["good_if_warm"], "notes": "good if warm"}
    assert [a["recording_id"] for a in star["attempts"]] == [None, REC_ID]  # by time
    assert star["attempts"][1] == {
        "session_id": "sess1",
        "at": t2.isoformat().replace("+00:00", "Z"),
        "quiz": quiz,
        "context": {"posture": "standing"},
        "pitch": {"p50": 55.0},
        "recording_id": REC_ID,
    }
    assert star["prediction"] is None

    other = songs[UNPOOLED]
    assert other["markers"] is None and other["attempts"] == [] and other["label"]["good"] == "maybe"
    assert other["artist"] == "Nobody Known"


def test_admin_songs_without_collections_or_voice_db(client: TestClient) -> None:
    assert client.get(f"/api/admin/voice/users/{EMAIL}/songs", headers=_tok()).json() == []
    app.dependency_overrides[get_voice_store] = lambda: VoiceDataStore()
    assert client.get(f"/api/admin/voice/users/{EMAIL}/songs", headers=_tok()).status_code == 200


def test_admin_songs_unknown_user_404(client: TestClient, fs: MemoryFirestore) -> None:
    res = client.get("/api/admin/voice/users/nobody@example.com/songs", headers=_tok())
    assert res.status_code == 404
    assert "decide_users" in fs.cols and len(fs.cols["decide_users"]) == 1  # lookup only: no profile created


# ---------------------------------------------------------------- audio


def test_admin_audio_streams(client: TestClient, fs: MemoryFirestore) -> None:
    fs.cols["voice_recordings"] = {
        REC_ID: {"user_id": USER_ID, "session_id": "sess1", "gcs_path": REC_PATH, "content_type": "audio/mp4"}
    }
    res = client.get(f"/api/admin/voice/recordings/{REC_ID}/audio", headers=_tok())
    assert res.status_code == 200
    assert res.headers["content-type"] == "audio/mp4"
    assert res.content == AUDIO


def test_admin_audio_accepts_gs_uri(client: TestClient, fs: MemoryFirestore, settings: BackendSettings) -> None:
    fs.cols["voice_recordings"] = {
        REC_ID: {"gcs_path": f"gs://{settings.voice_recordings_bucket}/{REC_PATH}", "content_type": "audio/wav"}
    }
    res = client.get(f"/api/admin/voice/recordings/{REC_ID}/audio", headers=_tok())
    assert res.status_code == 200 and res.headers["content-type"] == "audio/wav" and res.content == AUDIO


def test_admin_audio_404s(client: TestClient, fs: MemoryFirestore) -> None:
    assert client.get("/api/admin/voice/recordings/missing/audio", headers=_tok()).status_code == 404
    fs.cols["voice_recordings"] = {"gone": {"gcs_path": "voice-recordings/x/y/1.m4a", "content_type": "audio/mp4"}}
    assert client.get("/api/admin/voice/recordings/gone/audio", headers=_tok()).status_code == 404


def test_recordings_bucket_default(settings: BackendSettings, tmp_path: Path) -> None:
    assert settings.voice_recordings_bucket == "nomadkaraoke-voice-recordings"
    assert settings.voice_data_bucket == "nomadkaraoke-musicbrainz-data"
    assert settings.voice_refresh_s == 600 and settings.voice_local_dir == ""


@pytest.mark.parametrize(
    "gcs_path",
    [
        "gs://some-other-bucket/voice-recordings/x/y/1.m4a",
        "jobs/abc/output.mp4",
        "voice-recordings/../jobs/abc/output.mp4",
        "voice-recordings/u/../../secret",
        "gs://nomadkaraoke-voice-recordings/other/1.m4a",
        "/voice-recordings/u/s/1.m4a",
    ],
)
def test_admin_audio_refuses_paths_outside_recordings(
    client: TestClient, fs: MemoryFirestore, bucket: FakeBucket, gcs_path: str
) -> None:
    bucket.objects[gcs_path] = b"must not be served"
    bucket.objects[gcs_path.split("/", 3)[-1]] = b"must not be served"
    fs.cols["voice_recordings"] = {"bad": {"gcs_path": gcs_path, "content_type": "audio/mp4"}}
    assert client.get("/api/admin/voice/recordings/bad/audio", headers=_tok()).status_code == 404


def test_admin_audio_unchanged(client: TestClient, fs: MemoryFirestore) -> None:
    """The admin stream shares the user route's helpers (voice_recording_service) and still serves any user's
    recording, gs:// or plain, chunk by chunk."""
    assert recording_object_name(REC_PATH, "b") == REC_PATH
    assert recording_object_name(f"gs://b/{REC_PATH}", "b") == REC_PATH
    assert recording_object_name(f"gs://other/{REC_PATH}", "b") is None
    assert recording_object_name("voice-recordings/u/../x", "b") is None
    fs.cols["voice_recordings"] = {
        REC_ID: {"user_id": "someone_else", "gcs_path": REC_PATH, "content_type": "audio/mp4"}
    }
    res = client.get(f"/api/admin/voice/recordings/{REC_ID}/audio", headers=_tok())
    assert res.status_code == 200 and res.content == AUDIO
