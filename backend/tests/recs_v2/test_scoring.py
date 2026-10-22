"""v2 scoring: the weighted formula and the reason_type precedence."""

from __future__ import annotations

import pytest

from backend.services.recs_v2 import weights as w
from backend.services.recs_v2.context import ArtistSignal, UserContextV2
from backend.services.recs_v2.scoring import score
from backend.tests.recs_v2.factories import cand


def _ctx(**kw: object) -> UserContextV2:
    return UserContextV2(user_id="u", **kw)  # type: ignore[arg-type]


def test_known_song_outranks_unknown_song_by_same_artist() -> None:
    ctx = _ctx(artists={"band a": ArtistSignal(0.8, "manual", "Band A")}, known_songs={"band a|known": 1.0})
    known = score(cand("band a|known"), ctx)
    other = score(cand("band a|other"), ctx)
    assert known.score > other.score
    assert known.reason_type == "known_song"
    assert other.reason_type == "known_artist"


def test_chill_prefers_low_energy() -> None:
    ctx = _ctx(energy_pref="chill")
    low = score(cand("a|low", energy=0.2), ctx)
    high = score(cand("a|high", energy=0.9), ctx)
    assert low.score > high.score


@pytest.mark.parametrize(("pref", "best"), [("high", 0.9), ("medium", 0.55), ("chill", 0.1)])
def test_energy_preference_peaks(pref: str, best: float) -> None:
    ctx = _ctx(energy_pref=pref)
    scores = {e: score(cand(f"a|{e}", energy=e), ctx).score for e in (0.1, 0.55, 0.9)}
    assert max(scores, key=lambda e: scores[e]) == best


def test_unknown_energy_scores_zero_energy_feature() -> None:
    ctx = _ctx(energy_pref="high")
    assert score(cand("a|x", energy=None), ctx).score == score(cand("a|x", energy=None), _ctx()).score


def test_deep_cuts_lowers_popular_songs() -> None:
    hit, cut = cand("a|hit", popularity=95), cand("a|cut", popularity=10)
    neutral = _ctx()
    deep = _ctx(crowd_pref="deep_cuts")
    assert score(hit, deep).score < score(hit, neutral).score
    assert score(cut, deep).score > score(cut, neutral).score
    assert score(hit, deep).score - score(cut, deep).score < score(hit, neutral).score - score(cut, neutral).score


def test_hits_raises_popular_songs() -> None:
    hit = cand("a|hit", popularity=90)
    assert score(hit, _ctx(crowd_pref="hits")).score > score(hit, _ctx()).score


def test_formula_matches_weights_exactly() -> None:
    ctx = _ctx(
        artists={"band a": ArtistSignal(0.5, "quiz", "Band A")},
        known_songs={"band a|s": 0.4},
        genres={"rock"},
        decades={"1990s"},
        energy_pref="high",
    )
    c = cand("band a|s", popularity=60, brand_count=30, energy=0.7, similar_score=0.2, similar_via="band b")
    expected = (
        w.SCORE_AFFINITY * 0.5
        + w.SCORE_SIMILAR * 0.2
        + w.SCORE_KNOWN * 0.4
        + w.SCORE_GENRE
        + w.SCORE_DECADE
        + w.SCORE_ENERGY * 0.7
        + w.SCORE_POPULARITY * 0.6
        + w.SCORE_BRANDS * 1.0
    )
    assert score(c, ctx).score == pytest.approx(expected)


def test_other_genre_never_matches() -> None:
    ctx = _ctx(genres={"other"})
    assert score(cand("a|x", genres=("other",)), ctx).score == score(cand("a|x", genres=("other",)), _ctx()).score


def test_reason_type_precedence() -> None:
    ctx = _ctx(
        artists={"loved": ArtistSignal(0.9, "quiz", "Loved"), "seed": ArtistSignal(0.9, "quiz", "Seed")},
        known_songs={"loved|known": 1.0},
        genres={"pop"},
        decades={"2010s"},
    )
    g = ("rock",)
    assert score(cand("loved|known", "Loved", genres=g), ctx).reason_type == "known_song"
    assert score(cand("loved|x", "Loved", genres=g), ctx).reason_type == "known_artist"
    sim = score(cand("near|x", "Near", genres=("pop",), similar_via="seed", similar_score=0.6), ctx)
    assert (sim.reason_type, sim.reason_artist) == ("similar_artist", "Seed")
    assert score(cand("x|make", "X", has_karaoke=False, genres=("pop",)), ctx).reason_type == "make_it"
    assert score(cand("x|genre", "X", genres=("pop",)), ctx).reason_type == "genre_match"
    assert score(cand("x|dec", "X", genres=g, decade="2010s"), ctx).reason_type == "decade_match"
    assert score(cand("x|crowd", "X", genres=g), ctx).reason_type == "crowd_pleaser"
