"""Crowd stats helpers: one exact vote per singer, never negative."""

from __future__ import annotations

from backend.services.crowd_stats import (
    CROWD_COLLECTION,
    CROWD_MIN_BUCKET,
    CROWD_MIN_SINGERS,
    VOICE_BUCKETS,
    apply_pleaser,
    apply_vote,
    voice_bucket,
)


def test_constants() -> None:
    assert CROWD_COLLECTION == "song_crowd_stats"
    assert (CROWD_MIN_SINGERS, CROWD_MIN_BUCKET) == (5, 5)
    assert VOICE_BUCKETS == ("bass", "baritone", "tenor", "alto", "mezzo", "soprano")


def test_voice_bucket() -> None:
    assert voice_bucket({"voice_type": "tenor"}) == "tenor"
    assert voice_bucket({"voice_type": None, "register": "lower"}) is None
    assert voice_bucket({"voice_type": "countertenor"}) is None
    assert voice_bucket(None) is None
    assert voice_bucket("tenor") is None  # type: ignore[arg-type]


def test_apply_vote_moves_one_vote() -> None:
    s = apply_vote(None, None, ("too_high", "baritone"), now="t1")
    assert s["total"] == {"too_low": 0, "right": 0, "too_high": 1}
    assert s["by_bucket"]["baritone"]["too_high"] == 1
    assert s["updated_at"] == "t1"
    s = apply_vote(s, ("too_high", "baritone"), ("right", "tenor"), now="t2")
    assert s["total"] == {"too_low": 0, "right": 1, "too_high": 0}
    assert s["by_bucket"]["baritone"] == {"too_low": 0, "right": 0, "too_high": 0}
    assert s["by_bucket"]["tenor"]["right"] == 1


def test_apply_vote_no_bucket_counts_total_only() -> None:
    s = apply_vote(None, None, ("right", None))
    assert s["total"]["right"] == 1 and s["by_bucket"] == {}
    assert isinstance(s["updated_at"], str)


def test_apply_vote_never_negative() -> None:
    s = apply_vote(None, ("right", "tenor"), None)
    assert s["total"]["right"] == 0
    assert s["by_bucket"]["tenor"]["right"] == 0
    s = apply_vote({"total": {"right": 0}}, ("right", None), ("right", None))
    assert s["total"]["right"] == 1


def test_apply_vote_ignores_missing_key() -> None:
    s = apply_vote(None, (None, "tenor"), (None, "tenor"))
    assert s["total"] == {"too_low": 0, "right": 0, "too_high": 0}


def test_apply_pleaser() -> None:
    s = apply_pleaser(None, None, True, now="t")
    assert s["crowd_pleaser_yes"] == 1
    s = apply_pleaser(s, True, False)
    assert s["crowd_pleaser_yes"] == 0
    s = apply_pleaser(s, True, None)  # never below 0
    assert s["crowd_pleaser_yes"] == 0
    s = apply_pleaser(s, False, True)
    assert s["crowd_pleaser_yes"] == 1


def test_apply_pleaser_answered() -> None:
    s = apply_pleaser(None, None, False)
    assert (s["crowd_pleaser_yes"], s["crowd_pleaser_answered"]) == (0, 1)
    s = apply_pleaser(s, False, True)
    assert (s["crowd_pleaser_yes"], s["crowd_pleaser_answered"]) == (1, 1)
    s = apply_pleaser(s, True, None)
    assert (s["crowd_pleaser_yes"], s["crowd_pleaser_answered"]) == (0, 0)
    s = apply_pleaser(s, False, None)  # never below 0
    assert s["crowd_pleaser_answered"] == 0
