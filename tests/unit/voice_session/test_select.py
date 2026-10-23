"""Next-song choice: expected information gain, warm-up ordering, strata and familiarity (synthetic songs)."""

from __future__ import annotations

import pytest

from karaoke_decide.voice_session.model import Posterior
from karaoke_decide.voice_session.select import (
    SessionState,
    choose_next,
    eig,
    expected_stretch,
    know_weight,
)

from .builders import candidate, posterior, song

BARI_TENOR = posterior({("baritone", 0): 0.5, ("tenor", 0): 0.5})


def _state(post: Posterior, **overrides: object) -> SessionState:
    base: dict[str, object] = {
        "posterior": post,
        "warmth": "warm",
        "attempts": 1,
        "range_attempts": 1,
        "tired_count": 0,
        "done_keys": set(),
        "recent_strata": [],
        "comfort_history": [],
    }
    base.update(overrides)
    return SessionState(**base)  # type: ignore[arg-type]


def test_first_pick_is_comfortable_middle() -> None:
    cands = [
        candidate("low", song(40, 50)),
        candidate("mid", song(58, 63)),
        candidate("high", song(72, 82)),
        candidate("wide", song(48, 78)),
    ]
    pick = choose_next(_state(Posterior.uniform(), attempts=0), cands)
    assert pick is not None
    assert pick.song_key == "mid"
    assert pick.reason_type == "comfortable_start"


def test_first_pick_comes_from_the_most_familiar_twenty() -> None:
    familiar = [candidate(f"pop{i:02d}", song(70, 82), know=1.0, popularity=100) for i in range(20)]
    obscure = candidate("obscure", song(58, 63), know=0.2, popularity=0)
    pick = choose_next(_state(Posterior.uniform(), attempts=0), [*familiar, obscure])
    assert pick is not None
    assert pick.song_key.startswith("pop")


def test_cold_blocks_stretch_songs() -> None:
    easy, mid, hard = song(55, 63), song(56, 67), song(58, 71)
    assert expected_stretch(BARI_TENOR, easy) <= 1
    assert 1 < expected_stretch(BARI_TENOR, mid) <= 3
    assert expected_stretch(BARI_TENOR, hard) > 3
    cands = [candidate("easy", easy), candidate("mid", mid), candidate("hard", hard)]
    for attempts in (0, 1):
        pick = choose_next(_state(BARI_TENOR, warmth="cold", attempts=attempts), cands)
        assert pick is not None and pick.song_key == "easy"
    later = choose_next(_state(BARI_TENOR, warmth="cold", attempts=2), cands)
    assert later is not None and later.song_key == "mid"
    # When the cold filter would leave nothing, the least-stretchy songs are still offered.
    harder = song(57, 75)
    assert expected_stretch(BARI_TENOR, harder) > expected_stretch(BARI_TENOR, hard)
    stretchy = [candidate("a-harder", harder), candidate("b-hard", hard)]
    first = choose_next(_state(BARI_TENOR, warmth="cold", attempts=0), stretchy)
    assert first is not None and first.song_key == "b-hard"
    assert first.reason_type == "comfortable_start"
    only_hard = choose_next(_state(BARI_TENOR, warmth="cold", attempts=5), [candidate("hard", hard)])
    assert only_hard is not None and only_hard.song_key == "hard"
    assert choose_next(_state(BARI_TENOR, warmth="cold", attempts=5), stretchy) is not None
    warm = choose_next(_state(BARI_TENOR, warmth="bit_warm", attempts=1), [candidate("hard", hard)])
    assert warm is not None and warm.song_key == "hard"


def test_eig_prefers_discriminating_song() -> None:
    boundary = song(54, 66)  # too high for baritone, comfortable for tenor
    inside = song(55, 61)  # comfortable for both
    assert eig(BARI_TENOR, boundary) > 3 * eig(BARI_TENOR, inside)
    assert eig(BARI_TENOR, inside) >= 0
    certain = posterior({("tenor", 0): 1.0})
    assert eig(certain, boundary) == pytest.approx(0.0, abs=1e-6)
    cands = [candidate("a-inside", inside), candidate("b-boundary", boundary)]
    pick = choose_next(_state(BARI_TENOR), cands)
    assert pick is not None and pick.song_key == "b-boundary"


