"""Tests for the voice-pool stratified sampler."""

import hashlib
import random
import time

from karaoke_decide.voice_pool.models import PoolCandidate, pool_id
from karaoke_decide.voice_pool.strata import coverage, select_pool, stratum_label

GENDERS = ["male", "female", "mixed", None]
DECADES = ["pre1980", "1980s", "1990s", "2000s", "2010s+", None]


def make(i: int, **kw) -> PoolCandidate:
    base = dict(
        song_key=f"song{i:05d}",
        artist=f"Artist {i}",
        title=f"Title {i}",
        artist_key=f"artist{i}",
        popularity=70,
        lead_gender="male",
        decade="1990s",
        energy=0.5,
        duet=False,
        vr_p50=57.0,
        vr_span=12.0,
        vr_tier="measured",
        has_ultrastar=False,
        spotify_id=None,
        duration_ms=200_000,
        versions=(),
    )
    base.update(kw)
    return PoolCandidate(**base)  # type: ignore[arg-type]


def synthetic(count: int, seed: int = 1) -> list[PoolCandidate]:
    rng = random.Random(seed)
    return [
        make(
            i,
            popularity=rng.randint(45, 100),
            lead_gender=rng.choices(GENDERS, [0.5, 0.35, 0.1, 0.05])[0],
            decade=rng.choice(DECADES),
            energy=rng.choice([0.2, 0.5, 0.9, None]),
            duet=rng.random() < 0.15,
            vr_p50=rng.choice([50.0, 58.0, 66.0, None]),
            vr_span=rng.choice([8.0, 12.0, 18.0, None]),
            has_ultrastar=rng.random() < 0.2,
        )
        for i in range(count)
    ]


def test_pool_id_stable():
    assert pool_id("x") == hashlib.sha1(b"x").hexdigest()[:16]


def test_select_hits_gender_targets():
    picks = select_pool(synthetic(5000), 200)
    assert len(picks) == 200
    genders = [stratum_label(c).split("|")[0] for c, _ in picks]
    assert abs(genders.count("gender=male") - 90) <= 5
    assert abs(genders.count("gender=female") - 80) <= 5


def test_max_two_per_artist():
    cands = [make(i, artist_key=f"a{i % 10}") for i in range(100)]
    picks = select_pool(cands, 50)
    assert len(picks) == 20
    keys = [c.artist_key for c, _ in picks]
    assert max(keys.count(k) for k in set(keys)) == 2


def test_prefers_ultrastar_when_strata_equal():
    a, b = make(1), make(2, has_ultrastar=True)
    picks = select_pool([a, b], 1)
    assert picks[0][0] is b


def test_coverage_lists_empty_values():
    picks = select_pool(synthetic(800), 100)
    assert "duet=duet" not in coverage(picks, 100).empty_values
    no_duets = [make(i, duet=False) for i in range(50)]
    report = coverage(select_pool(no_duets, 20), 20)
    assert "duet=duet" in report.empty_values
    assert report.per_dimension["duet"]["duet"] == (2, 0)


def test_below_minimum_flag():
    picks = select_pool(synthetic(500), 1000)
    assert len(picks) == 500
    assert coverage(picks, 1000).below_minimum
    assert not coverage(select_pool(synthetic(700), 650), 650).below_minimum


def test_deterministic():
    cands = synthetic(1000)
    first = [c.song_key for c, _ in select_pool(cands, 100)]
    second = [c.song_key for c, _ in select_pool(list(reversed(cands)), 100)]
    assert first == second


def test_full_scale_is_fast():
    cands = synthetic(9000, seed=3)
    start = time.perf_counter()
    picks = select_pool(cands, 1000)
    assert len(picks) == 1000
    assert time.perf_counter() - start < 10
