"""Good/no prediction, the agreement metric and the offline replay harness (synthetic songs only)."""

from __future__ import annotations

import importlib.util
import json
import math
from dataclasses import dataclass
from pathlib import Path

import pytest

from karaoke_decide.vocal_range.fit import SongVocalRange, UserVoiceRange, fit_song
from karaoke_decide.voice_session.predict import (
    FIT_TERMS,
    PREDICT_WEIGHTS,
    agreement,
    features,
    held_out,
    per_marker,
    predict_good,
    ready_for_signoff,
    replay,
)

MALE = UserVoiceRange(low=45, high=69, comfort_low=50, comfort_high=64)  # centre 57
FEMALE = UserVoiceRange(low=55, high=79, comfort_low=60, comfort_high=72)  # centre 66
MIDDLE = UserVoiceRange(low=50, high=70, comfort_low=56, comfort_high=64)  # centre 60


@dataclass(frozen=True)
class A:
    """The SongAnalysis fields predict reads (voice.db analysis row)."""

    low: float | None = 48.0
    high: float | None = 66.0
    p10: float | None = 52.0
    p50: float | None = 57.0
    p90: float | None = 62.0
    octave_split_male: bool | None = False
    octave_split_female: bool | None = False
    sustained_high_count: int | None = 0
    syllables_per_s: float | None = 3.0
    intro_s: float | None = 10.0


@pytest.mark.parametrize(
    ("song", "bucket"),
    [
        (A(), "fits"),
        (A(low=50, high=68, p10=53, p50=60, p90=66), "fitsIn"),
        (A(low=47, high=68, p10=50, p50=57, p90=65), "stretch"),
        (A(low=36, high=74, p10=40, p50=55, p90=70), "outside"),
    ],
)
def test_fit_bucket_terms(song: A, bucket: str) -> None:
    vr = SongVocalRange(song.low, song.high, song.p10, song.p50, song.p90, "measured")  # type: ignore[arg-type]
    assert fit_song(MALE, vr).bucket == bucket
    x = features(MALE, song, None)
    assert x is not None
    assert x["fit"] == FIT_TERMS[bucket]
    assert FIT_TERMS == {"fits": 1.0, "fitsIn": 0.6, "stretch": -0.2, "outside": -1.0}


def test_octave_split_penalty_uses_user_register() -> None:
    split_for_men = A(octave_split_male=True, octave_split_female=False)
    assert features(MALE, split_for_men, None)["octave_split"] == 1.0  # type: ignore[index]
    assert features(FEMALE, split_for_men, None)["octave_split"] == 0.0  # type: ignore[index]
    split_for_women = A(octave_split_male=False, octave_split_female=True)
    assert features(MALE, split_for_women, None)["octave_split"] == 0.0  # type: ignore[index]
    assert features(FEMALE, split_for_women, None)["octave_split"] == 1.0  # type: ignore[index]
    # centre 60 is neither register: whichever is worse, unless a lead-gender hint picks one
    assert features(MIDDLE, split_for_women, None)["octave_split"] == 1.0  # type: ignore[index]
    assert features(MIDDLE, split_for_women, "male")["octave_split"] == 0.0  # type: ignore[index]
    # the hint never overrides a clear register
    assert features(MALE, split_for_women, "female")["octave_split"] == 0.0  # type: ignore[index]
    # boundaries: centre <= 58 is male, >= 62 female
    edge_male = UserVoiceRange(low=45, high=70, comfort_low=52, comfort_high=64)  # centre 58
    edge_female = UserVoiceRange(low=50, high=75, comfort_low=56, comfort_high=68)  # centre 62
    assert features(edge_male, split_for_women, None)["octave_split"] == 0.0  # type: ignore[index]
    assert features(edge_female, split_for_men, None)["octave_split"] == 0.0  # type: ignore[index]
    # the penalty lowers p
    _, p_plain = predict_good(MALE, A(), None)  # type: ignore[misc]
    _, p_split = predict_good(MALE, split_for_men, None)  # type: ignore[misc]
    assert p_split < p_plain


