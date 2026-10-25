"""Voice session routes: /api/voice/{flags,sessions,...} through the real app.

Fixture voice.db (Task A7), fixture catalog.db + availability.db, in-memory Firestore.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import random
import shutil
import sqlite3
import statistics
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import (
    get_catalog_store_dep,
    get_current_user,
    get_firestore,
    get_hidden_keys_provider,
    get_recommendation_service_v2_dep,
    get_settings,
    get_voice_store,
)
from backend.config import BackendSettings
from backend.main import app
from backend.models.voice_session import SessionResultSummaryOut
from backend.services import voice_session_service as vss
from backend.services.catalog_store import CatalogUnavailable
from backend.services.recs_v2.service import RecSection, RecSong
from backend.services.voice_data_store import VoiceDataStore
from backend.services.voice_profile_service import VoiceProfileOut
from backend.tests.test_karaoke_log_routes import RouteFirestore
from karaoke_decide.catalog import fixtures as catalog_fixtures
from karaoke_decide.catalog.reader import CatalogReader
from karaoke_decide.core.models import User
from karaoke_decide.voice_pool import fixtures
from karaoke_decide.voice_session.model import Hyp, Posterior, SongDemand, full, likely_octave
from karaoke_decide.voice_session.simulate import answer

EMAIL = "singer@example.com"
EMAIL_HASH = hashlib.sha256(EMAIL.encode()).hexdigest()
USER_ID = "user_voice_s"
OTHER_ID = "user_voice_other"
T0 = datetime(2026, 10, 22, 19, 0, tzinfo=UTC)

SETUP: dict[str, Any] = {
    "posture": "standing",
    "warmth": "warm",
    "record": False,
    "headphones": True,
    "device": "Pixel",
}


# ---------------------------------------------------------------- fixtures


class FakeCatalogStore:
    def __init__(self, reader: CatalogReader | None) -> None:
        self._reader = reader
        self.run = "fixture-run"

    def require(self) -> CatalogReader:
        if self._reader is None:
            raise CatalogUnavailable("warming_up")
        return self._reader


class FakeHidden:
    """Hidden keys and "Love it" (the SongFeedbackService slice voice sessions use)."""

    def __init__(self) -> None:
        self.keys: set[str] = set()
        self.loved: list[tuple[str, str, str, str]] = []

    async def hidden_keys(self, user_id: str) -> set[str]:
        return set(self.keys)

    async def love(self, user_id: str, song_key: str, artist: str, title: str) -> None:
        self.loved.append((user_id, song_key, artist, title))


def _store(db: Path) -> VoiceDataStore:
    store = VoiceDataStore()
    store.load(BackendSettings(voice_local_dir=str(db.parent)))
    assert store.status == "ready"
    return store


@pytest.fixture(scope="module")
def voice_store(tmp_path_factory: pytest.TempPathFactory) -> Iterator[VoiceDataStore]:
    store = _store(fixtures.build(tmp_path_factory.mktemp("voice")))
    yield store
    store.close()


@pytest.fixture(scope="module")
def voice_store_alts(tmp_path_factory: pytest.TempPathFactory) -> Iterator[VoiceDataStore]:
    """The fixture voice.db with two extra community versions per song (ALT<n>a, ALT<n>b)."""
    src = fixtures.build(tmp_path_factory.mktemp("voice_src"))
    dest_dir = tmp_path_factory.mktemp("voice_alts")
    shutil.copy(src, dest_dir / "voice.db")
    con = sqlite3.connect(dest_dir / "voice.db")
    rows = con.execute("SELECT song_key, video_id FROM versions").fetchall()
    for key, vid in rows:
        n = vid.removeprefix("FIXTURE")
        con.execute(
            "INSERT INTO versions VALUES (?, ?, 'SING', 'Sing King', 1, 1, 0.5), (?, ?, 'ZOOM', 'Zoom', 2, NULL, 0.5)",
            (key, f"ALT{n}a", key, f"ALT{n}b"),
        )
    con.commit()
    con.close()
    store = _store(dest_dir / "voice.db")
    yield store
    store.close()


@pytest.fixture(scope="module")
def catalog_reader(tmp_path_factory: pytest.TempPathFactory, index_dir: Path) -> Iterator[CatalogReader]:
    d = tmp_path_factory.mktemp("voice_catalog")
    catalog_fixtures.build(d)
    r = CatalogReader(d / "catalog.db", index_dir / "availability.db")
    yield r
    r.close()


@pytest.fixture
def fs() -> RouteFirestore:
    fs = RouteFirestore()
    fs.cols["decide_users"] = {EMAIL_HASH: {"user_id": USER_ID, "email": EMAIL, "is_guest": False}}
    return fs


@pytest.fixture
def hidden() -> FakeHidden:
    return FakeHidden()


MakeClient = Callable[..., TestClient]


@pytest.fixture
def make_client(
    fs: RouteFirestore, hidden: FakeHidden, voice_store: VoiceDataStore, catalog_reader: CatalogReader
) -> Iterator[MakeClient]:
    def make(
        *,
        admin: bool = True,
        open_: bool = False,
        user_id: str = USER_ID,
        store: VoiceDataStore | None = None,
    ) -> TestClient:
        settings = BackendSettings(
            environment="development", google_cloud_project="test-project", voice_session_open=open_
        )
        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[get_firestore] = lambda: fs
        app.dependency_overrides[get_current_user] = lambda: User(id=user_id, email=EMAIL, is_admin=admin)
        app.dependency_overrides[get_voice_store] = lambda: store or voice_store
        app.dependency_overrides[get_catalog_store_dep] = lambda: FakeCatalogStore(catalog_reader)
        app.dependency_overrides[get_hidden_keys_provider] = lambda: hidden
        return TestClient(app)

    yield make
    app.dependency_overrides.clear()


@pytest.fixture
def client(make_client: MakeClient) -> TestClient:
    return make_client()


def _create(client: TestClient, **setup: Any) -> str:
    r = client.post("/api/voice/sessions", json={**SETUP, **setup})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "active"
    return str(r.json()["session_id"])


def _next(client: TestClient, sid: str) -> dict[str, Any]:
    r = client.get(f"/api/voice/sessions/{sid}/next")
    assert r.status_code == 200, r.text
    return dict(r.json())


def _quiz(**over: Any) -> dict[str, Any]:
    return {"feel": "fine", "position": "comfortable", "difficulty": "easy", "knew": "well", **over}


def _attempt(song: dict[str, Any], i: int, quiz: dict[str, Any] | None = None, **over: Any) -> dict[str, Any]:
    start = T0 + timedelta(minutes=5 * i)
    return {
        "song_key": song["song_key"],
        "video_id": song["version"]["video_id"],
        "started_at": start.isoformat(),
        "ended_at": (start + timedelta(minutes=3)).isoformat(),
        "stopped_early": False,
        "quiz": quiz or _quiz(),
        "pitch": None,
        **over,
    }


def _post_attempt(client: TestClient, sid: str, body: dict[str, Any]) -> dict[str, Any]:
    r = client.post(f"/api/voice/sessions/{sid}/attempts", json=body)
    assert r.status_code == 200, r.text
    return dict(r.json())


# ---------------------------------------------------------------- flags + gate


def test_flags_admin_vs_user_vs_open(make_client: MakeClient) -> None:
    flags = make_client(admin=True).get("/api/voice/flags").json()
    assert flags == {"session_enabled": True, "is_admin": True, "voice_data": True, "active_session_id": None}
    flags = make_client(admin=False).get("/api/voice/flags").json()
    assert flags == {"session_enabled": False, "is_admin": False, "voice_data": True, "active_session_id": None}
    flags = make_client(admin=False, open_=True, store=VoiceDataStore()).get("/api/voice/flags").json()
    assert flags == {"session_enabled": True, "is_admin": False, "voice_data": False, "active_session_id": None}


def test_disabled_for_non_admin_403(make_client: MakeClient) -> None:
    c = make_client(admin=False)
    for method, url in (
        ("POST", "/api/voice/sessions"),
        ("PATCH", "/api/voice/sessions/x"),
        ("GET", "/api/voice/sessions/x/next"),
        ("POST", "/api/voice/sessions/x/skips"),
        ("POST", "/api/voice/sessions/x/attempts"),
        ("GET", "/api/voice/sessions/x/result"),
    ):
        r = c.request(method, url, json=SETUP if method != "GET" else None)
        assert r.status_code == 403, (method, url)
        assert r.json() == {"detail": "voice_session_disabled"}
    assert make_client(admin=False, open_=True).post("/api/voice/sessions", json=SETUP).status_code == 200


# ---------------------------------------------------------------- sessions


def test_create_abandons_previous(client: TestClient, fs: RouteFirestore) -> None:
    first = _create(client)
    second = _create(client, warmth="cold")
    sessions = fs.cols["voice_sessions"]
    assert sessions[first]["state"] == "abandoned"
    assert sessions[second]["state"] == "active"
    assert sessions[second]["user_id"] == USER_ID
    assert sessions[second]["setup"]["warmth"] == "cold"
    assert sessions[second]["tired_count"] == 0 and sessions[second]["skips"] == []
    # an abandoned session takes no more songs
    assert client.get(f"/api/voice/sessions/{first}/next").status_code == 409


def test_patch_updates_setup(client: TestClient, fs: RouteFirestore) -> None:
    sid = _create(client, warmth="cold")
    assert client.patch(f"/api/voice/sessions/{sid}", json={"warmth": "warm"}).status_code == 204
    setup = fs.cols["voice_sessions"][sid]["setup"]
    assert setup["warmth"] == "warm" and setup["posture"] == "standing"
    assert client.patch(f"/api/voice/sessions/{sid}", json={"warmth": "hot"}).status_code == 422


def test_rejects_extra_fields_in_setup(client: TestClient) -> None:
    assert client.post("/api/voice/sessions", json={**SETUP, "audio": "x"}).status_code == 422
    assert client.post("/api/voice/sessions", json={**SETUP, "device": "d" * 81}).status_code == 422


def test_next_first_is_comfortable_start(client: TestClient) -> None:
    sid = _create(client)
    song = _next(client, sid)
    assert song["kind"] == "song"
    assert song["reason_type"] == "comfortable_start"
    assert song["version"]["video_id"].startswith("FIXTURE")
    assert song["version"]["embeddable"] is True
    assert song["alternatives"] == []
    rng = song["song_range"]
    assert rng["low"] < rng["p10"] < rng["p50"] < rng["p90"] < rng["high"]
    assert song["artist"] and song["title"]
    assert _next(client, sid)["song_key"] == song["song_key"]  # stable until something happens


def test_next_skips_hidden_songs(client: TestClient, hidden: FakeHidden) -> None:
    sid = _create(client)
    first = _next(client, sid)["song_key"]
    hidden.keys.add(first)
    assert _next(client, sid)["song_key"] != first


def test_skip_dont_know_excludes_song(client: TestClient, fs: RouteFirestore) -> None:
    sid = _create(client)
    first = _next(client, sid)
    r = client.post(f"/api/voice/sessions/{sid}/skips", json={"song_key": first["song_key"], "reason": "dont_know"})
    assert r.status_code == 204
    second = _next(client, sid)
    assert second["song_key"] != first["song_key"]
    skips = fs.cols["voice_sessions"][sid]["skips"]
    assert [(s["song_key"], s["reason"]) for s in skips] == [(first["song_key"], "dont_know")]
    r = client.post(
        f"/api/voice/sessions/{sid}/skips", json={"song_key": second["song_key"], "reason": "something_else"}
    )
    assert r.status_code == 204
    assert _next(client, sid)["song_key"] not in {first["song_key"], second["song_key"]}


def test_skip_unknown_song_422(client: TestClient) -> None:
    sid = _create(client)
    r = client.post(f"/api/voice/sessions/{sid}/skips", json={"song_key": "nobody|nothing", "reason": "dont_know"})
    assert r.status_code == 422


def test_not_embeddable_skip_marks_version_and_offers_alternative(
    make_client: MakeClient, fs: RouteFirestore, voice_store_alts: VoiceDataStore
) -> None:
    """Review Focus 1: the session never dead-ends on an unplayable version."""
    client = make_client(store=voice_store_alts)
    sid = _create(client)
    song = _next(client, sid)
    key, n = song["song_key"], song["version"]["video_id"].removeprefix("FIXTURE")
    assert song["version"]["video_id"] == f"FIXTURE{n}"
    assert [a["video_id"] for a in song["alternatives"]] == [f"ALT{n}a", f"ALT{n}b"]

    def skip(video_id: str) -> None:
        body = {"song_key": key, "reason": "not_embeddable", "video_id": video_id}
        assert client.post(f"/api/voice/sessions/{sid}/skips", json=body).status_code == 204

    skip(f"FIXTURE{n}")
    assert fs.cols["voice_version_stats"][f"FIXTURE{n}"]["embeddable"] is False
    again = _next(client, sid)
    assert again["song_key"] == key and again["reason_type"] == song["reason_type"]
    assert again["version"] == {
        "video_id": f"ALT{n}a",
        "brand_code": "SING",
        "brand_name": "Sing King",
        "embeddable": True,
    }
    assert [a["video_id"] for a in again["alternatives"]] == [f"ALT{n}b"]

    skip(f"ALT{n}a")
    third = _next(client, sid)
    assert third["song_key"] == key and third["version"]["video_id"] == f"ALT{n}b" and third["alternatives"] == []

    # No embeddable version left: offered once more for link-out, flagged so the app skips the player.
    skip(f"ALT{n}b")
    link_out = _next(client, sid)
    assert link_out["song_key"] == key
    assert link_out["version"]["embeddable"] is False and link_out["alternatives"] == []

    skip(link_out["version"]["video_id"])
    assert _next(client, sid)["song_key"] != key


def test_not_embeddable_from_other_users_overrides_voice_db(
    make_client: MakeClient, fs: RouteFirestore, voice_store_alts: VoiceDataStore
) -> None:
    client = make_client(store=voice_store_alts)
    sid = _create(client)
    song = _next(client, sid)
    n = song["version"]["video_id"].removeprefix("FIXTURE")
    fs.cols["voice_version_stats"] = {f"FIXTURE{n}": {"embeddable": False}}
    again = _next(client, sid)
    assert again["song_key"] == song["song_key"]
    assert again["version"]["video_id"] == f"ALT{n}a"


def test_not_embeddable_needs_a_version_of_the_song(client: TestClient) -> None:
    sid = _create(client)
    song = _next(client, sid)
    for video_id in (None, "SOMETHINGELSE"):
        body = {"song_key": song["song_key"], "reason": "not_embeddable", "video_id": video_id}
        assert client.post(f"/api/voice/sessions/{sid}/skips", json=body).status_code == 422


def test_single_version_not_embeddable_goes_to_link_out_then_moves_on(client: TestClient) -> None:
    sid = _create(client)
    song = _next(client, sid)
    body = {"song_key": song["song_key"], "reason": "not_embeddable", "video_id": song["version"]["video_id"]}
    assert client.post(f"/api/voice/sessions/{sid}/skips", json=body).status_code == 204
    link_out = _next(client, sid)
    assert link_out["song_key"] == song["song_key"] and link_out["version"]["embeddable"] is False
    assert client.post(f"/api/voice/sessions/{sid}/skips", json=body).status_code == 204
    assert _next(client, sid)["song_key"] != song["song_key"]


# ---------------------------------------------------------------- attempts


def test_attempt_updates_and_is_idempotent(client: TestClient, fs: RouteFirestore) -> None:
    sid = _create(client)
    song = _next(client, sid)
    body = _attempt(song, 0, pitch={"voiced_s": 60, "held_count": 4, "held_low": 48, "held_high": 62,
                                     "p10": 50, "p50": 55, "p90": 60})  # fmt: skip
    out = _post_attempt(client, sid, body)
    assert out["n"] == 1 and out["attempt_id"] == f"{sid}:1"
    assert out["done"] is False and out["done_reason"] is None
    assert 0 < out["top_p"] <= 1
    doc = fs.cols["voice_attempts"][f"{sid}:1"]
    assert doc["user_id"] == USER_ID and doc["session_id"] == sid and doc["song_key"] == song["song_key"]
    assert doc["video_id"] == song["version"]["video_id"] and doc["recording_id"] is None
    assert doc["context"] == {"posture": "standing", "warmth": "warm", "headphones": True, "record": False,
                              "device": "Pixel"}  # fmt: skip
    assert doc["quiz"]["position"] == "comfortable" and doc["pitch"]["held_high"] == 62

    assert _post_attempt(client, sid, body) == out  # retry: same attempt
    assert len(fs.cols["voice_attempts"]) == 1
    assert _next(client, sid)["song_key"] != song["song_key"]  # attempted songs are done


def test_attempt_unknown_song_422(client: TestClient) -> None:
    sid = _create(client)
    song = _next(client, sid)
    body = _attempt({**song, "song_key": "nobody|nothing"}, 0)
    assert client.post(f"/api/voice/sessions/{sid}/attempts", json=body).status_code == 422


def test_rejects_extra_fields_in_pitch(client: TestClient, fs: RouteFirestore) -> None:
    sid = _create(client)
    song = _next(client, sid)
    pitch = {"voiced_s": 60, "held_count": 4, "held_low": 48, "held_high": 62, "p10": 50, "p50": 55, "p90": 60}
    for bad in ({**pitch, "samples": [0.1, 0.2]}, {**pitch, "p50": 120}):
        r = client.post(f"/api/voice/sessions/{sid}/attempts", json=_attempt(song, 0, pitch=bad))
        assert r.status_code == 422, bad
    r = client.post(f"/api/voice/sessions/{sid}/attempts", json={**_attempt(song, 0), "audio": "AAAA"})
    assert r.status_code == 422
    r = client.post(f"/api/voice/sessions/{sid}/attempts", json=_attempt(song, 0, quiz=_quiz(extra=1)))
    assert r.status_code == 422
    assert "voice_attempts" not in fs.cols


def test_quiz_requires_feel_and_position(client: TestClient) -> None:
    sid = _create(client)
    song = _next(client, sid)
    for missing in ("feel", "position"):
        quiz = {k: v for k, v in _quiz().items() if k != missing}
        r = client.post(f"/api/voice/sessions/{sid}/attempts", json=_attempt(song, 0, quiz=quiz))
        assert r.status_code == 422, missing
    minimal = {"feel": "loved", "position": "too_high"}
    assert client.post(f"/api/voice/sessions/{sid}/attempts", json=_attempt(song, 0, quiz=minimal)).status_code == 200


def _demand(song: dict[str, Any]) -> SongDemand:
    r = song["song_range"]
    return SongDemand(low=r["low"], high=r["high"], p10=r["p10"], p50=r["p50"], p90=r["p90"])


def _sung_pitch(h: Hyp, song: SongDemand) -> dict[str, Any]:
    """The on-device pitch summary of singer h on this song: sung in its likely octave, held notes in its range."""
    o = likely_octave(h, song)
    lo, hi = full(h)
    return {
        "voiced_s": 60,
        "held_count": 4,
        "held_low": max(song.p10 + o, lo),
        "held_high": min(song.p90 + o, hi),
        "p10": song.p10 + o,
        "p50": song.p50 + o,
        "p90": song.p90 + o,
    }


def test_three_consistent_baritone_attempts_then_more_until_done(client: TestClient) -> None:
    """A simulated baritone ends 'confident' within 10 songs on the A7 fixture pool, from a uniform prior.

    Quiz answers come from C2's simulated singer (``simulate.answer``, default noise 0.15); each attempt
    also carries the pitch summary the app sends. Quiz-only answers can't get there on this pool (6 narrow
    demand levels at the type centres; see the C3 report): only ~14% of seeds end confident within 10.
    """
    sid = _create(client)
    rng = random.Random(20261022)
    bari: Hyp = ("baritone", 0)
    outs: list[dict[str, Any]] = []
    for i in range(10):
        song = _next(client, sid)
        if song["kind"] == "done":
            break
        position, difficulty = answer(rng, bari, _demand(song), noise=0.15)
        quiz = _quiz(position=position, difficulty=difficulty)
        out = _post_attempt(client, sid, _attempt(song, i, quiz, pitch=_sung_pitch(bari, _demand(song))))
        outs.append(out)
        if out["done"]:
            break
    assert len(outs) >= 3, outs  # never confident before 3 attempts with range evidence
    assert all(not o["done"] for o in outs[:2])
    assert outs[-1]["done"] is True and outs[-1]["done_reason"] == "confident", outs
    assert len(outs) <= 10
    assert _next(client, sid) == {"kind": "done", "reason": "confident"}
    result = client.get(f"/api/voice/sessions/{sid}/result").json()
    assert "baritone" in {result["voice_type"], result["between"]}
    assert result["partial"] is False and result["confidence_label"] in ("medium", "high")


def test_tired_twice_done(client: TestClient, fs: RouteFirestore) -> None:
    sid = _create(client)
    for i in range(2):
        song = _next(client, sid)
        assert song["kind"] == "song"
        out = _post_attempt(client, sid, _attempt(song, i, _quiz(voice_now="tired")))
    assert out["done"] is True and out["done_reason"] == "tired"
    assert fs.cols["voice_sessions"][sid]["tired_count"] == 2
    assert _next(client, sid) == {"kind": "done", "reason": "tired"}


def test_result_with_zero_attempts_is_partial(client: TestClient, fs: RouteFirestore) -> None:
    """Review Focus 4: Finish before any song still gives a (low-confidence) result."""
    sid = _create(client)
    r = client.get(f"/api/voice/sessions/{sid}/result")
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["partial"] is True and res["confidence_label"] == "low"
    assert res["attempts"] == 0 and res["profile_saved"] is False
    assert res["voice_type"] in ("bass", "baritone", "tenor", "alto", "mezzo", "soprano")
    assert res["low"] <= res["comfort_low"] <= res["comfort_high"] <= res["high"]
    assert res["suited"] == [] and res["loved"] == []
    assert res["improve_hint"] in ("sing_higher_warm", "sing_lower", "more_songs")
    assert fs.cols["voice_sessions"][sid]["state"] == "done"
    assert client.get(f"/api/voice/sessions/{sid}/next").status_code == 409


def test_result_type_from_profile_prior_and_loved(client: TestClient, fs: RouteFirestore) -> None:
    # A mic profile's prior is centred on its comfort band (E3): a soprano band (centre 71.5, read 1 low).
    fs.cols["decide_users"][EMAIL_HASH]["voice_profile"] = {
        "low_midi": 59, "high_midi": 84, "comfort_low": 64, "comfort_high": 79, "voice_type": "soprano",
        "voice_type_between": None, "register": None, "confidence": "high", "method": "mic",
        "measured_at": "2026-10-20T10:00:00Z", "updated_at": "2026-10-20T10:00:00Z",
    }  # fmt: skip
    sid = _create(client)
    song = _next(client, sid)
    _post_attempt(client, sid, _attempt(song, 0, _quiz(feel="loved")))
    res = client.get(f"/api/voice/sessions/{sid}/result").json()
    assert res["voice_type"] == "soprano"
    assert res["loved"] == [{"song_key": song["song_key"], "artist": song["artist"], "title": song["title"]}]
    assert len(res["suited"]) <= 15


def test_result_reports_measured_comfort_source(client: TestClient) -> None:
    """With two or more songs rated comfortable and easy, sung with usable pitch, the result's comfort is what was
    sung (voiced-seconds-weighted p10/p90) and says so; quiz-only sessions keep the model's estimate."""
    sung = {"voiced_s": 60, "held_count": 20, "held_low": 49, "held_high": 59, "p10": 50, "p50": 54, "p90": 58}
    sid = _create(client)
    for i in range(3):
        _post_attempt(client, sid, _attempt(_next(client, sid), i, pitch=sung))
    res = client.get(f"/api/voice/sessions/{sid}/result").json()
    assert res["comfort_source"] == "measured"
    assert (res["comfort_low"], res["comfort_high"]) == (49, 59)  # sung 50-58, widened to 11 keys

    quiz_only = _create(client)
    for i in range(3):
        _post_attempt(client, quiz_only, _attempt(_next(client, quiz_only), i))
    assert client.get(f"/api/voice/sessions/{quiz_only}/result").json()["comfort_source"] == "model"


