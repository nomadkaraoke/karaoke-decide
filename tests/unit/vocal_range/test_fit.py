import json
from pathlib import Path

import pytest

from karaoke_decide.vocal_range.fit import (
    STRETCH_MAX,
    TIER_TOLERANCE,
    FitResult,
    SongVocalRange,
    UserVoiceRange,
    fit_song,
    is_singable,
)
from karaoke_decide.vocal_range.notes import VOICE_TYPES

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
CASES = json.loads((FIXTURES / "voice" / "fit_cases.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_fit_matches_fixture(case):
    r = fit_song(UserVoiceRange(**case["user"]), SongVocalRange(**case["song"]))
    e = case["expect"]
    assert (r.bucket, r.transpose, r.octave) == (e["bucket"], e["transpose"], e["octave"])
    assert r.cost == pytest.approx(e["cost"])
    assert r.score == pytest.approx(e["score"])


def test_fixture_has_every_bucket_and_tier():
    assert {c["expect"]["bucket"] for c in CASES} == {"fits", "fitsIn", "stretch", "outside"}
    assert {c["song"]["tier"] for c in CASES} == {"measured", "notes", "estimated", "rough"}
    assert len(CASES) >= 16
    assert len({c["name"] for c in CASES}) == len(CASES)


def test_voice_types_order_and_values():
    assert list(VOICE_TYPES.items()) == [
        ("bass", (40, 64)),
        ("baritone", (45, 69)),
        ("tenor", (48, 72)),
        ("alto", (53, 77)),
        ("mezzo", (57, 81)),
        ("soprano", (60, 84)),
    ]


def test_constants():
    assert STRETCH_MAX == 2.0
    assert TIER_TOLERANCE == {"measured": 0, "notes": 0, "estimated": 1, "rough": 2}


def test_is_singable():
    assert is_singable(FitResult("fits", 0, 0, 0.0, 0.0))
    assert is_singable(FitResult("fitsIn", -2, 0, 0.0, 0.1))
    assert is_singable(FitResult("stretch", 0, 0, 1.0, 1.0))
    assert not is_singable(FitResult("outside", 0, 0, 24.0, 24.0))