def test_other_feature_definitions() -> None:
    x = features(MALE, A(sustained_high_count=2, p90=62, syllables_per_s=5.5, intro_s=1.0), None)
    assert x is not None
    assert x["sustained_high_over_comfort"] == 0.0  # p90 62 <= comfort_high 64
    assert x["density_over_4"] == pytest.approx(1.5)
    assert x["intro_under_2s"] == 1.0
    assert x["bias"] == 1.0
    over = features(MALE, A(low=50, high=68, p10=53, p50=60, p90=66, sustained_high_count=1), None)
    assert over is not None and over["sustained_high_over_comfort"] == 1.0
    none_count = features(MALE, A(p90=66, low=50, high=68, p10=53, sustained_high_count=0), None)
    assert none_count is not None and none_count["sustained_high_over_comfort"] == 0.0
    missing = features(MALE, A(syllables_per_s=None, intro_s=None, sustained_high_count=None), None)
    assert missing is not None
    assert missing["density_over_4"] == 0.0 and missing["intro_under_2s"] == 0.0
    assert missing["sustained_high_over_comfort"] == 0.0
    assert features(MALE, A(syllables_per_s=3.9), None)["density_over_4"] == 0.0  # type: ignore[index]
    assert features(MALE, A(intro_s=2.0), None)["intro_under_2s"] == 0.0  # type: ignore[index]


def test_predict_good_is_sigmoid_of_weighted_sum() -> None:
    song = A(octave_split_male=True, syllables_per_s=5.0, intro_s=1.0)
    x = features(MALE, song, None)
    assert x is not None
    z = sum(PREDICT_WEIGHTS[k] * v for k, v in x.items())
    good, p = predict_good(MALE, song, None)  # type: ignore[misc]
    assert p == pytest.approx(1 / (1 + math.exp(-z)))
    assert good is (p >= 0.5)
    assert predict_good(MALE, A(), None)[0] is True  # type: ignore[index]  # fits, nothing else: p = σ(1)
    outside = A(low=36, high=74, p10=40, p50=55, p90=70)
    assert predict_good(MALE, outside, None)[0] is False  # type: ignore[index]
    assert predict_good(MALE, A(), None, weights={**PREDICT_WEIGHTS, "bias": -5.0})[0] is False  # type: ignore[index]


def test_predict_good_none_without_profile_or_range() -> None:
    assert predict_good(None, A(), None) is None
    assert predict_good(MALE, A(p10=None), None) is None
    assert features(MALE, A(high=None), None) is None


def test_weights_are_the_brief_values() -> None:
    assert PREDICT_WEIGHTS == {
        "fit": 1.0,
        "octave_split": -0.8,
        "sustained_high_over_comfort": -0.4,
        "density_over_4": -0.3,
        "intro_under_2s": -0.2,
        "bias": 0.0,
    }


def test_agreement_counts_and_confusion() -> None:
    rows = [("yes", True), ("yes", True), ("yes", False), ("no", True), ("no", False), ("no", False)]
    r = agreement(rows)  # type: ignore[arg-type]
    assert (r.n, r.agree) == (6, 4)
    assert r.rate == pytest.approx(4 / 6)
    assert r.confusion == {"tp": 2, "fp": 1, "tn": 2, "fn": 1}
    empty = agreement([])
    assert (empty.n, empty.agree, empty.rate) == (0, 0, 0.0)
    assert empty.confusion == {"tp": 0, "fp": 0, "tn": 0, "fn": 0}


def test_held_out_excludes_attempted_and_maybe() -> None:
    labels = [
        {"song_key": "a|one", "good": "yes"},
        {"song_key": "b|two", "good": "no"},
        {"song_key": "c|three", "good": "maybe"},
        {"song_key": "d|four", "good": None},
        {"song_key": "e|five", "good": "yes"},  # attempted
        {"song_key": "f|six", "good": "no"},  # no analysis row
        {"song_key": "a|one", "good": "yes"},  # duplicate
    ]
    attempts = [{"song_key": "e|five", "session_id": "s1"}]
    analysed = {"a|one", "b|two", "c|three", "d|four", "e|five"}
    assert held_out(labels, attempts, analysed) == ["a|one", "b|two"]


