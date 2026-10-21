"""Mood and energy buckets derived from Spotify valence/energy audio features."""

MOODS: tuple[str, ...] = ("happy", "chill", "sad", "intense")

VALENCE_SPLIT = 0.5
ENERGY_SPLIT = 0.55

# Half-open [lo, hi) ranges; the top bound exceeds 1.0 so energy == 1.0 is "high".
ENERGY_BANDS: dict[str, tuple[float, float]] = {
    "low": (0.0, 0.4),
    "mid": (0.4, 0.7),
    "high": (0.7, 1.01),
}


def mood_for(valence: float | None, energy: float | None) -> str | None:
    """Quadrant mood: high valence/energy happy, high/low chill, low/low sad, low/high intense."""
    if valence is None or energy is None:
        return None
    positive = valence >= VALENCE_SPLIT
    energetic = energy >= ENERGY_SPLIT
    if positive:
        return "happy" if energetic else "chill"
    return "intense" if energetic else "sad"


def energy_band(energy: float | None) -> str | None:
    """Return "low" | "mid" | "high" for an energy value, or None if unknown."""
    if energy is None:
        return None
    for name, (lo, hi) in ENERGY_BANDS.items():
        if lo <= energy < hi:
            return name
    return None
