"""What a voice session leaves behind (pure): the session voice profile, per-song labels, version stats.

* ``session_profile``: the 3c voice profile (``method: 'session'``) for a session's result.
* ``user_song_row``: one attempt folded into the song's ``user_songs`` row. A new row gets
  ``source: voice_session``; an existing row keeps its ``source`` (recs v2 reads the ``session_*``
  fields, not the source) and gains ``voice_session`` in ``sources``. ``playcount`` is never touched.
* ``bump_version_stats``: one attempt counted into ``voice_version_stats/{video_id}``.

``VoiceSessionService`` applies the row and stats writes once per attempt, in one transaction.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import ValidationError

from backend.services.voice_profile_service import VoiceProfileIn, VoiceProfileOut
from karaoke_decide.voice_session.stopping import ResultSummary

logger = logging.getLogger(__name__)

SESSION_SOURCE = "voice_session"
KeyHint = Literal["down", "up", "octave"]
_STAT_COUNTERS = ("plays", "good", "not_great", "early_exit")
_COMFORT = {"easy": "easy", "stretch": "challenging", "couldnt": "challenging"}
_MIDI_MIN, _MIDI_MAX = 24, 96


# ---------------------------------------------------------------- profile


def session_profile(s: ResultSummary, evidence_count: int, session_id: str, now: datetime) -> VoiceProfileIn | None:
    """The profile a session measured; None without range evidence (``evidence_count`` < 1)."""
    if evidence_count < 1:
        return None

    def midi(v: int) -> int:
        return max(_MIDI_MIN, min(_MIDI_MAX, v))

    try:
        return VoiceProfileIn.model_validate(
            {
                "low_midi": midi(s.full[0]),
                "high_midi": midi(s.full[1]),
                "comfort_low": midi(s.comfort[0]),
                "comfort_high": midi(s.comfort[1]),
                "voice_type": s.voice_type,
                "voice_type_between": s.between,
                "register": None,
                "confidence": s.confidence_label,
                "method": "session",
                "evidence_count": evidence_count,
                "session_id": session_id,
                "measured_at": now,
            }
        )
    except ValidationError:
        logger.warning("voice session %s: result does not make a valid voice profile", session_id, exc_info=True)
        return None


def should_save(label: str, existing: VoiceProfileOut | None) -> bool:
    """Save over nothing or a self-reported profile always; over a measured one only when not 'low'."""
    return label != "low" or existing is None or existing.method == "self"


# ---------------------------------------------------------------- per-song labels


def vocal_comfort(difficulty: str | None) -> str:
    return _COMFORT.get(difficulty or "", "comfortable")


def key_hint(quiz: Mapping[str, Any], direction: Callable[[], Literal["down", "up"]]) -> KeyHint | None:
    """Q5 "yes, in another key" or Q6 "another key" -> ``direction()``; Q6 "another octave" -> octave."""
    works_if = set(quiz.get("would_work_if") or ())
    if quiz.get("would_sing") == "yes_other_key" or "other_key" in works_if:
        return direction()
    if "other_octave" in works_if:
        return "octave"
    return None


def _sources(row: Mapping[str, Any]) -> list[str]:
    """The row's ``sources``; rows written before the field existed have just their ``source``."""
    sources = row.get("sources")
    if isinstance(sources, list):
        return [str(x) for x in sources]
    return [str(row["source"])] if row.get("source") else []


def _later(a: Any, b: str) -> str:
    try:
        return b if not a or datetime.fromisoformat(b) >= datetime.fromisoformat(str(a)) else str(a)
    except (TypeError, ValueError):  # unparsable, or naive vs aware
        return b


def user_song_row(
    existing: Mapping[str, Any] | None,
    *,
    user_id: str,
    row_id: str,
    attempt: Mapping[str, Any],
    hint: KeyHint | None,
    now: str,
) -> dict[str, Any]:
    """The ``user_songs`` row after one attempt (``times_sung`` + 1 and the latest session labels)."""
    song, quiz = attempt["song"], attempt["quiz"]
    if existing is None:
        row: dict[str, Any] = {
            "song_id": attempt["song_key"],
            "source": SESSION_SOURCE,
            "play_count": 0,
            "is_saved": False,
            "times_sung": 0,
            "last_sung_at": None,
            "created_at": now,
        }
    else:
        row = {k: v for k, v in existing.items() if k != "id"}
    ended = attempt.get("ended_at")
    sung_at = ended.isoformat() if isinstance(ended, datetime) else str(ended or now)
    row.update(
        {
            "user_id": user_id,
            "song_key": attempt["song_key"],
            "artist": row.get("artist") or song.get("artist") or "",
            "title": row.get("title") or song.get("title") or "",
            "sources": list(dict.fromkeys([*_sources(row), SESSION_SOURCE])),
            "times_sung": int(row.get("times_sung") or 0) + 1,
            "last_sung_at": _later(row.get("last_sung_at"), sung_at),
            "vocal_comfort": vocal_comfort(quiz.get("difficulty")),
            "session_feel": quiz.get("feel"),
            "session_would_sing": quiz.get("would_sing"),
            "session_key_hint": hint,
            "session_tags": sorted(quiz.get("tags") or ()),
            "updated_at": now,
        }
    )
    return row


# ---------------------------------------------------------------- version stats


def bump_version_stats(doc: Mapping[str, Any] | None, quiz: Mapping[str, Any], stopped_early: bool) -> dict[str, Any]:
    """``voice_version_stats/{video_id}`` after one attempt: plays, Q8 good / not_great, early exits, issue chips."""
    out = {k: v for k, v in (doc or {}).items() if k != "id"}
    for k in _STAT_COUNTERS:
        v = out.get(k)
        out[k] = int(v) if isinstance(v, int | float) and not isinstance(v, bool) else 0
    out["plays"] += 1
    quality = quiz.get("version_quality")
    if quality in ("good", "not_great"):
        out[quality] += 1
    if stopped_early:
        out["early_exit"] += 1
    issues = dict(out.get("issues") or {})
    for chip in quiz.get("version_issues") or ():
        issues[chip] = int(issues.get(chip) or 0) + 1
    out["issues"] = issues
    return out
