"""Models for the voice-session song pool."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


def pool_id(song_key: str) -> str:
    """Stable 16-hex-char id for a pool song (also its GCS path component)."""
    return hashlib.sha1(song_key.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Version:
    """A community karaoke version of a song."""

    video_id: str
    brand_code: str
    brand_name: str
    rank: int
    embeddable: bool | None


@dataclass(frozen=True)
class PoolCandidate:
    """A catalog song eligible for the voice pool."""

    song_key: str
    artist: str
    title: str
    artist_key: str
    popularity: int
    lead_gender: str | None
    decade: str | None
    energy: float | None
    duet: bool
    vr_p50: float | None
    vr_span: float | None  # vr_high - vr_low, filled by the loader
    vr_tier: str | None
    has_ultrastar: bool
    spotify_id: str | None
    duration_ms: int | None
    versions: tuple[Version, ...]
