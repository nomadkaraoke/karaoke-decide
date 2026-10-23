"""Builders for voice-model tests (synthetic songs and answers only)."""

from __future__ import annotations

import math
from dataclasses import replace

from karaoke_decide.voice_session.model import (
    HYPOTHESES,
    Context,
    Evidence,
    Hyp,
    PitchSummary,
    Posterior,
    Quiz,
    SongDemand,
)
from karaoke_decide.voice_session.select import CandidateSong


def song(p10: float, p90: float, *, spread: float = 2.0) -> SongDemand:
    """A song whose middle 80% spans p10..p90, with extremes `spread` semitones beyond."""
    return SongDemand(low=p10 - spread, high=p90 + spread, p10=p10, p50=(p10 + p90) / 2, p90=p90)


def quiz(**overrides: object) -> Quiz:
    base = Quiz(
        feel="fine",
        position="comfortable",
        difficulty=None,
        knew="well",
        would_sing="yes",
        would_work_if=frozenset(),
        voice_now=None,
        version_quality=None,
        version_issues=frozenset(),
        tags=frozenset(),
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def ctx(**overrides: object) -> Context:
    return replace(Context(posture="standing", warmth="warm", headphones=False), **overrides)  # type: ignore[arg-type]


def evidence(
    s: SongDemand,
    q: Quiz | None = None,
    c: Context | None = None,
    pitch: PitchSummary | None = None,
    stopped_early: bool = False,
) -> Evidence:
    return Evidence(song=s, quiz=q or quiz(), ctx=c or ctx(), pitch=pitch, stopped_early=stopped_early)


def posterior(weights: dict[Hyp, float]) -> Posterior:
    """A posterior with the given relative weights; hypotheses not listed get a negligible share."""
    raw = {h: weights.get(h, 1e-9) for h in HYPOTHESES}
    z = sum(raw.values())
    return Posterior({h: math.log(w / z) for h, w in raw.items()})


def candidate(
    key: str,
    s: SongDemand,
    *,
    artist: str | None = None,
    stratum: str = "pop",
    know: float = 0.4,
    pref: float = 0.0,
    popularity: int = 50,
    loved: bool = False,
) -> CandidateSong:
    return CandidateSong(
        song_key=key,
        artist_key=artist or f"artist-{key}",
        demand=s,
        stratum=stratum,
        know=know,
        pref=pref,
        popularity=popularity,
        loved=loved,
    )
