"""Crowd stats: how other singers found a song's key, one vote per singer per song (spec 6.7).

``song_crowd_stats/{song_key}`` holds aggregate counts only, never user ids:

* ``total``: ``{too_low, right, too_high}``, each singer's latest key answer;
* ``by_bucket.<bucket>``: the same, split by the singer's voice type (bass ... soprano); singers
  with a register-only or missing profile count in ``total`` alone;
* ``crowd_pleaser_yes``: singers whose latest crowd-pleaser answer is yes.

The karaoke-log transaction keeps each singer's exact contribution on their ``user_songs`` row
(``sung_key_latest`` + ``crowd_bucket``) and moves it here with ``apply_vote`` / ``apply_pleaser``
(subtract the old, add the new), so a re-answer or a profile change never double-counts.
Thresholds (``CROWD_MIN_SINGERS``, ``CROWD_MIN_BUCKET``) are applied by readers, server-side.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

CROWD_COLLECTION = "song_crowd_stats"
CROWD_MIN_SINGERS = 5
CROWD_MIN_BUCKET = 5
VOICE_BUCKETS = ("bass", "baritone", "tenor", "alto", "mezzo", "soprano")
KEY_ANSWERS = ("too_low", "right", "too_high")

Vote = tuple[str | None, str | None]  # (key_answer, bucket)


def voice_bucket(profile: dict[str, Any] | None) -> str | None:
    """The profile's ``voice_type`` when it is one of ``VOICE_BUCKETS``, else None."""
    if not isinstance(profile, dict):
        return None
    vt = profile.get("voice_type")
    return vt if isinstance(vt, str) and vt in VOICE_BUCKETS else None


def _empty_counts() -> dict[str, int]:
    return dict.fromkeys(KEY_ANSWERS, 0)


def _copy(stats: dict[str, Any] | None) -> dict[str, Any]:
    stats = stats or {}
    total = {**_empty_counts(), **(stats.get("total") or {})}
    by_bucket = {b: {**_empty_counts(), **c} for b, c in (stats.get("by_bucket") or {}).items()}
    return {**stats, "total": total, "by_bucket": by_bucket}


def _bump(counts: dict[str, int], key: str, delta: int) -> None:
    counts[key] = max(0, int(counts.get(key) or 0) + delta)


def _move(out: dict[str, Any], vote: Vote | None, delta: int) -> None:
    if vote is None:
        return
    key, bucket = vote
    if key not in KEY_ANSWERS:
        return
    _bump(out["total"], key, delta)
    if bucket in VOICE_BUCKETS:
        _bump(out["by_bucket"].setdefault(bucket, _empty_counts()), key, delta)


def apply_vote(
    stats: dict[str, Any] | None, old: Vote | None, new: Vote | None, now: str | None = None
) -> dict[str, Any]:
    """``stats`` with ``old`` taken out and ``new`` added (counts never go below 0)."""
    out = _copy(stats)
    _move(out, old, -1)
    _move(out, new, +1)
    out["updated_at"] = now or datetime.now(UTC).isoformat()
    return out


def apply_pleaser(
    stats: dict[str, Any] | None, old: bool | None, new: bool | None, now: str | None = None
) -> dict[str, Any]:
    """``crowd_pleaser_yes`` moved from ``old`` to ``new`` (one per singer, never below 0)."""
    out = _copy(stats)
    yes = int(out.get("crowd_pleaser_yes") or 0) - (1 if old is True else 0) + (1 if new is True else 0)
    out["crowd_pleaser_yes"] = max(0, yes)
    out["updated_at"] = now or datetime.now(UTC).isoformat()
    return out