def test_comfort_source_in_result_summaries_and_after_exclude(client: TestClient, fs: RouteFirestore) -> None:
    """The stored result summary (Me's latest_session, the history) carries comfort_source; excluding one of the
    two comfortable songs leaves too few to measure, so the recomputed comfort is the model's again."""
    sung = {"voiced_s": 60, "held_count": 20, "held_low": 49, "held_high": 59, "p10": 50, "p50": 54, "p90": 58}
    sid = _create(client)
    ids = [_post_attempt(client, sid, _attempt(_next(client, sid), i, pitch=sung))["attempt_id"] for i in range(2)]
    assert client.get(f"/api/voice/sessions/{sid}/result").json()["comfort_source"] == "measured"
    latest = client.get("/api/my/voice").json()["latest_session"]
    assert (latest["session_id"], latest["comfort_source"]) == (sid, "measured")
    assert (latest["comfort_low"], latest["comfort_high"]) == (49, 59)

    patched = _patch_attempt(client, ids[0], True)
    assert patched["result"]["comfort_source"] == "model"
    assert client.get("/api/my/voice").json()["latest_session"]["comfort_source"] == "model"
    history = client.get("/api/voice/history").json()["sessions"]
    assert history[0]["result"]["comfort_source"] == "model"

    assert _patch_attempt(client, ids[0], False)["result"]["comfort_source"] == "measured"
    assert fs.cols["voice_sessions"][sid]["result_summary"]["comfort_source"] == "measured"


