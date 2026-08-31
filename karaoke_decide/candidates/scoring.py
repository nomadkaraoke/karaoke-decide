"""Karaoke-suitability score.

Combines Spotify audio features with LRCLIB lyric-richness into a single 0-100
score. It's used as a cheap pre-filter (drop the egregiously-instrumental / long
before spending an LLM call) and for ranking/surfacing — the LLM judge is the
real quality gate. Weights are interpretable and recall-biased (don't drop a
good song); tune via the CLI.

Validated against Andrew's 20 hand-labelled tracks (2026-08-30): instrumentalness
is the dominant signal but noisy on DnB, so the score only reliably removes the
clear-cut cases; the LLM judge handles the rest.
"""

from __future__ import annotations

from dataclasses import dataclass

from karaoke_decide.candidates.lyrics import LyricsStats
from karaoke_decide.services.spotify_features import SpotifyFeatures


@dataclass(frozen=True)
class ScoreWeights:
    vocal: float = 0.55  # (1 - instrumentalness)
    richness: float = 0.25  # lyric unique-line richness
    duration: float = 0.20  # penalty for over-long tracks
    richness_target_lines: int = 15  # unique lines that saturate the richness term
    duration_free_min: float = 6.0  # no penalty at/under this many minutes
    duration_zero_min: float = 10.0  # penalty saturates (score 0 term) here


@dataclass(frozen=True)
class ScoreBreakdown:
    score: float
    vocal_term: float
    richness_term: float
    duration_term: float

    def as_dict(self) -> dict[str, float]:
        return {
            "score": round(self.score, 1),
            "vocal_term": round(self.vocal_term, 3),
            "richness_term": round(self.richness_term, 3),
            "duration_term": round(self.duration_term, 3),
        }


def suitability(
    features: SpotifyFeatures,
    stats: LyricsStats,
    weights: ScoreWeights | None = None,
) -> ScoreBreakdown:
    """Return a 0-100 karaoke-suitability score and its component terms."""
    w = weights or ScoreWeights()

    vocal = 1.0 - features.instrumentalness  # higher = more vocal
    richness = min(1.0, stats.unique_lines / w.richness_target_lines)

    span = max(0.001, w.duration_zero_min - w.duration_free_min)
    dur_penalty = min(1.0, max(0.0, (features.duration_min - w.duration_free_min) / span))
    duration = 1.0 - dur_penalty  # higher = better (shorter/OK length)

    score = 100.0 * (w.vocal * vocal + w.richness * richness + w.duration * duration)
    return ScoreBreakdown(
        score=score,
        vocal_term=vocal,
        richness_term=richness,
        duration_term=duration,
    )
