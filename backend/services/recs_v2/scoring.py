"""v2 scoring: one weighted sum per candidate plus the reason it is recommended.

Weights live in ``weights.py`` so quality iteration touches one module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from backend.services.recs_v2 import weights as w
from backend.services.recs_v2.candidates import Candidate
from backend.services.recs_v2.context import UserContextV2

ReasonType = Literal[
    "known_song", "known_artist", "similar_artist", "genre_match", "decade_match", "crowd_pleaser", "make_it"
]


@dataclass
class Scored:
    c: Candidate
    score: float
    reason_type: ReasonType
    reason_artist: str | None


def energy_match(pref: str | None, energy: float | None) -> float:
    """1.0 at the preferred end of the energy range, falling linearly; 0 when unknown."""
    if not pref or energy is None:
        return 0.0
    if pref == "chill":
        return 1 - energy
    if pref == "high":
        return energy
    if pref == "medium":
        return 1 - min(1.0, abs(energy - w.ENERGY_MEDIUM_CENTER) * 2)
    return 0.0


def score(c: Candidate, ctx: UserContextV2) -> Scored:
    signal = ctx.artists.get(c.artist_key)
    aff = signal.weight if signal else 0.0
    sim = c.similar_score
    known = ctx.known_songs.get(c.song_key, 0.0)
    quiz_genres = ctx.genres - {"other"}
    genre = 1.0 if quiz_genres and set(c.genres) & quiz_genres else 0.0
    dec = 1.0 if c.decade is not None and c.decade in ctx.decades else 0.0
    nrg = energy_match(ctx.energy_pref, c.energy)
    pop = (c.popularity or 0) / 100
    brand = min(c.brand_count, w.BRAND_CAP) / w.BRAND_CAP

    total = (
        w.SCORE_AFFINITY * aff
        + w.SCORE_SIMILAR * sim
        + w.SCORE_KNOWN * known
        + w.SCORE_GENRE * genre
        + w.SCORE_DECADE * dec
        + w.SCORE_ENERGY * nrg
        + w.SCORE_POPULARITY * pop
        + w.SCORE_BRANDS * brand
    )
    if ctx.crowd_pref == "hits":
        total += w.HITS_POPULARITY_BONUS * pop
    elif ctx.crowd_pref == "deep_cuts":
        total += w.DEEP_CUTS_OBSCURITY_BONUS * (1 - pop) - w.DEEP_CUTS_POPULARITY_PENALTY * pop

    reason: ReasonType
    reason_artist: str | None = None
    if known > 0:
        reason = "known_song"
    elif aff > 0:
        reason, reason_artist = "known_artist", c.artist
    elif sim > 0:
        reason = "similar_artist"
        seed = ctx.artists.get(c.similar_via or "")
        reason_artist = seed.name if seed else None
    elif not c.has_karaoke:
        reason = "make_it"
    elif genre:
        reason = "genre_match"
    elif dec:
        reason = "decade_match"
    else:
        reason = "crowd_pleaser"
    return Scored(c=c, score=total, reason_type=reason, reason_artist=reason_artist)
