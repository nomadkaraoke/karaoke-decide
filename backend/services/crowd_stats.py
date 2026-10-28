"""Crowd stats: how other singers found a song's key, one vote per singer per song (spec 6.7).

``song_crowd_stats/{song_key}`` holds aggregate counts only, never user ids:

* ``total``: ``{too_low, right, too_high}``, each singer's latest key answer;
* ``by_bucket.<bucket>``: the same, split by the singer's voice type (bass ... soprano); singers
  with a register-only or missing profile count in ``total`` alone;
* ``crowd_pleaser_yes``: singers whose latest crowd-pleaser answer is yes, and
  ``crowd_pleaser_answered``: singers whose latest record answered it (yes or no), the base for a share.

The karaoke-log transaction keeps each singer's exact contribution on their ``user_songs`` row
(``sung_key_latest`` + ``crowd_bucket``) and moves it here with ``apply_vote`` / ``apply_pleaser``
(subtract the old, add the new), so a re-answer or a profile change never double-counts.
Thresholds (``CROWD_MIN_SINGERS``, ``CROWD_MIN_BUCKET``) are applied by readers, server-side.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

CROWD_COLLECTION = "song_crowd_stats"
CROWD_MIN_SINGERS = 5
CROWD_MIN_BUCKET = 5
VOICE_BUCKETS = ("bass", "baritone", "tenor", "alto", "mezzo", "soprano")
KEY_ANSWERS = ("too_low", "right", "too_high")
# A viewer-bucket share at or above this suggests a key shift (recs v2), unless the user has their own hint.
CROWD_KEY_HINT_SHARE = 0.6
CROWD_KEY_HINT_SEMITONES = {"too_high": -2, "too_low": 2}

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
    """``crowd_pleaser_yes`` (latest answer yes) and ``crowd_pleaser_answered`` (latest answer
    yes or no) moved from ``old`` to ``new``: one per singer, never below 0 (R17)."""
    out = _copy(stats)
    yes = int(out.get("crowd_pleaser_yes") or 0) - (old is True) + (new is True)
    answered = int(out.get("crowd_pleaser_answered") or 0) - (old is not None) + (new is not None)
    out["crowd_pleaser_yes"] = max(0, yes)
    out["crowd_pleaser_answered"] = max(0, answered)
    out["updated_at"] = now or datetime.now(UTC).isoformat()
    return out


# --------------------------------------------------------------------------- readers (thresholded)


class SongCrowd(BaseModel):
    """How the crowd found a song, thresholded server-side: raw counts below the minimums never leave.

    The overall fields are null below ``CROWD_MIN_SINGERS`` key answers; the bucket fields are null
    unless the viewer has a bucket with at least ``CROWD_MIN_BUCKET`` answers;
    ``crowd_pleaser_share`` is null below ``CROWD_MIN_SINGERS`` crowd-pleaser answers (R17)."""

    singers: int | None = None
    comfortable_share: float | None = None
    too_high_share: float | None = None
    too_low_share: float | None = None
    bucket: str | None = None
    bucket_singers: int | None = None
    bucket_comfortable_share: float | None = None
    crowd_pleaser_share: float | None = None


def _int(v: Any) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0


def _counts(raw: Any) -> dict[str, int]:
    raw = raw if isinstance(raw, dict) else {}
    return {k: _int(raw.get(k)) for k in KEY_ANSWERS}


def _bucket_counts(stats: dict[str, Any], bucket: str | None) -> dict[str, int] | None:
    if bucket not in VOICE_BUCKETS:
        return None
    by_bucket = stats.get("by_bucket")
    return _counts(by_bucket.get(bucket)) if isinstance(by_bucket, dict) else _counts(None)


def _share(n: int, of: int) -> float:
    return round(n / of, 4)


def crowd_figure(stats: dict[str, Any] | None, viewer_bucket: str | None) -> SongCrowd:
    """The thresholded crowd figure of one ``song_crowd_stats`` doc for a viewer in ``viewer_bucket``."""
    stats = stats if isinstance(stats, dict) else {}
    out = SongCrowd()
    answered = _int(stats.get("crowd_pleaser_answered"))
    if answered >= CROWD_MIN_SINGERS:
        out.crowd_pleaser_share = _share(min(_int(stats.get("crowd_pleaser_yes")), answered), answered)
    total = _counts(stats.get("total"))
    n = sum(total.values())
    if n < CROWD_MIN_SINGERS:
        return out
    out.singers = n
    out.comfortable_share = _share(total["right"], n)
    out.too_high_share = _share(total["too_high"], n)
    out.too_low_share = _share(total["too_low"], n)
    b = _bucket_counts(stats, viewer_bucket)
    bn = sum(b.values()) if b else 0
    if b is not None and bn >= CROWD_MIN_BUCKET:
        out.bucket = viewer_bucket
        out.bucket_singers = bn
        out.bucket_comfortable_share = _share(b["right"], bn)
    return out


def crowd_key_hint(stats: dict[str, Any] | None, viewer_bucket: str | None) -> int | None:
    """Semitones to shift the key by when the viewer's bucket mostly found it too high (-2) or too
    low (+2): a share of at least ``CROWD_KEY_HINT_SHARE`` over at least ``CROWD_MIN_BUCKET`` answers."""
    if not isinstance(stats, dict):
        return None
    b = _bucket_counts(stats, viewer_bucket)
    bn = sum(b.values()) if b else 0
    if b is None or bn < CROWD_MIN_BUCKET:
        return None
    for answer, semitones in CROWD_KEY_HINT_SEMITONES.items():
        if b[answer] / bn >= CROWD_KEY_HINT_SHARE:
            return semitones
    return None