def test_stored_summary_without_comfort_source_reads_as_model() -> None:
    """Summaries stored before v0.32.0 have no comfort_source: they read as the model's estimate."""
    stored = {
        "session_id": "s", "voice_type": "baritone", "between": None, "confidence_label": "low", "top_p": 0.5,
        "low": 45, "high": 69, "comfort_low": 50, "comfort_high": 64, "attempts": 3, "evidence_attempts": 3,
        "profile_saved": False, "computed_at": "2026-10-25T19:00:00Z",
    }  # fmt: skip
    assert SessionResultSummaryOut.model_validate(stored).comfort_source == "model"


def test_from_profile_reads_voice_profile_out_register() -> None:
    """3c's VoiceProfileOut keeps the register on ``register_`` with values lower/middle/higher."""
    base = {
        "low_midi": 40, "high_midi": 69, "comfort_low": 45, "comfort_high": 64, "voice_type": None,
        "voice_type_between": None, "register": None, "confidence": "low", "method": "self",
        "measured_at": "2026-10-20T10:00:00Z", "updated_at": "2026-10-20T10:00:00Z",
    }  # fmt: skip
    lower = Posterior.from_profile(VoiceProfileOut.model_validate({**base, "register": "lower"})).type_marginal()
    higher = Posterior.from_profile(VoiceProfileOut.model_validate({**base, "register": "higher"})).type_marginal()
    assert lower["bass"] > lower["soprano"] and higher["soprano"] > higher["bass"]
    # A mic profile is read through its comfort band (centre 59, +1 mic bias = tenor's centre 60), not its name.
    mic = VoiceProfileOut.model_validate(
        {**base, "voice_type": "tenor", "method": "mic", "confidence": "high", "comfort_low": 54, "comfort_high": 64}
    )
    marg = Posterior.from_profile(mic).type_marginal()
    assert max(marg, key=lambda t: marg[t]) == "tenor"


def test_other_users_session_404(make_client: MakeClient) -> None:
    sid = _create(make_client())
    other = make_client(user_id=OTHER_ID)
    for method, url, body in (
        ("PATCH", f"/api/voice/sessions/{sid}", {"warmth": "cold"}),
        ("GET", f"/api/voice/sessions/{sid}/next", None),
        ("POST", f"/api/voice/sessions/{sid}/skips", {"song_key": fixtures.STARLIGHT, "reason": "dont_know"}),
        ("GET", f"/api/voice/sessions/{sid}/result", None),
    ):
        assert other.request(method, url, json=body).status_code == 404, url
    assert other.get("/api/voice/sessions/nope/next").status_code == 404


