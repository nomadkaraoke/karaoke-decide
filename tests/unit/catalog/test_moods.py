import pytest

from karaoke_decide.catalog.moods import ENERGY_BANDS, MOODS, energy_band, mood_for


def test_moods_tuple() -> None:
    assert MOODS == ("happy", "chill", "sad", "intense")


@pytest.mark.parametrize(
    ("valence", "energy", "expected"),
    [
        (0.8, 0.9, "happy"),
        (0.8, 0.2, "chill"),
        (0.1, 0.2, "sad"),
        (0.1, 0.9, "intense"),
        (0.5, 0.55, "happy"),  # splits are inclusive on the high side
        (None, 0.5, None),
        (0.5, None, None),
    ],
)
def test_mood_for(valence: float | None, energy: float | None, expected: str | None) -> None:
    assert mood_for(valence, energy) == expected


@pytest.mark.parametrize(
    ("energy", "expected"),
    [(0.0, "low"), (0.39, "low"), (0.4, "mid"), (0.69, "mid"), (0.7, "high"), (1.0, "high"), (None, None)],
)
def test_energy_band(energy: float | None, expected: str | None) -> None:
    assert energy_band(energy) == expected


def test_bands_cover_unit_interval() -> None:
    assert ENERGY_BANDS["high"][1] > 1.0
