"""Replaying a stored session through the pure model (the service's rebuild_state logic, minus Firestore)."""

from __future__ import annotations

import copy
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from backend.services.voice_session_service import rebuild_state
from karaoke_decide.voice_session import replay
from karaoke_decide.voice_session.stopping import summary

FIXTURE = Path(__file__).parent / "fixtures" / "andrew_2026_10_25.json"
_ATTEMPT_KEYS = {"n", "song_key", "demand", "quiz", "pitch", "context", "stopped_early"}


def _fixture() -> dict[str, Any]:
    return replay.load_fixture(FIXTURE)


def _service_docs(fx: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The fixture as the service stores it: a session doc and voice_attempts docs (with the song snapshot)."""
    now = datetime(2026, 10, 25, tzinfo=UTC).isoformat()
    session = {
        "id": "s1",
        "setup": fx["setup"],
        "prior_profile": {**fx["prior_profile"], "measured_at": now, "updated_at": now},
        "skips": [{"song_key": "skipped", "reason": "dont_know", "done": True}],
    }
    attempts = [
        {
            "n": a["n"],
            "song_key": a["song_key"],
            "quiz": a["quiz"],
            "pitch": a["pitch"],
            "context": a["context"],
            "stopped_early": a["stopped_early"],
            "song": {"artist": "x", "title": "y", "stratum": "pop", "demand": a["demand"]},
        }
        for a in fx["attempts"]
    ]
    return session, attempts


def test_fixture_is_numbers_only() -> None:
    fx = _fixture()
    assert set(fx) >= {"prior_profile", "attempts"}
    text = FIXTURE.read_text()
    for banned in ("radiohead", "abba", "foo fighters", "everlong", "@", "06116d77", "pixel"):
        assert banned not in text.lower()
    for a in fx["attempts"]:
        assert set(a) == _ATTEMPT_KEYS
        assert re.fullmatch(r"[0-9a-f]{10}", a["song_key"])


def test_replay_matches_service_rebuild_state() -> None:
    fx = _fixture()
    session, docs = _service_docs(fx)
    trace = replay.replay_fixture(fx)
    assert len(trace) == len(fx["attempts"]) == 4
    for k in range(1, len(docs) + 1):
        state = rebuild_state(session, docs[:k])
        state.warmth = "warm" if any((d["context"] or {}).get("warmth") == "warm" for d in docs[:k]) else state.warmth
        assert trace[k - 1] == summary(state), k


def test_rebuild_matches_service_state_fields() -> None:
    fx = _fixture()
    session, docs = _service_docs(fx)
    ours = replay.rebuild(replay.prior_from_profile(fx["prior_profile"]), session["setup"], docs, session["skips"])
    theirs = rebuild_state(session, docs)
    assert ours.posterior.log_p == pytest.approx(theirs.posterior.log_p)
    assert (ours.attempts, ours.range_attempts, ours.tired_count) == (theirs.attempts, theirs.range_attempts, 0)
    assert ours.done_keys == theirs.done_keys
    assert ours.comfort_history == pytest.approx(theirs.comfort_history)


def test_andrew_replay_measures_comfort_from_comfortable_songs() -> None:
    """The live result (v0.31) was "Probably Baritone", low confidence, 46-70, comfortable 51-65: the model's
    type-mean band (docs/VOICE-SESSION-TUNING.md). Since D1 comfort comes from what he sang on songs 1 and 4,
    the ones rated comfortable and easy (song 2 he barely knew, song 3 was a stretch with falsetto)."""
    trace = replay.replay_fixture(_fixture())
    assert [s.comfort_source for s in trace] == ["model", "model", "model", "measured"]
    last = trace[-1]
    assert last.voice_type == "baritone"
    assert last.comfort == (50, 58)


def test_replay_needs_a_demand_or_an_override() -> None:
    fx = copy.deepcopy(_fixture())
    demands = {a["n"]: a["demand"] for a in fx["attempts"]}
    for a in fx["attempts"]:
        a["demand"] = None
    with pytest.raises(ValueError, match="demand"):
        replay.replay_fixture(fx)
    assert replay.replay_fixture(fx, demands=demands) == replay.replay_fixture(_fixture())


def test_prior_from_profile_reads_register_and_type() -> None:
    mic = replay.prior_from_profile({"method": "mic", "voice_type": "bass", "confidence": "high"})
    assert max(mic.type_marginal().items(), key=lambda kv: kv[1])[0] == "bass"
    reg = replay.prior_from_profile({"method": "self", "voice_type": None, "register": "higher", "confidence": "low"})
    assert reg.type_marginal()["soprano"] > reg.type_marginal()["bass"]
    assert replay.prior_from_profile(None).type_marginal()["tenor"] == pytest.approx(1 / 6)


def test_fixture_round_trips_through_json(tmp_path: Path) -> None:
    out = tmp_path / "fx.json"
    out.write_text(json.dumps(_fixture()))
    assert replay.replay_fixture(replay.load_fixture(out)) == replay.replay_fixture(_fixture())


def _relabelled(fx: dict[str, Any], falsetto_from: float = 69.0) -> dict[str, Any]:
    """Notes at or above ``falsetto_from`` labelled falsetto: held_high capped below it, percentiles kept."""
    out = copy.deepcopy(fx)
    for a in out["attempts"]:
        p = a["pitch"]
        if p and p["held_high"] >= falsetto_from:
            p["held_high"] = falsetto_from - 1
    return out


def _trace_lines(trace: list[Any]) -> str:
    return "\n".join(
        f"  {i}: {s.voice_type} top {s.top_p:.2f} pair {s.pair_p:.2f} {s.confidence_label} "
        f"comfort {s.comfort} ({s.comfort_source}) full {s.full}"
        for i, s in enumerate(trace, 1)
    )


def test_andrew_replay() -> None:
    """The binding real-world gate: Andrew's 4 songs, A4 (69) relabelled falsetto, are baritone at medium or high
    confidence with comfort within ±2 of (49, 59). Andrew's true type is assumed baritone (A#2-A#4 vs 45-69)."""
    trace = replay.replay_fixture(_relabelled(_fixture()))
    print("\nAndrew, 69+ relabelled falsetto:\n" + _trace_lines(trace))
    last = trace[3]
    assert last.voice_type == "baritone"
    assert last.confidence_label in ("medium", "high")
    assert abs(last.comfort[0] - 49) <= 2 and abs(last.comfort[1] - 59) <= 2, last.comfort


def test_andrew_replay_unlabelled_report() -> None:
    """Not gated: the same replay with the falsetto notes left in (what today's app sends)."""
    trace = replay.replay_fixture(_fixture())
    print("\nAndrew, as recorded:\n" + _trace_lines(trace))
    assert len(trace) == 4


def test_andrew_replay_without_the_falsetto_song_is_still_baritone() -> None:
    """ "Don't count this one" on song 3 (the falsetto song): the other three songs alone say baritone, and comfort
    is still measured from songs 1 and 4. The excluded attempt still takes its turn (4 results, 4 attempts)."""
    fx = copy.deepcopy(_fixture())
    fx["attempts"][2]["excluded"] = True
    trace = replay.replay_fixture(fx)
    print("\nAndrew, song 3 excluded:\n" + _trace_lines(trace))
    assert len(trace) == 4
    assert trace[2] == trace[1]  # the excluded song moves nothing
    last = trace[3]
    assert last.voice_type == "baritone"
    assert (last.comfort, last.comfort_source) == ((50, 58), "measured")
    state = replay.rebuild(
        replay.prior_from_profile(fx["prior_profile"]),
        fx["setup"],
        [{**a, "song": {"demand": a["demand"]}} for a in fx["attempts"]],
    )
    assert (state.attempts, state.range_attempts, len(state.evidence), len(state.comfort_history)) == (4, 3, 3, 4)
    assert "cef30cca65" in state.done_keys