def test_next_503_without_voice_db(make_client: MakeClient) -> None:
    client = make_client(store=VoiceDataStore())
    sid = _create(client)
    r = client.get(f"/api/voice/sessions/{sid}/next")
    assert r.status_code == 503 and r.json() == {"detail": "warming_up"}


def test_next_works_without_catalog(make_client: MakeClient) -> None:
    client = make_client()
    app.dependency_overrides[get_catalog_store_dep] = lambda: FakeCatalogStore(None)
    sid = _create(client)
    assert _next(client, sid)["kind"] == "song"


@pytest.mark.slow
def test_next_p50_under_300ms(client: TestClient) -> None:
    sid = _create(client)
    for i in range(4):  # a few attempts so the posterior rebuild does real work
        song = _next(client, sid)
        _post_attempt(client, sid, _attempt(song, i, _quiz(position="too_high" if i % 2 else "comfortable")))
    times = []
    for _ in range(20):
        t = time.perf_counter()
        assert _next(client, sid)["kind"] in ("song", "done")
        times.append(time.perf_counter() - t)
    p50 = statistics.median(times)
    print(f"voice session next p50 = {p50 * 1000:.1f} ms")
    assert p50 < 0.3


# ---------------------------------------------------------------- fix round 1


class RacingFirestore(RouteFirestore):
    """The first attempt create loses a race to an identical request that lands just before it."""

    raced = False

    async def create_document(self, collection: str, doc_id: str, data: dict[str, Any]) -> bool:
        if collection == "voice_attempts" and not self.raced:
            self.raced = True
            await super().create_document(collection, doc_id, data)  # the concurrent retry wins n
            return False
        return await super().create_document(collection, doc_id, data)


def test_overlapping_attempt_retries_store_one_attempt(make_client: MakeClient) -> None:
    racing = RacingFirestore()
    racing.cols["decide_users"] = {EMAIL_HASH: {"user_id": USER_ID, "email": EMAIL, "is_guest": False}}
    client = make_client()
    app.dependency_overrides[get_firestore] = lambda: racing
    sid = _create(client)
    song = _next(client, sid)
    out = _post_attempt(client, sid, _attempt(song, 0))
    assert racing.raced
    assert out["n"] == 1 and out["attempt_id"] == f"{sid}:1"
    assert list(racing.cols["voice_attempts"]) == [f"{sid}:1"]
    assert _post_attempt(client, sid, _attempt(song, 0)) == out
    assert len(racing.cols["voice_attempts"]) == 1


def test_retried_skip_is_recorded_once(client: TestClient, fs: RouteFirestore) -> None:
    sid = _create(client)
    song = _next(client, sid)
    body = {"song_key": song["song_key"], "reason": "dont_know"}
    for _ in range(2):
        assert client.post(f"/api/voice/sessions/{sid}/skips", json=body).status_code == 204
    skips = fs.cols["voice_sessions"][sid]["skips"]
    assert [(s["song_key"], s["reason"]) for s in skips] == [(song["song_key"], "dont_know")]
    ne = {"song_key": song["song_key"], "reason": "not_embeddable", "video_id": song["version"]["video_id"]}
    for _ in range(2):  # retried not_embeddable (no next in between): one skip
        assert client.post(f"/api/voice/sessions/{sid}/skips", json=ne).status_code == 204
    assert len(fs.cols["voice_sessions"][sid]["skips"]) == 2


def test_pitch_and_times_must_be_ordered(client: TestClient, fs: RouteFirestore) -> None:
    sid = _create(client)
    song = _next(client, sid)
    pitch = {"voiced_s": 60, "held_count": 4, "held_low": 48, "held_high": 62, "p10": 50, "p50": 55, "p90": 60}
    for bad in ({"held_low": 63}, {"p10": 56}, {"p90": 54}, {"p50": 61}):
        r = client.post(f"/api/voice/sessions/{sid}/attempts", json=_attempt(song, 0, pitch={**pitch, **bad}))
        assert r.status_code == 422, bad
    body = _attempt(song, 0, pitch=pitch)
    body["ended_at"] = (T0 - timedelta(seconds=1)).isoformat()
    assert client.post(f"/api/voice/sessions/{sid}/attempts", json=body).status_code == 422
    assert "voice_attempts" not in fs.cols
    body["ended_at"] = body["started_at"]  # equal is fine
    assert client.post(f"/api/voice/sessions/{sid}/attempts", json=body).status_code == 200


def test_every_song_skipped_is_no_candidates_and_partial(client: TestClient) -> None:
    """Review Focus 4: skipping the whole 48-song fixture pool ends the session, and the result is partial."""
    sid = _create(client)
    seen: set[str] = set()
    while (song := _next(client, sid))["kind"] == "song":
        assert song["song_key"] not in seen
        seen.add(song["song_key"])
        body = {"song_key": song["song_key"], "reason": "dont_know"}
        assert client.post(f"/api/voice/sessions/{sid}/skips", json=body).status_code == 204
    assert len(seen) == 48
    assert song == {"kind": "done", "reason": "no_candidates"}
    res = client.get(f"/api/voice/sessions/{sid}/result").json()
    assert res["partial"] is True and res["confidence_label"] == "low" and res["attempts"] == 0


# ---------------------------------------------------------------- final review fixes


