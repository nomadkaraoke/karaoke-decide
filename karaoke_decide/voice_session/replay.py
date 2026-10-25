"""Rebuild a session's state from stored attempts, and replay a stored session song by song.

Pure Python. ``rebuild`` is the voice session service's ``rebuild_state`` loop (the service passes its
validated prior and the Firestore docs); ``replay_attempts`` runs it for each prefix of a session and returns
the result the singer would have seen after each song. Fixtures (``tests/unit/voice_session/fixtures``,
written by ``scripts/voice/export_session_fixture.py``) hold numbers only::

    {prior_profile, setup?, attempts: [{n, song_key, demand, quiz, pitch, context, stopped_early}]}

``song_key`` is ``sha1(song_key)[:10]``; ``demand`` is the attempt's song snapshot demand
(``{low, high, p10, p50, p90}``), or null with the demand passed in ``demands`` instead.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from karaoke_decide.voice_session.model import (
    Context,
    Evidence,
    PitchSummary,
    Posterior,
    Quiz,
    SongDemand,
    usable_pitch,
)
from karaoke_decide.voice_session.select import SessionState
from karaoke_decide.voice_session.stopping import ResultSummary, summary

_PITCH_KEYS = ("voiced_s", "held_count", "held_low", "held_high", "p10", "p50", "p90")
_DEMAND_KEYS = ("low", "high", "p10", "p50", "p90")


def quiz_from_doc(d: Mapping[str, Any]) -> Quiz:
    return Quiz(
        feel=d["feel"],
        position=d["position"],
        difficulty=d.get("difficulty"),
        knew=d.get("knew"),
        would_sing=d.get("would_sing"),
        would_work_if=frozenset(d.get("would_work_if") or ()),
        voice_now=d.get("voice_now"),
        version_quality=d.get("version_quality"),
        version_issues=frozenset(d.get("version_issues") or ()),
        tags=frozenset(d.get("tags") or ()),
    )


def pitch_from_doc(d: Mapping[str, Any] | None) -> PitchSummary | None:
    if not d:
        return None
    return PitchSummary(**{k: float(d[k]) for k in _PITCH_KEYS})


def demand_from_doc(d: Mapping[str, Any]) -> SongDemand:
    return SongDemand(**{k: float(d[k]) for k in _DEMAND_KEYS})


def has_range_evidence(quiz: Quiz, pitch: PitchSummary | None) -> bool:
    """Q2 answered (always, the API requires it) and the song known better than "barely" or pitch usable."""
    return quiz.position is not None and (quiz.knew != "barely" or usable_pitch(pitch))


def done_skip_keys(skips: Sequence[Mapping[str, Any]]) -> set[str]:
    return {s["song_key"] for s in skips if s.get("done")}


def rebuild(
    prior: Posterior,
    setup: Mapping[str, Any],
    attempts: Sequence[Mapping[str, Any]],
    skips: Sequence[Mapping[str, Any]] = (),
    on_attempt: Callable[[SessionState, Mapping[str, Any]], None] | None = None,
) -> SessionState:
    """SessionState from the prior, the session setup and its attempt docs (in ``n`` order).

    Attempt docs are the stored ``voice_attempts`` shape: ``quiz``, ``pitch``, ``context``, ``stopped_early``,
    ``song_key`` and ``song.demand`` (``song.stratum`` optional). An ``excluded`` attempt ("Don't count this one")
    counts in ``attempts`` and done songs only: no posterior update, ``range_attempts``, ``tired_count`` or
    ``comfort_history`` entry. ``on_attempt(state, doc)`` runs after each one, excluded or not.
    """
    state = SessionState(
        posterior=prior,
        warmth=setup.get("warmth") or "cold",
        attempts=0,
        range_attempts=0,
        tired_count=0,
        comfort_history=[prior.comfort_estimate()],
    )
    for a in sorted(attempts, key=lambda a: int(a["n"])):
        song = a["song"]
        state.attempts += 1
        state.done_keys.add(a["song_key"])
        state.recent_strata.append(song.get("stratum") or "")
        if not a.get("excluded"):  # "Don't count this one": it took its turn, but it is no evidence
            quiz, pitch = quiz_from_doc(a["quiz"]), pitch_from_doc(a.get("pitch"))
            c = a.get("context") or {}
            ctx = Context(posture=c.get("posture") or "standing", warmth=c.get("warmth") or "cold",
                          headphones=bool(c.get("headphones")))  # fmt: skip
            stopped = bool(a.get("stopped_early"))
            ev = Evidence(demand_from_doc(song["demand"]), quiz, ctx, pitch, stopped_early=stopped)
            state.posterior = state.posterior.update(ev)
            state.evidence.append(ev)  # excluded attempts never reach the measured comfort
            state.range_attempts += int(has_range_evidence(quiz, pitch))
            state.tired_count += int(quiz.voice_now == "tired")
            state.comfort_history.append(state.posterior.comfort_estimate())
        if on_attempt is not None:
            on_attempt(state, a)
    state.done_keys |= done_skip_keys(skips)
    return state


@dataclass(frozen=True)
class StoredProfile:
    """A stored voice profile dict as the model's ``VoiceProfileLike`` (no validation)."""

    method: str
    voice_type: str | None
    voice_type_between: str | None = None
    confidence: str | None = None
    register: str | None = None
    comfort_low: int | None = None
    comfort_high: int | None = None

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> StoredProfile:
        return cls(
            method=str(d.get("method") or ""),
            voice_type=d.get("voice_type"),
            voice_type_between=d.get("voice_type_between"),
            confidence=d.get("confidence"),
            register=d.get("register", d.get("register_")),
            comfort_low=d.get("comfort_low"),
            comfort_high=d.get("comfort_high"),
        )


def prior_from_profile(profile: Mapping[str, Any] | None) -> Posterior:
    return Posterior.from_profile(StoredProfile.from_dict(profile) if profile else None)


def replay_attempts(
    prior_profile: Mapping[str, Any] | None,
    attempts: Sequence[Mapping[str, Any]],
    *,
    setup: Mapping[str, Any] | None = None,
    demands: Mapping[int, Mapping[str, Any]] | None = None,
) -> list[ResultSummary]:
    """The result after each attempt (fixture attempt shape), as the service's ``result()`` would compute it.

    Like ``result()``, the hint reads warmth as "ever warm" (setup or any attempt so far)."""
    docs = []
    for a in attempts:
        demand = a.get("demand") or (demands or {}).get(int(a["n"]))
        if demand is None:
            raise ValueError(f"attempt {a['n']} has no demand: pass it in demands={{n: demand}}")
        docs.append({**a, "song": {"demand": demand}})
    setup = setup or {}
    trace: list[ResultSummary] = []
    ever_warm = setup.get("warmth") == "warm"

    def record(state: SessionState, doc: Mapping[str, Any]) -> None:
        nonlocal ever_warm
        ever_warm = ever_warm or (doc.get("context") or {}).get("warmth") == "warm"
        trace.append(summary(dataclasses.replace(state, warmth="warm" if ever_warm else state.warmth)))

    rebuild(prior_from_profile(prior_profile), setup, docs, on_attempt=record)
    return trace


def replay_fixture(
    fixture: Mapping[str, Any], *, demands: Mapping[int, Mapping[str, Any]] | None = None
) -> list[ResultSummary]:
    return replay_attempts(
        fixture.get("prior_profile"), fixture["attempts"], setup=fixture.get("setup"), demands=demands
    )


def load_fixture(path: str | Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(Path(path).read_text())
    return data