def test_ready_for_signoff_thresholds() -> None:
    assert ready_for_signoff(agreement([("yes", True)] * 29)) is False
    assert ready_for_signoff(agreement([("yes", True)] * 24 + [("yes", False)] * 6)) is True  # 30 at 80%
    # 79%: 30 rows can't hit 79% exactly, so 79 of 100 (and 23/30 = 76.7%) fail
    assert ready_for_signoff(agreement([("yes", True)] * 79 + [("no", True)] * 21)) is False
    assert ready_for_signoff(agreement([("yes", True)] * 23 + [("no", True)] * 7)) is False


def test_per_marker_means_for_agree_and_disagree() -> None:
    rows = [
        {"label": "yes", "features": {"fit": 1.0, "octave_split": 0.0, "bias": 1.0}},  # agrees
        {"label": "no", "features": {"fit": -1.0, "octave_split": 1.0, "bias": 1.0}},  # agrees
        {"label": "no", "features": {"fit": 1.0, "octave_split": 0.0, "bias": 1.0}},  # disagrees
    ]
    weights = {"fit": 1.0, "octave_split": -0.8, "bias": 0.0}
    pm = per_marker(rows, weights)
    assert pm["fit"] == {"agree": pytest.approx(0.0), "disagree": pytest.approx(1.0)}
    assert pm["octave_split"] == {"agree": pytest.approx(-0.4), "disagree": pytest.approx(0.0)}
    only_agree = per_marker(rows[:2], weights)
    assert only_agree["fit"]["disagree"] is None


def test_replay_recomputes_from_features_with_new_weights() -> None:
    rows = [
        {"song_key": f"k|{i}", "label": "yes", "features": {"fit": 1.0, "octave_split": 0.0, "bias": 1.0}}
        for i in range(30)
    ]
    base = replay(rows, PREDICT_WEIGHTS)
    assert (base["n"], base["agree"], base["ready_for_signoff"]) == (30, 30, True)
    assert set(base["per_marker"]) >= {"fit", "octave_split"}
    tuned = replay(rows, {**PREDICT_WEIGHTS, "bias": -3.0})
    assert tuned["agree"] == 0 and tuned["ready_for_signoff"] is False
    assert tuned["weights"]["bias"] == -3.0


def _script():
    path = Path(__file__).resolve().parents[3] / "scripts" / "voice" / "agreement.py"
    spec = importlib.util.spec_from_file_location("voice_agreement_script", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_script_offline_rows_needs_no_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    rows = [{"song_key": "k|1", "label": "no", "features": {"fit": 1.0, "bias": 1.0}}]
    path = tmp_path / "rows.json"
    path.write_text(json.dumps(rows))
    mod = _script()
    assert mod.main(["--rows", str(path), "--weights", json.dumps({"bias": -2.0})]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["n"] == 1 and out["agree"] == 1  # σ(1 - 2) < 0.5 → "no" agrees
    assert out["weights"]["bias"] == -2.0 and out["weights"]["fit"] == 1.0


def test_script_api_mode_exports_rows_and_hides_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "very-secret-token-xyz"
    monkeypatch.setenv("ADMIN_TOKEN", secret)
    mod = _script()
    seen: dict[str, object] = {}
    rows = [{"song_key": "k|1", "label": "yes", "features": {"fit": 1.0, "bias": 1.0}}]

    def fake_get(api: str, token: str, email: str) -> dict:
        seen.update(api=api, token=token, email=email)
        return {"n": 1, "agree": 1, "rows": rows, "skipped_no_profile": 0}

    monkeypatch.setattr(mod, "fetch", fake_get)
    out_path = tmp_path / "rows.json"
    assert mod.main(["--email", "u@example.com", "--api", "https://api.example", "--export", str(out_path)]) == 0
    assert seen == {"api": "https://api.example", "token": secret, "email": "u@example.com"}
    assert json.loads(out_path.read_text()) == rows
    printed = capsys.readouterr()
    assert secret not in printed.out and secret not in printed.err
    report = json.loads(printed.out)
    assert report["n"] == 1 and "rows" not in report


def test_script_api_mode_requires_token(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    mod = _script()
    assert mod.main(["--email", "u@example.com", "--api", "https://api.example"]) == 2
    assert "ADMIN_TOKEN" in capsys.readouterr().err