def test_result_of_abandoned_session_never_saves_over_newer_profile(
    client: TestClient, fs: RouteFirestore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S1 active -> S2 created (S1 abandoned) -> S2 finished (saves P2) -> result(S1): P2 stands, S1 stays abandoned."""
    monkeypatch.setattr(vss, "should_save", lambda *_: True)  # without the state gate S1 would overwrite P2
    s1 = _create(client)
    song = _next(client, s1)
    _post_attempt(client, s1, _attempt(song, 0, _quiz(position="too_high")))
    s2 = _create(client)
    assert fs.cols["voice_sessions"][s1]["state"] == "abandoned"
    song = _next(client, s2)
    _post_attempt(client, s2, _attempt(song, 1))
    r2 = client.get(f"/api/voice/sessions/{s2}/result").json()
    assert r2["profile_saved"] is True
    p2 = dict(fs.cols["decide_users"][EMAIL_HASH]["voice_profile"])
    assert p2["session_id"] == s2

    r1 = client.get(f"/api/voice/sessions/{s1}/result")
    assert r1.status_code == 200, r1.text
    assert r1.json()["profile_saved"] is False and r1.json()["attempts"] == 1
    assert fs.cols["decide_users"][EMAIL_HASH]["voice_profile"] == p2
    assert fs.cols["voice_sessions"][s1]["state"] == "abandoned"
    assert "profile_saved" not in fs.cols["voice_sessions"][s1]
    # a done session keeps returning its stored outcome
    assert client.get(f"/api/voice/sessions/{s2}/result").json()["profile_saved"] is True


def _without(song_key: str, *, versions_only: bool = False) -> Callable[[Any], dict[str, Any]]:
    """pool_index as a hot-swapped voice.db would give it: the song (or just its versions) gone."""
    real = vss.pool_index

    def index(reader: Any) -> dict[str, Any]:
        out = dict(real(reader))
        if versions_only:
            out[song_key] = dataclasses.replace(out[song_key], versions=())
        else:
            out.pop(song_key)
        return out

    return index


@pytest.mark.parametrize("versions_only", [False, True])
def test_attempt_on_offered_song_survives_voice_db_swap(
    client: TestClient, fs: RouteFirestore, monkeypatch: pytest.MonkeyPatch, versions_only: bool
) -> None:
    sid = _create(client)
    song = _next(client, sid)
    expected = dict(fs.cols["voice_sessions"][sid]["last_offer"]["song"])
    monkeypatch.setattr(vss, "pool_index", _without(song["song_key"], versions_only=versions_only))
    out = _post_attempt(client, sid, _attempt(song, 0))
    assert out["n"] == 1
    stored = fs.cols["voice_attempts"][f"{sid}:1"]
    assert stored["song_key"] == song["song_key"] and stored["song"] == expected
    assert stored["song"]["artist"] == song["artist"]
    # a song or version that was not the offer is still refused
    other = {**song, "version": {**song["version"], "video_id": "NOT_OFFERED"}}
    r = client.post(f"/api/voice/sessions/{sid}/attempts", json=_attempt(other, 1))
    assert r.status_code == 422 and r.json() == {"detail": "unknown_song" if not versions_only else "unknown_version"}


def test_skips_of_offered_song_survive_voice_db_swap(
    client: TestClient, fs: RouteFirestore, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _create(client)
    song = _next(client, sid)
    monkeypatch.setattr(vss, "pool_index", _without(song["song_key"]))
    ne = {"song_key": song["song_key"], "reason": "not_embeddable", "video_id": song["version"]["video_id"]}
    assert client.post(f"/api/voice/sessions/{sid}/skips", json=ne).status_code == 204
    skip = fs.cols["voice_sessions"][sid]["skips"][-1]
    assert (
        skip["done"] is True
        and skip["artist_key"] == fs.cols["voice_sessions"][sid]["last_offer"]["song"]["artist_key"]
    )
    assert fs.cols["voice_version_stats"][song["version"]["video_id"]]["embeddable"] is False
    dk = {"song_key": song["song_key"], "reason": "dont_know"}
    assert client.post(f"/api/voice/sessions/{sid}/skips", json=dk).status_code == 204
    bad = {"song_key": "nobody|nothing", "reason": "dont_know"}
    assert client.post(f"/api/voice/sessions/{sid}/skips", json=bad).status_code == 422


def test_result_survives_failing_outputs(
    client: TestClient, fs: RouteFirestore, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    sid = _create(client)
    song = _next(client, sid)
    _post_attempt(client, sid, _attempt(song, 0))
    fs.cols["voice_attempts"][f"{sid}:1"].pop("outputs_applied")  # missed: result sweeps it

    async def boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("firestore down")

    monkeypatch.setattr(vss.VoiceSessionService, "_apply_outputs", boom)
    with caplog.at_level("WARNING"):
        r = client.get(f"/api/voice/sessions/{sid}/result")
    assert r.status_code == 200, r.text
    assert r.json()["attempts"] == 1
    assert any(rec.levelname == "WARNING" and "outputs" in rec.getMessage() for rec in caplog.records)
    assert fs.cols["voice_sessions"][sid]["state"] == "done"


def test_public_put_rejects_session_method(client: TestClient, fs: RouteFirestore) -> None:
    body = {
        "low_midi": 40, "high_midi": 69, "comfort_low": 45, "comfort_high": 64, "voice_type": "baritone",
        "register": None, "confidence": "medium", "method": "session", "evidence_count": 3,
        "session_id": "abc", "measured_at": "2026-10-22T10:00:00Z",
    }  # fmt: skip
    assert client.put("/api/my/voice", json=body).status_code == 422
    assert "voice_profile" not in fs.cols["decide_users"][EMAIL_HASH]
    assert client.put("/api/my/voice", json={**body, "method": "mic"}).status_code == 200


def test_flags_report_active_session(client: TestClient) -> None:
    assert client.get("/api/voice/flags").json()["active_session_id"] is None
    first = _create(client)
    assert client.get("/api/voice/flags").json()["active_session_id"] == first
    second = _create(client)
    assert client.get("/api/voice/flags").json()["active_session_id"] == second
    client.get(f"/api/voice/sessions/{second}/result")
    assert client.get("/api/voice/flags").json()["active_session_id"] is None


def test_get_session_for_resume_is_read_only(make_client: MakeClient, fs: RouteFirestore) -> None:
    client = make_client()
    sid = _create(client)
    got = client.get(f"/api/voice/sessions/{sid}")
    assert got.status_code == 200, got.text
    assert got.json() == {
        "session_id": sid, "state": "active", "setup": SETUP, "attempts": 0, "top_p": got.json()["top_p"],
        "done_reason": None, "last_offer": None, "profile_saved": False,
    }  # fmt: skip

    song = _next(client, sid)
    assert song["attempt_n"] == 1 and song["max_attempts"] == 12
    before = copy.deepcopy(fs.cols)
    resumed = client.get(f"/api/voice/sessions/{sid}").json()
    assert resumed["last_offer"] == song
    assert fs.cols == before  # reading changes nothing

    _post_attempt(client, sid, _attempt(song, 0))
    resumed = client.get(f"/api/voice/sessions/{sid}").json()
    assert resumed["attempts"] == 1 and resumed["last_offer"] is None
    song2 = _next(client, sid)
    assert song2["attempt_n"] == 2
    assert client.get(f"/api/voice/sessions/{sid}").json()["last_offer"]["attempt_n"] == 2

    ne = {"song_key": song2["song_key"], "reason": "not_embeddable", "video_id": song2["version"]["video_id"]}
    assert client.post(f"/api/voice/sessions/{sid}/skips", json=ne).status_code == 204
    assert client.get(f"/api/voice/sessions/{sid}").json()["last_offer"] is None  # ask /next for the next version

    assert make_client(user_id=OTHER_ID).get(f"/api/voice/sessions/{sid}").status_code == 404
    client = make_client()
    assert client.get("/api/voice/sessions/nope").status_code == 404
    res = client.get(f"/api/voice/sessions/{sid}/result").json()
    done = client.get(f"/api/voice/sessions/{sid}").json()
    assert done["state"] == "done" and done["last_offer"] is None
    assert done["profile_saved"] is res["profile_saved"] and done["done_reason"] == res["done_reason"]


def test_result_carries_done_reason(client: TestClient) -> None:
    sid = _create(client)
    for i in range(2):
        song = _next(client, sid)
        _post_attempt(client, sid, _attempt(song, i, _quiz(voice_now="tired")))
    assert client.get(f"/api/voice/sessions/{sid}/result").json()["done_reason"] == "tired"
    sid = _create(client)
    assert client.get(f"/api/voice/sessions/{sid}/result").json()["done_reason"] is None


# ---------------------------------------------------------------- history (Phase 3d batch 1, Task A1)


def _seed_session(
    fs: RouteFirestore,
    sid: str,
    *,
    user_id: str = USER_ID,
    created_at: datetime = T0,
    attempts: int = 2,
    recorded: tuple[int, ...] = (),
    state: str = "done",
    record: bool = True,
) -> None:
    """A session in the shape production stores (voice_sessions + voice_attempts + voice_recordings)."""
    fs.cols.setdefault("voice_sessions", {})[sid] = {
        "user_id": user_id,
        "state": state,
        "setup": {"record": record, "posture": "standing", "warmth": "warm", "device": "Pixel", "headphones": True},
        "skips": [],
        "prior_profile": None,
        "profile_saved": False,
        "last_offer": None,
        "created_at": created_at,
        "updated_at": created_at + timedelta(minutes=30),
    }
    for n in range(1, attempts + 1):
        start = created_at + timedelta(minutes=5 * n)
        fs.cols.setdefault("voice_attempts", {})[f"{sid}:{n}"] = {
            "user_id": user_id,
            "session_id": sid,
            "n": n,
            "song_key": f"artist {n}|song {n}",
            "video_id": f"vid{n}",
            "context": {"posture": "standing", "warmth": "warm", "headphones": True, "device": "Pixel", "record": True},
            "quiz": {
                "would_sing": "yes",
                "tags": [],
                "voice_now": "fresh",
                "knew": "well",
                "version_issues": [],
                "difficulty": "easy",
                "version_quality": "good",
                "position": "comfortable",
                "feel": "fine",
                "would_work_if": [],
            },  # fmt: skip
            "pitch": {
                "held_high": 62.0,
                "voiced_s": 40.0,
                "p10": 50.0,
                "p50": 55.0,
                "p90": 60.0,
                "held_count": 6,
                "held_low": 49.0,
            },  # fmt: skip
            "recording_id": f"{sid}:{n}" if n in recorded else None,
            "song": {
                "artist": f"Artist {n}",
                "title": f"Song {n}",
                "artist_key": f"artist {n}",
                "stratum": "male_mid",
                "demand": {"low": 48.0, "high": 64.0, "p10": 50.0, "p50": 55.0, "p90": 60.0},
            },  # fmt: skip
            "stopped_early": False,
            "started_at": start,
            "ended_at": start + timedelta(minutes=3),
            "outputs_applied": True,
        }
        if n in recorded:
            fs.cols.setdefault("voice_recordings", {})[f"{sid}:{n}"] = {
                "user_id": user_id,
                "session_id": sid,
                "attempt_n": n,
                "gcs_path": f"voice-recordings/{user_id}/{sid}/{n}.m4a",
                "content_type": "audio/mp4",
                "bytes": 48_000 + n,
                "duration_s": 180.5,
                "created_at": start + timedelta(minutes=3),
            }


def test_history_lists_sessions_newest_first_with_attempts_and_recordings(
    client: TestClient, fs: RouteFirestore
) -> None:
    _seed_session(fs, "old", created_at=T0, attempts=1)
    _seed_session(fs, "new", created_at=T0 + timedelta(days=1), attempts=2, recorded=(2,))
    r = client.get("/api/voice/history")
    assert r.status_code == 200, r.text
    sessions = r.json()["sessions"]
    assert [s["session_id"] for s in sessions] == ["new", "old"]
    new = sessions[0]
    assert new["state"] == "done" and new["result"] is None
    assert new["created_at"].startswith("2026-10-23T19:00:00")
    a1, a2 = new["attempts"]
    assert a1 == {
        "attempt_id": "new:1", "n": 1, "song_key": "artist 1|song 1", "artist": "Artist 1", "title": "Song 1",
        "sung_at": a1["sung_at"], "feel": "fine", "position": "comfortable", "excluded": False, "recording": None,
        "falsetto_notes": 0,
    }  # fmt: skip
    assert a1["sung_at"].startswith("2026-10-23T19:08:00")
    assert a2["n"] == 2
    assert a2["recording"] == {
        "recording_id": "new:2",
        "duration_s": 180.5,
        "content_type": "audio/mp4",
        "bytes": 48_002,
    }


def test_history_is_user_scoped(make_client: MakeClient, fs: RouteFirestore) -> None:
    _seed_session(fs, "mine", attempts=1, recorded=(1,))
    _seed_session(fs, "theirs", user_id=OTHER_ID, attempts=1, recorded=(1,))
    # an attempt of another user filed under my session id never shows up
    fs.cols["voice_attempts"]["mine:9"] = {**fs.cols["voice_attempts"]["theirs:1"], "session_id": "mine", "n": 9}
    mine = make_client().get("/api/voice/history").json()["sessions"]
    assert [s["session_id"] for s in mine] == ["mine"] and [a["n"] for a in mine[0]["attempts"]] == [1]
    theirs = make_client(user_id=OTHER_ID).get("/api/voice/history").json()["sessions"]
    assert [s["session_id"] for s in theirs] == ["theirs"]


def test_history_open_when_session_flag_off(make_client: MakeClient, fs: RouteFirestore) -> None:
    _seed_session(fs, "s1", attempts=1)
    r = make_client(admin=False, open_=False).get("/api/voice/history")
    assert r.status_code == 200, r.text
    assert [s["session_id"] for s in r.json()["sessions"]] == ["s1"]


def test_history_omits_empty_sessions(client: TestClient, fs: RouteFirestore) -> None:
    _seed_session(fs, "empty", attempts=0)
    _seed_session(fs, "sung", attempts=1, created_at=T0 - timedelta(days=1))
    assert [s["session_id"] for s in client.get("/api/voice/history").json()["sessions"]] == ["sung"]


def test_history_caps_at_20(client: TestClient, fs: RouteFirestore) -> None:
    for i in range(vss.HISTORY_LIMIT + 3):
        _seed_session(fs, f"s{i:02d}", attempts=1, created_at=T0 + timedelta(hours=i))
    sessions = client.get("/api/voice/history").json()["sessions"]
    assert len(sessions) == 20
    assert sessions[0]["session_id"] == "s22" and sessions[-1]["session_id"] == "s03"


# ---------------------------------------------------------------- "Don't count this one" (Task A4)


def _patch_attempt(client: TestClient, attempt_id: str, excluded: bool) -> dict[str, Any]:
    r = client.patch(f"/api/voice/attempts/{attempt_id}", json={"excluded": excluded})
    assert r.status_code == 200, r.text
    return dict(r.json())


def _sing(client: TestClient, sid: str, k: int, start: int = 0) -> list[dict[str, Any]]:
    """Sing ``k`` songs as a simulated baritone (quiz + pitch evidence); returns the offered songs."""
    rng = random.Random(7 + start)
    bari: Hyp = ("baritone", 0)
    songs = []
    for i in range(start, start + k):
        song = _next(client, sid)
        assert song["kind"] == "song"
        position, difficulty = answer(rng, bari, _demand(song), noise=0.0)
        _post_attempt(
            client,
            sid,
            _attempt(song, i, _quiz(position=position, difficulty=difficulty), pitch=_sung_pitch(bari, _demand(song))),
        )
        songs.append(song)
    return songs


def test_excluded_attempt_ignored_by_posterior(fs: RouteFirestore) -> None:
    _seed_session(fs, "s1", attempts=2)
    session = {"id": "s1", **fs.cols["voice_sessions"]["s1"]}
    a1, a2 = ({"id": k, **v} for k, v in sorted(fs.cols["voice_attempts"].items()))
    a2["quiz"] = {**a2["quiz"], "voice_now": "tired", "position": "too_high"}
    only_first = vss.rebuild_state(session, [a1])
    with_excluded = vss.rebuild_state(session, [a1, {**a2, "excluded": True}])
    assert with_excluded.posterior.type_marginal() == only_first.posterior.type_marginal()
    assert with_excluded.range_attempts == only_first.range_attempts == 1
    assert with_excluded.tired_count == 0
    assert with_excluded.comfort_history == only_first.comfort_history
    assert with_excluded.attempts == 2  # it still took a turn ...
    assert a2["song_key"] in with_excluded.done_keys  # ... and is never offered again


def test_excluded_attempt_still_blocks_reoffer(client: TestClient) -> None:
    sid = _create(client)
    (song,) = _sing(client, sid, 1)
    out = _patch_attempt(client, f"{sid}:1", True)
    assert out["attempt_id"] == f"{sid}:1" and out["excluded"] is True and out["profile"] == "unchanged"
    for _ in range(3):
        nxt = _next(client, sid)
        assert nxt["song_key"] != song["song_key"]
        assert nxt["attempt_n"] == 2


def test_exclude_recomputes_summary(client: TestClient, fs: RouteFirestore) -> None:
    sid = _create(client)
    _sing(client, sid, 2)
    client.get(f"/api/voice/sessions/{sid}/result")
    out = _patch_attempt(client, f"{sid}:2", True)
    stored = fs.cols["voice_sessions"][sid]["result_summary"]
    assert out["result"]["evidence_attempts"] == 1 and out["result"]["attempts"] == 2
    assert stored["evidence_attempts"] == 1 and stored["session_id"] == sid
    attempt = fs.cols["voice_attempts"][f"{sid}:2"]
    assert attempt["excluded"] is True and isinstance(attempt["excluded_at"], datetime)
    history = client.get("/api/voice/history").json()["sessions"][0]
    assert [a["excluded"] for a in history["attempts"]] == [False, True]
    assert history["result"]["evidence_attempts"] == 1


def _saved_session(client: TestClient, monkeypatch: pytest.MonkeyPatch, k: int = 2) -> str:
    monkeypatch.setattr(vss, "should_save", lambda *_: True)
    sid = _create(client)
    _sing(client, sid, k)
    assert client.get(f"/api/voice/sessions/{sid}/result").json()["profile_saved"] is True
    return sid


MIC_PROFILE = {
    "low_midi": 45, "high_midi": 69, "comfort_low": 50, "comfort_high": 61, "voice_type": "bass",
    "voice_type_between": None, "register": None, "confidence": "medium", "method": "mic",
    "measured_at": "2026-10-20T10:00:00Z", "updated_at": "2026-10-20T10:00:00Z",
}  # fmt: skip
MIC_PROFILE_IN = {k: v for k, v in MIC_PROFILE.items() if k != "updated_at"}  # the PUT /api/my/voice body


def test_exclude_updates_saved_session_profile(
    client: TestClient, fs: RouteFirestore, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _saved_session(client, monkeypatch)
    assert fs.cols["decide_users"][EMAIL_HASH]["voice_profile"]["evidence_count"] == 2
    out = _patch_attempt(client, f"{sid}:1", True)
    assert out["profile"] == "updated"
    profile = fs.cols["decide_users"][EMAIL_HASH]["voice_profile"]
    assert profile["method"] == "session" and profile["session_id"] == sid and profile["evidence_count"] == 1
    assert out["result"]["profile_saved"] is True


def test_exclude_last_evidence_reverts_to_mic_profile(
    client: TestClient, fs: RouteFirestore, monkeypatch: pytest.MonkeyPatch
) -> None:
    fs.cols["decide_users"][EMAIL_HASH]["voice_profile_mic"] = dict(MIC_PROFILE)
    sid = _saved_session(client, monkeypatch, k=1)
    out = _patch_attempt(client, f"{sid}:1", True)
    assert out["profile"] == "reverted_to_mic"
    profile = fs.cols["decide_users"][EMAIL_HASH]["voice_profile"]
    assert profile["method"] == "mic" and profile["voice_type"] == "bass" and profile["comfort_high"] == 61
    assert out["result"]["profile_saved"] is False and out["result"]["evidence_attempts"] == 0
    assert fs.cols["voice_sessions"][sid]["profile_saved"] is False


def test_exclude_last_evidence_without_mic_removes_profile(
    client: TestClient, fs: RouteFirestore, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _saved_session(client, monkeypatch, k=1)
    out = _patch_attempt(client, f"{sid}:1", True)
    assert out["profile"] == "removed"
    assert not fs.cols["decide_users"][EMAIL_HASH].get("voice_profile")
    assert client.get("/api/my/voice").json()["profile"] is None


def test_unexclude_restores(client: TestClient, fs: RouteFirestore, monkeypatch: pytest.MonkeyPatch) -> None:
    sid = _saved_session(client, monkeypatch)
    assert _patch_attempt(client, f"{sid}:2", False)["profile"] == "updated"  # counting it again is a no-op ...
    before = dict(fs.cols["voice_sessions"][sid]["result_summary"])  # ... that stores the baseline summary
    assert _patch_attempt(client, f"{sid}:2", True)["profile"] == "updated"
    out = _patch_attempt(client, f"{sid}:2", False)
    assert out["excluded"] is False and out["profile"] == "updated"
    after = fs.cols["voice_sessions"][sid]["result_summary"]
    assert {k: v for k, v in after.items() if k != "computed_at"} == {
        k: v for k, v in before.items() if k != "computed_at"
    }
    assert fs.cols["voice_attempts"][f"{sid}:2"]["excluded"] is False
    assert fs.cols["voice_attempts"][f"{sid}:2"]["excluded_at"] is None
    assert fs.cols["decide_users"][EMAIL_HASH]["voice_profile"]["evidence_count"] == 2


def test_exclude_other_user_404(make_client: MakeClient, fs: RouteFirestore) -> None:
    _seed_session(fs, "s1", attempts=1)
    r = make_client(user_id=OTHER_ID).patch("/api/voice/attempts/s1:1", json={"excluded": True})
    assert r.status_code == 404 and r.json() == {"detail": "attempt_not_found"}
    assert "excluded" not in fs.cols["voice_attempts"]["s1:1"]
    assert make_client().patch("/api/voice/attempts/s1:7", json={"excluded": True}).status_code == 404
    bad = make_client().patch("/api/voice/attempts/s1:1", json={"excluded": True, "why": "x"})
    assert bad.status_code == 422


def test_exclude_on_active_session_changes_next_pick_state(make_client: MakeClient, fs: RouteFirestore) -> None:
    client = make_client(admin=False, open_=False)  # PATCH is open even with the session flag off ...
    admin = make_client()  # ... singing needs it
    sid = _create(admin)
    p0 = admin.get(f"/api/voice/sessions/{sid}").json()["top_p"]
    _sing(admin, sid, 1)
    p1 = admin.get(f"/api/voice/sessions/{sid}").json()["top_p"]
    assert p1 != p0
    out = _patch_attempt(client, f"{sid}:1", True)
    assert out["profile"] == "unchanged" and out["result"]["evidence_attempts"] == 0
    got = admin.get(f"/api/voice/sessions/{sid}").json()
    assert got["top_p"] == p0 and got["attempts"] == 1 and got["state"] == "active"
    assert "result_summary" not in fs.cols["voice_sessions"][sid]  # an active session's result isn't stored yet


# ---------------------------------------------------------------- both results on Me; adopt (Task A5)


def test_session_save_keeps_mic_copy(client: TestClient, fs: RouteFirestore, monkeypatch: pytest.MonkeyPatch) -> None:
    assert client.put("/api/my/voice", json=MIC_PROFILE_IN).status_code == 200
    sid = _saved_session(client, monkeypatch)
    doc = fs.cols["decide_users"][EMAIL_HASH]
    assert doc["voice_profile"]["method"] == "session" and doc["voice_profile"]["session_id"] == sid
    assert doc["voice_profile_mic"]["method"] == "mic" and doc["voice_profile_mic"]["voice_type"] == "bass"


def test_get_voice_returns_mic_and_latest_session(
    client: TestClient, fs: RouteFirestore, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert client.put("/api/my/voice", json=MIC_PROFILE_IN).status_code == 200
    older = _saved_session(client, monkeypatch)
    newer = _saved_session(client, monkeypatch, k=1)
    fs.cols["voice_sessions"][older]["created_at"] = T0  # creation order decides "latest"
    fs.cols["voice_sessions"][newer]["created_at"] = T0 + timedelta(hours=1)
    fs.cols["voice_sessions"][older]["updated_at"] = T0 + timedelta(hours=2)  # re-fetched later: still not latest
    got = client.get("/api/my/voice").json()
    assert got["profile"]["method"] == "session" and got["profile"]["session_id"] == newer
    assert got["mic"]["method"] == "mic" and got["mic"]["voice_type"] == "bass"
    latest = got["latest_session"]
    assert latest["session_id"] == newer and latest["evidence_attempts"] == 1 and latest["profile_saved"] is True
    # a session whose evidence was all excluded no longer counts as the latest result
    _patch_attempt(client, f"{newer}:1", True)
    assert client.get("/api/my/voice").json()["latest_session"]["session_id"] == older


def test_latest_session_present_when_low_confidence_not_saved(client: TestClient, fs: RouteFirestore) -> None:
    assert client.put("/api/my/voice", json=MIC_PROFILE_IN).status_code == 200
    sid = _create(client)
    _sing(client, sid, 1)
    res = client.get(f"/api/voice/sessions/{sid}/result").json()
    assert res["confidence_label"] == "low" and res["profile_saved"] is False
    got = client.get("/api/my/voice").json()
    assert got["profile"]["method"] == "mic"
    assert got["latest_session"]["session_id"] == sid and got["latest_session"]["profile_saved"] is False
    assert got["latest_session"]["confidence_label"] == "low"


def test_adopt_low_confidence_session_saves_profile(client: TestClient, fs: RouteFirestore) -> None:
    assert client.put("/api/my/voice", json=MIC_PROFILE_IN).status_code == 200
    sid = _create(client)
    _sing(client, sid, 1)
    assert client.get(f"/api/voice/sessions/{sid}/result").json()["profile_saved"] is False
    r = client.post(f"/api/voice/sessions/{sid}/adopt")
    assert r.status_code == 200, r.text
    adopted = r.json()
    assert adopted["method"] == "session" and adopted["session_id"] == sid and adopted["confidence"] == "low"
    doc = fs.cols["decide_users"][EMAIL_HASH]
    assert doc["voice_profile"]["session_id"] == sid and doc["voice_profile_mic"]["method"] == "mic"
    session = fs.cols["voice_sessions"][sid]
    assert session["profile_saved"] is True and session["result_summary"]["profile_saved"] is True
    assert client.get("/api/my/voice").json()["latest_session"]["profile_saved"] is True
    # someone else's session is not found
    assert client.post("/api/voice/sessions/nope/adopt").status_code == 404


def test_refetching_an_adopted_abandoned_session_keeps_it_saved(client: TestClient, fs: RouteFirestore) -> None:
    sid = _create(client)
    _sing(client, sid, 1)
    _create(client)  # a new session abandons the first one
    assert fs.cols["voice_sessions"][sid]["state"] == "abandoned"
    assert client.post(f"/api/voice/sessions/{sid}/adopt").status_code == 200
    r = client.get(f"/api/voice/sessions/{sid}/result")
    assert r.status_code == 200 and r.json()["profile_saved"] is True
    assert fs.cols["voice_sessions"][sid]["result_summary"]["profile_saved"] is True


def test_adopt_without_evidence_409(client: TestClient, make_client: MakeClient, fs: RouteFirestore) -> None:
    sid = _create(client)
    client.get(f"/api/voice/sessions/{sid}/result")  # finished before singing anything
    r = client.post(f"/api/voice/sessions/{sid}/adopt")
    assert r.status_code == 409 and r.json() == {"detail": "no_evidence"}
    assert not fs.cols["decide_users"][EMAIL_HASH].get("voice_profile")
    assert make_client(user_id=OTHER_ID).post(f"/api/voice/sessions/{sid}/adopt").status_code == 404


def test_result_summary_written_on_finish_and_refetch(client: TestClient, fs: RouteFirestore) -> None:
    sid = _create(client)
    _sing(client, sid, 2)
    res = client.get(f"/api/voice/sessions/{sid}/result").json()
    stored = fs.cols["voice_sessions"][sid]["result_summary"]
    expected = {k: res[k] for k in ("voice_type", "between", "confidence_label", "top_p", "low", "high",
                                    "comfort_low", "comfort_high", "attempts", "profile_saved")}  # fmt: skip
    assert {k: stored[k] for k in expected} == expected
    assert stored["session_id"] == sid and stored["evidence_attempts"] == 2
    first_at = stored["computed_at"]
    fs.cols["voice_sessions"][sid]["result_summary"] = None  # a refetch writes it again
    client.get(f"/api/voice/sessions/{sid}/result")
    assert fs.cols["voice_sessions"][sid]["result_summary"]["evidence_attempts"] == 2
    assert fs.cols["voice_sessions"][sid]["result_summary"]["computed_at"] >= first_at
    # an abandoned session's result is stored too (never saved)
    s1 = _create(client)
    _sing(client, s1, 1, start=5)
    _create(client)
    client.get(f"/api/voice/sessions/{s1}/result")
    assert fs.cols["voice_sessions"][s1]["state"] == "abandoned"
    assert fs.cols["voice_sessions"][s1]["result_summary"]["profile_saved"] is False


# ---------------------------------------------------------------- songs that suit you (Task A6)


def test_suited_prefers_known_artists_over_popular_unknown(client: TestClient, fs: RouteFirestore) -> None:
    """On the fixture catalog the singable songs are Muse, Taylor Swift and Adele; by fit alone Muse leads
    (its songs fit best). The singer loves Adele, so Adele's songs come first."""
    fs.cols["decide_users"][EMAIL_HASH].update(
        {
            "quiz_completed_at": "2026-10-01T00:00:00+00:00",
            "quiz_artists_known": ["Adele"],
            "quiz_artist_affinities": [{"artist_name": "Adele", "affinity": "love"}],
        }
    )
    assert client.put("/api/my/voice", json={**MIC_PROFILE_IN, "voice_type": "baritone"}).status_code == 200
    sid = _create(client)
    _sing(client, sid, 1)
    suited = client.get(f"/api/voice/sessions/{sid}/result").json()["suited"]
    assert len(suited) >= 5
    assert [s["artist"] for s in suited[:3]] == ["Adele"] * 3
    assert len({s["song_key"] for s in suited}) == len(suited)


class _FakeRecs:
    """recs v2 with a fixed answer per sort; records the calls."""

    def __init__(self, by_sort: dict[str, list[list[str]]]) -> None:
        self.by_sort = by_sort
        self.calls: list[tuple[str, bool]] = []

    @staticmethod
    def _song(key: str) -> dict[str, Any]:
        artist, _, title = key.partition("|")
        return {
            "song_key": key, "artist": artist, "title": title, "reason": "", "reason_type": "crowd_pleaser",
            "reason_artist": None, "score": 1.0, "has_karaoke_version": True,
            "karaoke": {"community": True, "commercial": False, "karafun": False, "brand_count": 1},
            "facets": {"decade": None, "genres": [], "mood": None, "energy": None, "lead_gender": None,
                       "duration_ms": None, "popularity": None, "is_classic": False, "explicit": False},
            "fit": None,
        }  # fmt: skip

    async def recommend(
        self, user: User, f: Any, sort: Any = "best", cursor: Any = None, *, profile: Any = None
    ) -> Any:
        self.calls.append((sort, bool(f.singable)))
        sections = [
            RecSection(
                id=f"s{i}",
                title_key="t",
                reason_key="r",
                title_params={},
                songs=[RecSong.model_validate(self._song(k)) for k in keys],
            )
            for i, keys in enumerate(self.by_sort.get(sort, []))
        ]
        return type("Res", (), {"sections": sections})()


def test_suited_tops_up_with_fit_when_no_taste(make_client: MakeClient, fs: RouteFirestore) -> None:
    recs = _FakeRecs(
        {
            "best": [["a|one", "b|two"], ["a|one"]],  # little taste: 2 songs (one repeated across sections)
            "fit": [["b|two", "c|three", "d|four", "e|five", "f|six"]],
        }
    )
    client = make_client()
    app.dependency_overrides[get_recommendation_service_v2_dep] = lambda: recs
    sid = _create(client)
    _sing(client, sid, 1)
    suited = client.get(f"/api/voice/sessions/{sid}/result").json()["suited"]
    assert [s["song_key"] for s in suited] == ["a|one", "b|two", "c|three", "d|four", "e|five", "f|six"]
    assert recs.calls == [("best", True), ("fit", True)]

    recs.by_sort["best"] = [[f"x|{i}" for i in range(20)]]  # plenty of taste: no top-up, capped at 15
    recs.calls.clear()
    sid = _create(client)
    _sing(client, sid, 1, start=3)
    suited = client.get(f"/api/voice/sessions/{sid}/result").json()["suited"]
    assert [s["song_key"] for s in suited] == [f"x|{i}" for i in range(vss.SUITED_LIMIT)]
    assert recs.calls == [("best", True)]


# ---------------------------------------------------------------- review fixes


LEGACY_MIC = {**MIC_PROFILE}  # a mic result saved before v0.31.0: voice_profile only, no voice_profile_mic


def test_legacy_mic_profile_survives_a_session_save(
    client: TestClient, fs: RouteFirestore, monkeypatch: pytest.MonkeyPatch
) -> None:
    fs.cols["decide_users"][EMAIL_HASH]["voice_profile"] = dict(LEGACY_MIC)
    assert client.get("/api/my/voice").json()["mic"]["voice_type"] == "bass"  # read falls back to voice_profile
    sid = _saved_session(client, monkeypatch, k=1)
    doc = fs.cols["decide_users"][EMAIL_HASH]
    assert doc["voice_profile"]["method"] == "session"
    assert doc["voice_profile_mic"]["method"] == "mic" and doc["voice_profile_mic"]["voice_type"] == "bass"
    assert client.get("/api/my/voice").json()["mic"]["voice_type"] == "bass"
    out = _patch_attempt(client, f"{sid}:1", True)
    assert out["profile"] == "reverted_to_mic"
    assert fs.cols["decide_users"][EMAIL_HASH]["voice_profile"]["voice_type"] == "bass"


def test_legacy_mic_profile_survives_adopt(client: TestClient, fs: RouteFirestore) -> None:
    fs.cols["decide_users"][EMAIL_HASH]["voice_profile"] = dict(LEGACY_MIC)
    sid = _create(client)
    _sing(client, sid, 1)
    client.get(f"/api/voice/sessions/{sid}/result")
    assert client.post(f"/api/voice/sessions/{sid}/adopt").status_code == 200
    assert fs.cols["decide_users"][EMAIL_HASH]["voice_profile_mic"]["method"] == "mic"


def test_unexclude_after_revert_restores_session_profile(
    client: TestClient, fs: RouteFirestore, monkeypatch: pytest.MonkeyPatch
) -> None:
    fs.cols["decide_users"][EMAIL_HASH]["voice_profile_mic"] = dict(MIC_PROFILE)
    sid = _saved_session(client, monkeypatch, k=1)
    assert _patch_attempt(client, f"{sid}:1", True)["profile"] == "reverted_to_mic"
    assert fs.cols["voice_sessions"][sid]["profile_displaced_by_exclusion"] is True
    out = _patch_attempt(client, f"{sid}:1", False)
    assert out["profile"] == "updated" and out["result"]["profile_saved"] is True
    profile = fs.cols["decide_users"][EMAIL_HASH]["voice_profile"]
    assert profile["method"] == "session" and profile["session_id"] == sid
    assert not fs.cols["voice_sessions"][sid].get("profile_displaced_by_exclusion")


def test_unexclude_after_remove_restores_session_profile(
    client: TestClient, fs: RouteFirestore, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _saved_session(client, monkeypatch, k=1)
    assert _patch_attempt(client, f"{sid}:1", True)["profile"] == "removed"
    out = _patch_attempt(client, f"{sid}:1", False)
    assert out["profile"] == "updated"
    assert fs.cols["decide_users"][EMAIL_HASH]["voice_profile"]["session_id"] == sid


def test_unexclude_does_not_override_a_newer_choice(
    client: TestClient, fs: RouteFirestore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After the revert the user saved a new mic test: undoing the exclusion must not overwrite it."""
    fs.cols["decide_users"][EMAIL_HASH]["voice_profile_mic"] = dict(MIC_PROFILE)
    sid = _saved_session(client, monkeypatch, k=1)
    _patch_attempt(client, f"{sid}:1", True)
    assert client.put("/api/my/voice", json={**MIC_PROFILE_IN, "voice_type": "tenor"}).status_code == 200
    assert _patch_attempt(client, f"{sid}:1", False)["profile"] == "unchanged"
    assert fs.cols["decide_users"][EMAIL_HASH]["voice_profile"]["voice_type"] == "tenor"


def test_exclude_with_missing_session_leaves_no_flag(client: TestClient, fs: RouteFirestore) -> None:
    _seed_session(fs, "s1", attempts=1)
    del fs.cols["voice_sessions"]["s1"]
    r = client.patch("/api/voice/attempts/s1:1", json={"excluded": True})
    assert r.status_code == 404
    assert "excluded" not in fs.cols["voice_attempts"]["s1:1"]


# ---------------------------------------------------------------- falsetto (Phase 3d batch 1, Part C)

CHEST = {"voiced_s": 60, "held_count": 8, "held_low": 48, "held_high": 64, "p10": 50, "p50": 55, "p90": 62}
UNFILTERED = {"voiced_s": 66, "held_count": 10, "held_low": 48, "held_high": 69, "p10": 50, "p50": 56, "p90": 66}


def _label(t: float, midi: float = 69.0, label: str = "falsetto", source: str = "live") -> dict[str, Any]:
    return {"t_start": t, "t_end": t + 0.8, "midi": midi, "label": label, "source": source}


def test_attempt_accepts_note_labels_and_pitch_all(client: TestClient, fs: RouteFirestore) -> None:
    """The app sends the chest-only summary as ``pitch``, the unfiltered one as ``pitch_all`` and its per-note
    labels; all three are stored on the attempt. An old app's request (none of the new fields) still works."""
    sid = _create(client)
    song = _next(client, sid)
    labels = [_label(31.0), _label(95.5, 67.0, "unsure", "quiz"), _label(120.0, 66.0, "full", "quiz")]
    out = _post_attempt(client, sid, _attempt(song, 0, pitch=CHEST, pitch_all=UNFILTERED, note_labels=labels))
    doc = fs.cols["voice_attempts"][out["attempt_id"]]
    assert doc["pitch"]["held_high"] == 64
    assert doc["pitch_all"] == {**UNFILTERED, "held_count": 10}
    assert doc["note_labels"] == labels

    old = _post_attempt(client, sid, _attempt(_next(client, sid), 1, pitch=UNFILTERED))
    old_doc = fs.cols["voice_attempts"][old["attempt_id"]]
    assert old_doc["note_labels"] == [] and old_doc["pitch_all"] is None


def test_note_labels_reject_extra_keys(client: TestClient, fs: RouteFirestore) -> None:
    sid = _create(client)
    song = _next(client, sid)
    bad_labels = (
        {**_label(1.0), "audio": "AAAA"},
        {**_label(1.0), "t_end": 0.5},  # ends before it starts
        {**_label(1.0), "label": "head"},
        {**_label(1.0), "source": "admin"},
        {**_label(1.0), "midi": 120},
        {**_label(1.0), "t_start": -1},
        {**_label(4000.0)},
    )
    for bad in bad_labels:
        r = client.post(f"/api/voice/sessions/{sid}/attempts", json=_attempt(song, 0, note_labels=[bad]))
        assert r.status_code == 422, bad
    r = client.post(f"/api/voice/sessions/{sid}/attempts", json=_attempt(song, 0, pitch_all={**CHEST, "frames": []}))
    assert r.status_code == 422
    assert "voice_attempts" not in fs.cols


def test_note_labels_cap_40(client: TestClient, fs: RouteFirestore) -> None:
    sid = _create(client)
    song = _next(client, sid)
    too_many = [_label(float(i)) for i in range(41)]
    r = client.post(f"/api/voice/sessions/{sid}/attempts", json=_attempt(song, 0, note_labels=too_many))
    assert r.status_code == 422
    out = _post_attempt(client, sid, _attempt(song, 0, note_labels=too_many[:40]))
    assert len(fs.cols["voice_attempts"][out["attempt_id"]]["note_labels"]) == 40


def test_history_counts_falsetto_notes(client: TestClient, fs: RouteFirestore) -> None:
    """``falsetto_notes`` counts the notes labelled falsetto (not "full" or "unsure"); attempts stored before
    labels existed read 0."""
    _seed_session(fs, "s1", attempts=2)
    fs.cols["voice_attempts"]["s1:1"]["note_labels"] = [
        _label(10.0), _label(20.0), _label(30.0, 66.0, "full"), _label(40.0, 68.0, "unsure"), _label(50.0, 70.0)
    ]  # fmt: skip
    attempts = client.get("/api/voice/history").json()["sessions"][0]["attempts"]
    assert [a["falsetto_notes"] for a in attempts] == [3, 0]
