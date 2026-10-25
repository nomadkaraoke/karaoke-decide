# ruff: noqa: F811  (C3's fixtures are imported from test_voice_session_routes and used as parameters)
"""Voice session outputs (Task C5): the session voice profile, per-song user_songs labels, version stats.

Runs through the real app with C3's fixtures (fixture voice.db + catalog.db, in-memory Firestore).
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.api.deps import get_recommendation_service_v2_dep
from backend.main import app
from backend.services import voice_session_service as vss
from backend.services.recs_v2.service import RecVoice
from backend.services.song_feedback_service import loved_doc_id
from backend.services.voice_profile_service import VoiceProfileIn, VoiceProfileOut
from backend.tests.test_karaoke_log_routes import RouteFirestore
from backend.tests.test_voice_session_routes import (  # noqa: F401  (pytest fixtures)
    EMAIL_HASH,
    USER_ID,
    FakeHidden,
    MakeClient,
    _attempt,
    _create,
    _demand,
    _next,
    _post_attempt,
    _quiz,
    _sung_pitch,
    catalog_reader,
    client,
    fs,
    hidden,
    make_client,
    voice_store,
)
from karaoke_decide.catalog.reader import CatalogReader
from karaoke_decide.core.models import UserSong
from karaoke_decide.voice_session.model import Hyp, Posterior, centre
from karaoke_decide.voice_session.simulate import answer
from karaoke_decide.voice_session.weights import MIC_BIAS

PROFILE_BASE: dict[str, Any] = {
    "low_midi": 41, "high_midi": 69, "comfort_low": 46, "comfort_high": 64, "voice_type": "baritone",
    "voice_type_between": None, "register": None, "confidence": "high", "method": "mic",
    "measured_at": "2026-10-20T10:00:00Z", "updated_at": "2026-10-20T10:00:00Z",
}  # fmt: skip


def _store_profile(fs: RouteFirestore, **over: Any) -> None:
    fs.cols["decide_users"][EMAIL_HASH]["voice_profile"] = {**PROFILE_BASE, **over}


def _stored_profile(fs: RouteFirestore) -> dict[str, Any] | None:
    return fs.cols["decide_users"][EMAIL_HASH].get("voice_profile")


def _result(client: TestClient, sid: str) -> dict[str, Any]:
    r = client.get(f"/api/voice/sessions/{sid}/result")
    assert r.status_code == 200, r.text
    return dict(r.json())


def _drive_baritone(client: TestClient, sid: str) -> None:
    """A simulated baritone with pitch until the session is done (C3's confident path)."""
    rng = random.Random(20261022)
    bari: Hyp = ("baritone", 0)
    for i in range(12):
        song = _next(client, sid)
        if song["kind"] == "done":
            return
        position, difficulty = answer(rng, bari, _demand(song), noise=0.15)
        quiz = _quiz(position=position, difficulty=difficulty)
        out = _post_attempt(client, sid, _attempt(song, i, quiz, pitch=_sung_pitch(bari, _demand(song))))
        if out["done"]:
            return


def _user_song(fs: RouteFirestore, doc_id: str) -> dict[str, Any]:
    return fs.cols["user_songs"][doc_id]


# ---------------------------------------------------------------- profile


def test_profile_validator_session_needs_evidence() -> None:
    base = {k: v for k, v in PROFILE_BASE.items() if k != "updated_at"}
    with pytest.raises(ValidationError, match="evidence_count"):
        VoiceProfileIn.model_validate({**base, "method": "session", "confidence": "medium"})
    with pytest.raises(ValidationError, match="evidence_count"):
        VoiceProfileIn.model_validate({**base, "method": "session", "evidence_count": 0, "session_id": "s"})
    ok = VoiceProfileIn.model_validate(
        {**base, "method": "session", "confidence": "low", "evidence_count": 3, "session_id": "s1"}
    )
    assert ok.method == "session" and ok.evidence_count == 3 and ok.session_id == "s1"
    # 3c profiles still validate without the new fields
    mic = VoiceProfileIn.model_validate(base)
    assert mic.evidence_count is None and mic.session_id is None
    assert RecVoice(has_profile=True, method="session").method == "session"


def test_result_saves_profile_rules(client: TestClient, fs: RouteFirestore) -> None:
    # low confidence + an existing mic profile: the mic profile stays
    _store_profile(fs)
    sid = _create(client)
    song = _next(client, sid)
    _post_attempt(client, sid, _attempt(song, 0))
    res = _result(client, sid)
    assert res["confidence_label"] == "low" and res["profile_saved"] is False
    assert _stored_profile(fs) == PROFILE_BASE

    # medium/high confidence: saved over the mic profile, as a session profile
    sid = _create(client)
    _drive_baritone(client, sid)
    res = _result(client, sid)
    assert res["confidence_label"] in ("medium", "high")
    assert res["profile_saved"] is True
    saved = _stored_profile(fs)
    assert saved is not None
    assert saved["method"] == "session" and saved["session_id"] == sid
    assert saved["confidence"] == res["confidence_label"]
    assert saved["voice_type"] == res["voice_type"] and saved["voice_type_between"] == res["between"]
    assert (saved["low_midi"], saved["high_midi"]) == (res["low"], res["high"])
    assert (saved["comfort_low"], saved["comfort_high"]) == (res["comfort_low"], res["comfort_high"])
    attempts = [a for a in fs.cols["voice_attempts"].values() if a["session_id"] == sid]
    assert 3 <= saved["evidence_count"] <= len(attempts)
    assert datetime.fromisoformat(saved["measured_at"]).date() == datetime.now(UTC).date()
    assert saved["register"] is None
    VoiceProfileOut.model_validate(saved)


def test_refetching_an_old_result_never_overwrites_a_newer_profile(client: TestClient, fs: RouteFirestore) -> None:
    sid = _create(client)
    song = _next(client, sid)
    _post_attempt(client, sid, _attempt(song, 0))
    assert _result(client, sid)["profile_saved"] is True  # no profile before: saved
    assert fs.cols["voice_sessions"][sid]["profile_saved"] is True
    _store_profile(fs)  # a newer mic measurement
    again = _result(client, sid)
    assert again["profile_saved"] is True  # the stored outcome of the session
    assert _stored_profile(fs) == PROFILE_BASE


@pytest.mark.parametrize(
    ("existing", "saved"),
    [(None, True), ({"method": "self", "confidence": "low"}, True), ({"method": "session", "evidence_count": 4,
                                                                       "session_id": "old"}, False)],
)  # fmt: skip
def test_low_result_saves_only_over_nothing_or_self(
    client: TestClient, fs: RouteFirestore, existing: dict[str, Any] | None, saved: bool
) -> None:
    if existing is not None:
        _store_profile(fs, **existing)
    sid = _create(client)
    song = _next(client, sid)
    _post_attempt(client, sid, _attempt(song, 0))
    res = _result(client, sid)
    assert res["confidence_label"] == "low" and res["profile_saved"] is saved
    stored = _stored_profile(fs)
    if saved:
        assert stored is not None and stored["method"] == "session" and stored["evidence_count"] == 1
        assert stored["confidence"] == "low" and stored["session_id"] == sid
    else:
        assert stored is not None and stored["session_id"] == "old"


def test_result_without_range_evidence_saves_nothing(client: TestClient, fs: RouteFirestore) -> None:
    sid = _create(client)
    res = _result(client, sid)
    assert res["profile_saved"] is False
    assert _stored_profile(fs) is None


class RecordingRecs:
    def __init__(self) -> None:
        self.profiles: list[VoiceProfileOut | None] = []

    async def recommend(self, user: Any, f: Any, sort: Any = "best", cursor: Any = None, *, profile: Any = None) -> Any:
        assert f.singable is True and sort == "fit"
        self.profiles.append(profile)

        class Res:
            sections: list[Any] = []

        return Res()


@pytest.fixture
def recs(make_client: MakeClient) -> Iterator[RecordingRecs]:
    fake = RecordingRecs()
    app.dependency_overrides[get_recommendation_service_v2_dep] = lambda: fake
    yield fake


def test_suited_uses_derived_profile_when_not_saved(
    make_client: MakeClient, fs: RouteFirestore, recs: RecordingRecs
) -> None:
    client = make_client()
    _store_profile(fs)
    sid = _create(client)
    song = _next(client, sid)
    _post_attempt(client, sid, _attempt(song, 0))
    res = _result(client, sid)
    assert res["profile_saved"] is False
    (used,) = recs.profiles
    assert used is not None and used.method == "session" and used.session_id == sid
    assert (used.comfort_low, used.comfort_high) == (res["comfort_low"], res["comfort_high"])


def test_suited_uses_saved_profile(make_client: MakeClient, fs: RouteFirestore, recs: RecordingRecs) -> None:
    client = make_client()
    sid = _create(client)
    song = _next(client, sid)
    _post_attempt(client, sid, _attempt(song, 0))
    res = _result(client, sid)
    assert res["profile_saved"] is True
    (used,) = recs.profiles
    stored = _stored_profile(fs)
    assert used is not None and stored is not None
    assert used.model_dump(mode="json", by_alias=True) == stored


# ---------------------------------------------------------------- prior from a session profile


def _session_profile(confidence: str) -> VoiceProfileOut:
    return VoiceProfileOut.model_validate(
        {**PROFILE_BASE, "method": "session", "confidence": confidence, "evidence_count": 4, "session_id": "s"}
    )


def test_session_profile_prior_weights() -> None:
    """Measured profiles (mic, session) seed a range-shaped prior on their comfort band (46-64, centre 55);
    the mic's is moved up by its bias, a session's is not, whatever its confidence. Self reports keep type weights."""

    def peak(post: Posterior) -> set[float]:
        probs = post.probs()
        top = max(probs.values())
        return {centre(h) for h, p in probs.items() if p == pytest.approx(top)}

    mic = Posterior.from_profile(VoiceProfileOut.model_validate(PROFILE_BASE))
    assert peak(mic) == {55.0 + MIC_BIAS["mic"]}
    sessions = [Posterior.from_profile(_session_profile(c)) for c in ("high", "medium", "low")]
    for post in sessions:
        assert peak(post) == {55.0}
        assert post.log_p == pytest.approx(sessions[0].log_p)
    self_ = Posterior.from_profile(
        VoiceProfileOut.model_validate({**PROFILE_BASE, "method": "self", "confidence": "low"})
    ).type_marginal()
    assert self_["baritone"] / self_["soprano"] == pytest.approx(3.0)
    assert mic.type_marginal()["baritone"] > Posterior.uniform().type_marginal()["baritone"]


# ---------------------------------------------------------------- per-song labels


def test_attempt_writes_user_song_fields(client: TestClient, fs: RouteFirestore) -> None:
    sid = _create(client)
    song = _next(client, sid)
    quiz = _quiz(difficulty="stretch", would_sing="yes", tags=["fast_lyrics", "crowd_song"])
    body = _attempt(song, 0, quiz)
    _post_attempt(client, sid, body)
    doc_id = loved_doc_id(USER_ID, song["song_key"])  # no kn_id row exists: the song_key row
    row = _user_song(fs, doc_id)
    assert "id" not in row
    assert row["user_id"] == USER_ID and row["song_key"] == song["song_key"]
    assert row["artist"] == song["artist"] and row["title"] == song["title"]
    assert row["source"] == "voice_session" and row["sources"] == ["voice_session"]
    assert row["times_sung"] == 1 and row["last_sung_at"] == body["ended_at"]
    assert row["vocal_comfort"] == "challenging"
    assert row["session_feel"] == "fine" and row["session_would_sing"] == "yes"
    assert row["session_key_hint"] is None
    assert sorted(row["session_tags"]) == ["crowd_song", "fast_lyrics"]
    UserSong.model_validate({**row, "id": doc_id})

    # a retried POST counts once
    _post_attempt(client, sid, body)
    assert _user_song(fs, doc_id)["times_sung"] == 1


@pytest.mark.parametrize(
    ("difficulty", "comfort"), [("easy", "easy"), ("stretch", "challenging"), ("couldnt", "challenging"),
                                (None, "comfortable")],
)  # fmt: skip
def test_vocal_comfort_mapping(client: TestClient, fs: RouteFirestore, difficulty: str | None, comfort: str) -> None:
    sid = _create(client)
    song = _next(client, sid)
    _post_attempt(client, sid, _attempt(song, 0, _quiz(difficulty=difficulty)))
    assert _user_song(fs, loved_doc_id(USER_ID, song["song_key"]))["vocal_comfort"] == comfort


def test_attempt_lands_on_existing_kn_row_keeping_its_source(
    client: TestClient, fs: RouteFirestore, catalog_reader: CatalogReader
) -> None:
    sid = _create(client)
    song = _next(client, sid)
    kn_id = catalog_reader.kn_ids_for_song_keys([song["song_key"]])[song["song_key"]]
    row_id = f"{USER_ID}:{kn_id}"
    fs.cols.setdefault("user_songs", {})[row_id] = {
        "user_id": USER_ID, "song_id": str(kn_id), "source": "spotify_export", "playcount": 42,
        "artist": song["artist"], "title": song["title"], "times_sung": 2,
    }  # fmt: skip
    _post_attempt(client, sid, _attempt(song, 0, _quiz(would_sing="no")))
    row = _user_song(fs, row_id)
    assert row["source"] == "spotify_export" and row["playcount"] == 42
    assert row["sources"] == ["spotify_export", "voice_session"]
    assert row["times_sung"] == 3 and row["session_would_sing"] == "no"
    assert loved_doc_id(USER_ID, song["song_key"]) not in fs.cols["user_songs"]


def test_second_attempt_on_same_song_row_counts_again(client: TestClient, fs: RouteFirestore) -> None:
    """Two sessions singing one song: one row, two counts, the latest labels."""
    sid = _create(client)
    song = _next(client, sid)
    _post_attempt(client, sid, _attempt(song, 0, _quiz(would_sing="yes")))
    sid2 = _create(client)
    _post_attempt(client, sid2, _attempt(song, 1, _quiz(would_sing="no")))
    row = _user_song(fs, loved_doc_id(USER_ID, song["song_key"]))
    assert row["times_sung"] == 2 and row["session_would_sing"] == "no"
    assert row["sources"] == ["voice_session"]


def test_outputs_applied_on_retry_after_a_failed_write(
    client: TestClient, fs: RouteFirestore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The attempt doc is stored but its outputs write failed: the retried POST applies them, once."""
    real = vss.user_song_row
    calls = {"n": 0}

    def flaky(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("firestore blip")
        return real(*args, **kwargs)

    monkeypatch.setattr(vss, "user_song_row", flaky)
    sid = _create(client)
    song = _next(client, sid)
    body = _attempt(song, 0)
    with pytest.raises(RuntimeError, match="firestore blip"):
        client.post(f"/api/voice/sessions/{sid}/attempts", json=body)
    assert f"{sid}:1" in fs.cols["voice_attempts"] and "user_songs" not in fs.cols
    _post_attempt(client, sid, body)
    _post_attempt(client, sid, body)
    assert _user_song(fs, loved_doc_id(USER_ID, song["song_key"]))["times_sung"] == 1
    assert fs.cols["voice_version_stats"][song["version"]["video_id"]]["plays"] == 1
    assert fs.cols["voice_attempts"][f"{sid}:1"]["outputs_applied"] is True


def test_result_applies_outputs_an_attempt_missed(
    client: TestClient, fs: RouteFirestore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An attempt stored without its outputs (and never retried) is applied by result, once."""
    real = vss.user_song_row
    calls = {"n": 0}

    def flaky(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("firestore blip")
        return real(*args, **kwargs)

    monkeypatch.setattr(vss, "user_song_row", flaky)
    sid = _create(client)
    song = _next(client, sid)
    with pytest.raises(RuntimeError, match="firestore blip"):
        client.post(f"/api/voice/sessions/{sid}/attempts", json=_attempt(song, 0, _quiz(feel="loved")))
    assert "user_songs" not in fs.cols
    _result(client, sid)
    _result(client, sid)
    assert _user_song(fs, loved_doc_id(USER_ID, song["song_key"]))["times_sung"] == 1
    assert fs.cols["voice_version_stats"][song["version"]["video_id"]]["plays"] == 1
    assert fs.cols["voice_attempts"][f"{sid}:1"]["outputs_applied"] is True


def test_loved_calls_love(client: TestClient, fs: RouteFirestore, hidden: FakeHidden) -> None:
    sid = _create(client)
    song = _next(client, sid)
    body = _attempt(song, 0, _quiz(feel="loved"))
    _post_attempt(client, sid, body)
    _post_attempt(client, sid, body)  # retried: loved once
    assert hidden.loved == [(USER_ID, song["song_key"], song["artist"], song["title"])]
    other = _next(client, sid)
    _post_attempt(client, sid, _attempt(other, 1, _quiz(feel="fine")))
    assert len(hidden.loved) == 1


def test_session_key_hint(client: TestClient, fs: RouteFirestore) -> None:
    sid = _create(client)
    hints = []
    for i, quiz in enumerate(
        [
            _quiz(would_sing="yes_other_key", position="too_high"),
            _quiz(would_sing="no", would_work_if=["other_octave"]),
            _quiz(would_sing="no", would_work_if=["other_key"], position="too_low"),
        ]
    ):
        song = _next(client, sid)
        _post_attempt(client, sid, _attempt(song, i, quiz))
        hints.append(_user_song(fs, loved_doc_id(USER_ID, song["song_key"]))["session_key_hint"])
    assert hints == ["down", "octave", "up"]  # too high -> down a key; too low -> up


# ---------------------------------------------------------------- version stats


def test_version_stats_increment(client: TestClient, fs: RouteFirestore) -> None:
    sid = _create(client)
    song = _next(client, sid)
    vid = song["version"]["video_id"]
    fs.cols.setdefault("voice_version_stats", {})[vid] = {"embeddable": True, "plays": 4, "good": 1}
    body = _attempt(
        song, 0, _quiz(version_quality="not_great", version_issues=["bad_sync", "no_intro"]), stopped_early=True
    )
    _post_attempt(client, sid, body)
    _post_attempt(client, sid, body)  # retried: counted once
    stats = fs.cols["voice_version_stats"][vid]
    assert stats["embeddable"] is True
    assert stats["plays"] == 5 and stats["good"] == 1 and stats["not_great"] == 1
    assert stats["early_exit"] == 1
    assert stats["issues"] == {"bad_sync": 1, "no_intro": 1}

    other = _next(client, sid)
    _post_attempt(client, sid, _attempt(other, 1, _quiz(version_quality="good")))
    ostats = fs.cols["voice_version_stats"][other["version"]["video_id"]]
    assert ostats == {"plays": 1, "good": 1, "not_great": 0, "early_exit": 0, "issues": {}}
