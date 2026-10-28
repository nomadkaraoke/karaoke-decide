"""Crowd stats helpers: one exact vote per singer, never negative."""

from __future__ import annotations

from typing import Any

from backend.services.crowd_stats import (
    CROWD_COLLECTION,
    CROWD_MIN_BUCKET,
    CROWD_MIN_SINGERS,
    VOICE_BUCKETS,
    SongCrowd,
    apply_pleaser,
    apply_vote,
    crowd_doc_id,
    crowd_figure,
    crowd_key_hint,
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


# --------------------------------------------------------------------------- crowd_figure (C4)


def _stats(
    right: int = 0,
    too_low: int = 0,
    too_high: int = 0,
    by_bucket: dict[str, dict[str, int]] | None = None,
    yes: int = 0,
    answered: int = 0,
) -> dict[str, Any]:
    return {
        "total": {"right": right, "too_low": too_low, "too_high": too_high},
        "by_bucket": by_bucket or {},
        "crowd_pleaser_yes": yes,
        "crowd_pleaser_answered": answered,
    }


def test_crowd_hidden_below_five() -> None:
    fig = crowd_figure(_stats(right=3, too_high=1, yes=2, answered=4, by_bucket={"tenor": {"right": 4}}), "tenor")
    assert fig == SongCrowd()
    assert all(v is None for v in fig.model_dump().values())


def test_crowd_overall_at_five() -> None:
    fig = crowd_figure(_stats(right=4, too_high=1), None)
    assert fig.singers == 5
    assert fig.comfortable_share == 0.8
    assert fig.too_high_share == 0.2
    assert fig.too_low_share == 0.0
    assert fig.bucket is None and fig.bucket_singers is None and fig.bucket_comfortable_share is None
    assert fig.crowd_pleaser_share is None  # nobody answered


def test_crowd_bucket_needs_five_in_bucket() -> None:
    stats = _stats(right=6, too_high=3, by_bucket={"baritone": {"right": 2, "too_high": 2}})
    fig = crowd_figure(stats, "baritone")
    assert fig.singers == 9
    assert fig.bucket is None and fig.bucket_singers is None and fig.bucket_comfortable_share is None


def test_crowd_bucket_shown() -> None:
    stats = _stats(right=7, too_high=3, by_bucket={"baritone": {"right": 2, "too_high": 3}})
    fig = crowd_figure(stats, "baritone")
    assert fig.bucket == "baritone"
    assert fig.bucket_singers == 5
    assert fig.bucket_comfortable_share == 0.4
    assert crowd_figure(stats, None).bucket is None  # a viewer without a bucket
    assert crowd_figure(stats, "tenor").bucket is None  # a bucket nobody in it voted


def test_crowd_bucket_hidden_when_overall_below_threshold() -> None:
    # buckets are a subset of the total, so this can't happen from apply_vote; never leak it anyway
    fig = crowd_figure(_stats(right=1, by_bucket={"tenor": {"right": 5}}), "tenor")
    assert fig.bucket is None


def test_crowd_pleaser_share_needs_five_answers() -> None:
    assert crowd_figure(_stats(right=5, yes=4, answered=4), None).crowd_pleaser_share is None
    assert crowd_figure(_stats(right=5, yes=3, answered=5), None).crowd_pleaser_share == 0.6


def test_crowd_figure_tolerates_missing_and_bad_docs() -> None:
    assert crowd_figure(None, "tenor") == SongCrowd()
    assert crowd_figure({}, "tenor") == SongCrowd()
    assert crowd_figure({"total": {"right": "x"}, "by_bucket": None}, "tenor") == SongCrowd()


def test_crowd_key_hint() -> None:
    hi = _stats(right=2, too_high=3, by_bucket={"baritone": {"right": 2, "too_high": 3}})
    assert crowd_key_hint(hi, "baritone") == -2  # 3/5 = 0.6 too high
    lo = _stats(right=2, too_low=3, by_bucket={"tenor": {"right": 2, "too_low": 3}})
    assert crowd_key_hint(lo, "tenor") == 2
    mixed = _stats(right=3, too_high=2, by_bucket={"tenor": {"right": 3, "too_high": 2}})
    assert crowd_key_hint(mixed, "tenor") is None
    few = _stats(right=6, too_high=4, by_bucket={"tenor": {"too_high": 4}})
    assert crowd_key_hint(few, "tenor") is None  # 4 in the bucket < CROWD_MIN_BUCKET
    assert crowd_key_hint(hi, None) is None
    assert crowd_key_hint(None, "tenor") is None


def test_crowd_doc_id() -> None:
    """R23: sha1(song_key) hex, 32 chars; no '/' whatever the key holds; stable."""
    import hashlib

    key = "ac/dc|back in black"
    assert crowd_doc_id(key) == hashlib.sha1(key.encode()).hexdigest()[:32]
    assert len(crowd_doc_id(key)) == 32 and "/" not in crowd_doc_id(key)
    assert crowd_doc_id(key) == crowd_doc_id(key) != crowd_doc_id("ac dc|back in black")