def test_avoids_last_two_strata() -> None:
    s = song(54, 66)
    cands = [candidate("a", s, stratum="rock"), candidate("b", s, stratum="pop")]
    pick = choose_next(_state(BARI_TENOR, recent_strata=["rock", "jazz"]), cands)
    assert pick is not None and pick.song_key == "b"
    older = choose_next(_state(BARI_TENOR, recent_strata=["rock", "jazz", "pop"]), cands)
    assert older is not None and older.song_key == "a"


def test_unknown_artist_downweighted_after_two_dont_know() -> None:
    kw = {"known_songs": {"k"}, "loved_songs": {"l"}, "artists": {"fav"}}
    assert know_weight("k", "x", dont_know_skips={}, **kw) == 1.0
    assert know_weight("l", "x", dont_know_skips={}, **kw) == 1.0
    assert know_weight("s", "fav", dont_know_skips={}, **kw) == 0.7
    assert know_weight("s", "x", dont_know_skips={"x": 1}, **kw) == 0.4
    assert know_weight("s", "x", dont_know_skips={"x": 2}, **kw) == 0.2
    # Two "don't know" skips override artist familiarity, but never a known or loved song.
    assert know_weight("s", "fav", dont_know_skips={"fav": 1}, **kw) == 0.7
    assert know_weight("s", "fav", dont_know_skips={"fav": 2}, **kw) == 0.2
    assert know_weight("k", "fav", dont_know_skips={"fav": 3}, **kw) == 1.0
    assert know_weight("l", "fav", dont_know_skips={"fav": 3}, **kw) == 1.0

    s = song(54, 66)
    cands = [candidate("a", s, artist="skipped", know=0.2), candidate("b", s, artist="other", know=0.4)]
    pick = choose_next(_state(BARI_TENOR), cands)
    assert pick is not None and pick.song_key == "b"


def test_preference_breaks_near_ties() -> None:
    s = song(54, 66)
    cands = [candidate("a", s, pref=0.0), candidate("b", s, pref=1.0)]
    pick = choose_next(_state(BARI_TENOR), cands)
    assert pick is not None and pick.song_key == "b"


def test_reason_labels() -> None:
    bari = posterior({("baritone", 0): 0.9, ("tenor", 0): 0.1})
    lo, hi = bari.comfort_estimate()

    def reason(c: object) -> str:
        pick = choose_next(_state(bari), [c])  # type: ignore[list-item]
        assert pick is not None
        return pick.reason_type

    assert reason(candidate("up", song(56, hi + 3))) == "upper_range"
    assert reason(candidate("down", song(lo - 3, 58))) == "lower_range"
    assert reason(candidate("lov", song(lo + 2, hi - 2), loved=True, know=1.0)) == "loved"
    assert reason(candidate("kn", song(lo + 2, hi - 2), know=1.0)) == "known"
    assert reason(candidate("ex", song(lo + 2, hi - 2), know=0.4)) == "explore"
    # A female-range song a baritone would sing an octave down is not an "upper range" pick.
    assert reason(candidate("oct", song(lo + 14, hi + 10))) != "upper_range"


def test_no_candidates_done() -> None:
    cands = [candidate("a", song(54, 66)), candidate("b", song(55, 61))]
    assert choose_next(_state(BARI_TENOR, done_keys={"a", "b"}), cands) is None
    assert choose_next(_state(BARI_TENOR), []) is None
    assert choose_next(_state(Posterior.uniform(), attempts=0), []) is None
    pick = choose_next(_state(BARI_TENOR, done_keys={"b"}), cands)
    assert pick is not None and pick.song_key == "a"
