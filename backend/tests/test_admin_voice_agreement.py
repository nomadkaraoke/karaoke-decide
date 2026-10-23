"""GET /api/admin/voice/agreement and the songs endpoint's prediction (fixture voice.db, invented labels)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_auth_service_dep, get_firestore, get_optional_user, get_settings
from backend.api.routes.admin_voice import ATTEMPTS_COLLECTION, LABELS_COLLECTION, get_voice_store, label_doc_id
from backend.config import BackendSettings
from backend.main import app
from backend.services.auth_service import AuthService
from backend.services.voice_data_store import VoiceDataStore
from backend.tests.test_karaoke_log_service import MemoryFirestore
from karaoke_decide.voice_pool import fixtures

TOKEN = "script-token-1"
EMAIL = "andrew@example.com"
USER_ID = "user_andrew"
STARLIGHT = fixtures.STARLIGHT  # baritone centre: fits a baritone, nothing else
SPLIT_FOR_MEN = "bee gees|how deep is your love"  # baritone centre, octave_split_male
HIGH_SPLIT = "blake shelton|gods country"  # soprano centre, octave_split_male, sustained highs
ATTEMPTED = "billy joel|piano man"
MAYBE = "adele|hello"
UNPOOLED = "nobody known|invented tune"
URL = "/api/admin/voice/agreement"

BARITONE_PROFILE = {
    "low_midi": 45,
    "high_midi": 69,
    "comfort_low": 50,
    "comfort_high": 64,
    "voice_type": "baritone",
    "register": None,
    "confidence": "high",
    "method": "session",
    "evidence_count": 5,
    "session_id": "s1",
    "measured_at": "2026-10-22T00:00:00Z",
    "updated_at": "2026-10-22T00:00:00Z",
}


@pytest.fixture(scope="module")
def voice_store(tmp_path_factory: pytest.TempPathFactory) -> Iterator[VoiceDataStore]:
    db = fixtures.build(tmp_path_factory.mktemp("voice"))
    store = VoiceDataStore()
    store.load(BackendSettings(voice_local_dir=str(db.parent)))
    assert store.status == "ready"
    yield store
    store.close()


def _label(key: str, good: str | None, user_id: str = USER_ID) -> tuple[str, dict[str, Any]]:
    return label_doc_id(user_id, key), {"user_id": user_id, "song_key": key, "good": good, "tags": [], "notes": ""}


@pytest.fixture
def fs() -> MemoryFirestore:
    fs = MemoryFirestore()
    fs.cols["decide_users"] = {
        hashlib.sha256(EMAIL.encode()).hexdigest(): {
            "user_id": USER_ID,
            "email": EMAIL,
            "is_guest": False,
            "voice_profile": dict(BARITONE_PROFILE),
        }
    }
    fs.cols[LABELS_COLLECTION] = dict(
        [
            _label(STARLIGHT, "yes"),
            _label(SPLIT_FOR_MEN, "no"),
            _label(HIGH_SPLIT, "no"),
            _label(ATTEMPTED, "yes"),
            _label(MAYBE, "maybe"),
            _label(UNPOOLED, "no"),
            _label("bee gees|stayin alive", "yes", user_id="someone_else"),
        ]
    )
    fs.cols[ATTEMPTS_COLLECTION] = {
        "s1:1": {"user_id": USER_ID, "session_id": "s1", "n": 1, "song_key": ATTEMPTED},
        "o:1": {"user_id": "someone_else", "session_id": "o", "n": 1, "song_key": STARLIGHT},
    }
    return fs


@pytest.fixture
def client(fs: MemoryFirestore, voice_store: VoiceDataStore) -> Iterator[TestClient]:
    settings = BackendSettings(
        environment="development",
        google_cloud_project="test-project",
        jwt_secret="test-jwt-secret-key-for-testing-only",
        admin_tokens=[TOKEN],
        flacfetch_api_url="",
        flacfetch_api_key="",
    )
    auth = AuthService(settings, fs, email_service=MagicMock())  # type: ignore[arg-type]
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_firestore] = lambda: fs
    app.dependency_overrides[get_auth_service_dep] = lambda: auth
    app.dependency_overrides[get_optional_user] = lambda: None
    app.dependency_overrides[get_voice_store] = lambda: voice_store
    yield TestClient(app)
    app.dependency_overrides.clear()


def _tok() -> dict[str, str]:
    return {"X-Admin-Token": TOKEN}


def test_agreement_403_without_admin(client: TestClient) -> None:
    assert client.get(URL, params={"email": EMAIL}).status_code == 403


def test_agreement_report_over_held_out_songs(client: TestClient) -> None:
    res = client.get(URL, params={"email": EMAIL}, headers=_tok())
    assert res.status_code == 200, res.text
    body = res.json()
    rows = {r["song_key"]: r for r in body["rows"]}
    # maybe, attempted, unpooled and other users' labels are not held out
    assert set(rows) == {STARLIGHT, SPLIT_FOR_MEN, HIGH_SPLIT}
    assert rows[STARLIGHT]["pred"] is True and rows[STARLIGHT]["label"] == "yes"
    assert rows[STARLIGHT]["features"]["fit"] == 1.0
    assert rows[SPLIT_FOR_MEN]["features"]["octave_split"] == 1.0
    assert rows[SPLIT_FOR_MEN]["pred"] is True  # fits (+1) - split (0.8) = 0.2 → p 0.55
    assert rows[HIGH_SPLIT]["pred"] is False
    assert (body["n"], body["agree"]) == (3, 2)
    assert body["rate"] == pytest.approx(2 / 3)
    assert body["confusion"] == {"tp": 1, "fp": 1, "tn": 1, "fn": 0}
    assert body["ready_for_signoff"] is False
    assert body["skipped_no_profile"] == 0
    assert body["per_marker"]["octave_split"]["disagree"] == pytest.approx(-0.8)
    assert body["per_marker"]["fit"]["agree"] == pytest.approx((1.0 + 0.6) / 2)
    assert body["weights"]["fit"] == 1.0


def test_agreement_without_profile_skips_every_row(client: TestClient, fs: MemoryFirestore) -> None:
    for doc in fs.cols["decide_users"].values():
        doc["voice_profile"] = None
    body = client.get(URL, params={"email": EMAIL}, headers=_tok()).json()
    assert (body["n"], body["agree"], body["rows"]) == (0, 0, [])
    assert body["skipped_no_profile"] == 3
    assert body["ready_for_signoff"] is False


def test_agreement_unknown_user_404(client: TestClient) -> None:
    res = client.get(URL, params={"email": "nobody@example.com"}, headers=_tok())
    assert res.status_code == 404


def test_agreement_503_without_voice_db(client: TestClient) -> None:
    app.dependency_overrides[get_voice_store] = lambda: VoiceDataStore()
    assert client.get(URL, params={"email": EMAIL}, headers=_tok()).status_code == 503


def test_songs_endpoint_fills_prediction(client: TestClient, fs: MemoryFirestore) -> None:
    songs = {s["song_key"]: s for s in client.get(f"/api/admin/voice/users/{EMAIL}/songs", headers=_tok()).json()}
    star = songs[STARLIGHT]["prediction"]
    assert star["good"] is True and star["p"] == pytest.approx(0.7311, abs=1e-3)  # σ(1)
    assert songs[UNPOOLED]["prediction"] is None  # no analysis row
    assert songs[ATTEMPTED]["prediction"] is not None
    for doc in fs.cols["decide_users"].values():
        doc["voice_profile"] = None
    songs = client.get(f"/api/admin/voice/users/{EMAIL}/songs", headers=_tok()).json()
    assert all(s["prediction"] is None for s in songs)
