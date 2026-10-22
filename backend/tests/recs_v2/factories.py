"""Hand-built candidates and contexts for scoring / section tests."""

from __future__ import annotations

from typing import Any

from backend.services.recs_v2.candidates import Candidate


def cand(song_key: str, artist: str = "Band A", **kw: Any) -> Candidate:
    base: dict[str, Any] = {
        "song_key": song_key,
        "artist": artist,
        "title": song_key.split("|")[-1],
        "artist_key": artist.lower(),
        "has_karaoke": True,
        "community": False,
        "commercial": True,
        "karafun": False,
        "brand_count": 3,
        "is_classic": False,
        "explicit": False,
        "duration_ms": 200_000,
        "year": 1995,
        "decade": "1990s",
        "popularity": 50,
        "energy": 0.5,
        "mood": "happy",
        "lead_gender": "male",
        "genres": ("rock",),
        "similar_via": None,
        "similar_score": 0.0,
    }
    base.update(kw)
    return Candidate(**base)
