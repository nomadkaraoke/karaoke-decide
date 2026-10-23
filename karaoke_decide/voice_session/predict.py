"""Will this singer call this song good? The good/no prediction, its agreement with the singer's
own labels, and an offline replay for weight trials. This gates opening the voice session to
everyone: sign-off needs >= 80% agreement on >= 30 held-out songs, plus Andrew's OK.

``p = σ(Σ w·x)`` over the features below; good when ``p >= 0.5``. The weights are tuned with
Andrew (Task E2) and every change to ``PREDICT_WEIGHTS`` is a ledger "Ruling:".

Features (``x``), from a voice.db analysis row (``backend.services.voice_data_store.SongAnalysis``,
MIDI numbers and seconds; see ``voice_analysis.markers``) and the singer's ``UserVoiceRange``:

* ``fit``: 3c ``fit_song`` with the song range low/high/p10/p50/p90 at tier ``measured``, mapped
  by bucket through ``FIT_TERMS`` (fits 1, fitsIn 0.6, stretch -0.2, outside -1).
* ``octave_split``: 1 when the song's octave-split marker for the singer's register is set. The
  register comes from the comfort centre ``(comfort_low + comfort_high) / 2``: <= 58 male, >= 62
  female. Between those, a ``lead_gender_of_user`` of ``male``/``female`` picks the register;
  otherwise whichever marker is worse (either set → 1). A missing marker counts as 0.
* ``sustained_high_over_comfort``: 1 when ``sustained_high_count > 0`` and ``p90`` is above the
  singer's ``comfort_high``, else 0.
* ``density_over_4``: ``max(0, syllables_per_s - 4)``; 0 when density is missing.
* ``intro_under_2s``: 1 when ``intro_s < 2`` (the voice comes in almost at once); 0 when missing.
* ``bias``: always 1.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Literal, Protocol

from karaoke_decide.vocal_range.fit import FitBucket, SongVocalRange, UserVoiceRange, fit_song

__all__ = [
    "FIT_TERMS",
    "PREDICT_WEIGHTS",
    "SIGNOFF_MIN_N",
    "SIGNOFF_MIN_RATE",
    "AgreementReport",
    "SongMarkersLike",
    "agreement",
    "features",
    "held_out",
    "per_marker",
    "predict_good",
    "ready_for_signoff",
    "replay",
]

PREDICT_WEIGHTS: dict[str, float] = {
    "fit": 1.0,
    "octave_split": -0.8,
    "sustained_high_over_comfort": -0.4,
    "density_over_4": -0.3,
    "intro_under_2s": -0.2,
    "bias": 0.0,
}
FIT_TERMS: dict[FitBucket, float] = {"fits": 1.0, "fitsIn": 0.6, "stretch": -0.2, "outside": -1.0}
MALE_CENTRE_MAX = 58.0
FEMALE_CENTRE_MIN = 62.0
DENSITY_FREE_SYLLABLES_PER_S = 4.0
SHORT_INTRO_S = 2.0
SIGNOFF_MIN_N = 30
SIGNOFF_MIN_RATE = 0.80

Label = Literal["yes", "no"]


class SongMarkersLike(Protocol):
    """The voice.db analysis fields the prediction reads (``SongAnalysis`` satisfies it)."""

    @property
    def low(self) -> float | None: ...
    @property
    def high(self) -> float | None: ...
    @property
    def p10(self) -> float | None: ...
    @property
    def p50(self) -> float | None: ...
    @property
    def p90(self) -> float | None: ...
    @property
    def octave_split_male(self) -> bool | None: ...
    @property
    def octave_split_female(self) -> bool | None: ...
    @property
    def sustained_high_count(self) -> int | None: ...
    @property
    def syllables_per_s(self) -> float | None: ...
    @property
    def intro_s(self) -> float | None: ...


def _octave_split(profile: UserVoiceRange, a: SongMarkersLike, lead_gender_of_user: str | None) -> float:
    male, female = bool(a.octave_split_male), bool(a.octave_split_female)
    centre = (profile.comfort_low + profile.comfort_high) / 2
    if centre <= MALE_CENTRE_MAX:
        split = male
    elif centre >= FEMALE_CENTRE_MIN:
        split = female
    elif lead_gender_of_user == "male":
        split = male
    elif lead_gender_of_user == "female":
        split = female
    else:
        split = male or female
    return 1.0 if split else 0.0


def features(profile: UserVoiceRange, a: SongMarkersLike, lead_gender_of_user: str | None) -> dict[str, float] | None:
    """The feature vector ``x`` (see the module doc); None when the song has no measured range."""
    if a.low is None or a.high is None or a.p10 is None or a.p50 is None or a.p90 is None:
        return None
    fit = fit_song(profile, SongVocalRange(a.low, a.high, a.p10, a.p50, a.p90, "measured"))
    sustained_over = (a.sustained_high_count or 0) > 0 and a.p90 > profile.comfort_high
    density = a.syllables_per_s
    return {
        "fit": FIT_TERMS[fit.bucket],
        "octave_split": _octave_split(profile, a, lead_gender_of_user),
        "sustained_high_over_comfort": 1.0 if sustained_over else 0.0,
        "density_over_4": max(0.0, density - DENSITY_FREE_SYLLABLES_PER_S) if density is not None else 0.0,
        "intro_under_2s": 1.0 if a.intro_s is not None and a.intro_s < SHORT_INTRO_S else 0.0,
        "bias": 1.0,
    }


def _sigmoid(z: float) -> float:
    return 1 / (1 + math.exp(-z))


def _p(x: Mapping[str, float], weights: Mapping[str, float]) -> float:
    return _sigmoid(sum(weights.get(k, 0.0) * v for k, v in x.items()))


def predict_good(
    profile: UserVoiceRange | None,
    a: SongMarkersLike,
    lead_gender_of_user: str | None,
    weights: Mapping[str, float] = PREDICT_WEIGHTS,
) -> tuple[bool, float] | None:
    """``(good, p)``; None without a voice profile or a measured song range."""
    if profile is None:
        return None
    x = features(profile, a, lead_gender_of_user)
    if x is None:
        return None
    p = _p(x, weights)
    return p >= 0.5, p


@dataclass(frozen=True)
class AgreementReport:
    n: int
    agree: int
    rate: float
    confusion: dict[str, int]  # tp / fp / tn / fn, "positive" = predicted good


def agreement(rows: Iterable[tuple[Label, bool]]) -> AgreementReport:
    confusion = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    for label, pred in rows:
        good = label == "yes"
        confusion[("t" if pred == good else "f") + ("p" if pred else "n")] += 1
    n = sum(confusion.values())
    agree = confusion["tp"] + confusion["tn"]
    return AgreementReport(n=n, agree=agree, rate=agree / n if n else 0.0, confusion=confusion)


def ready_for_signoff(report: AgreementReport) -> bool:
    return report.n >= SIGNOFF_MIN_N and report.rate >= SIGNOFF_MIN_RATE


def held_out(
    labels: Iterable[Mapping[str, Any]], attempts: Iterable[Mapping[str, Any]], analysed: Iterable[str]
) -> list[str]:
    """Song keys labelled yes/no (maybe and unlabelled excluded) that have a voice.db analysis row
    and that the singer has not attempted in any session; label order, no duplicates."""
    tried = {a.get("song_key") for a in attempts}
    have = set(analysed)
    out: dict[str, None] = {}
    for label in labels:
        key = label.get("song_key")
        if label.get("good") in ("yes", "no") and key in have and key not in tried:
            out.setdefault(key, None)
    return list(out)


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def per_marker(rows: Sequence[Mapping[str, Any]], weights: Mapping[str, float]) -> dict[str, dict[str, float | None]]:
    """Mean contribution ``w·x`` of each feature over the rows the prediction agrees with vs the
    rows it gets wrong (``rows``: ``{label, features}``; the prediction is recomputed with ``weights``)."""
    groups: dict[str, dict[str, list[float]]] = {}
    for row in rows:
        x = row["features"]
        side = "agree" if (_p(x, weights) >= 0.5) == (row["label"] == "yes") else "disagree"
        for k, v in x.items():
            groups.setdefault(k, {"agree": [], "disagree": []})[side].append(weights.get(k, 0.0) * v)
    return {k: {side: _mean(vals) for side, vals in g.items()} for k, g in groups.items()}


def replay(rows: Sequence[Mapping[str, Any]], weights: Mapping[str, float]) -> dict[str, Any]:
    """The agreement report for exported rows (``{song_key, label, features, ...}``) under ``weights``.

    Features don't depend on the weights, so a weight trial needs no API and no voice.db.
    """
    scored = [(row, _p(row["features"], weights)) for row in rows]
    report = agreement((row["label"], p >= 0.5) for row, p in scored)
    return {
        **asdict(report),
        "ready_for_signoff": ready_for_signoff(report),
        "per_marker": per_marker(rows, weights),
        "weights": dict(weights),
        "rows": [{**row, "p": p, "pred": p >= 0.5} for row, p in scored],
    }
