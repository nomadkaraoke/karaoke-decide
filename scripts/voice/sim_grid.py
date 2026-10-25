#!/usr/bin/env python3
"""Coordinate-wise search of the voice-session weights against the 3-5 song gates (Phase 3d batch 1, Task E5).

Objective: ``exact_at[5]`` for realistic singers (``simulate.AnswerStyle``) with the mic prior and headphone pitch
(seed 20261025, n 300). A setting only counts when it passes every gate:

* realistic, mic, headphones: adjacent@5 ≥ 0.92, confident≤5 ≥ 0.50, confidently-wrong≤5 ≤ 0.10;
* realistic, mic, open mic: exact@5 ≥ 0.60;
* Andrew's replay with 69+ relabelled falsetto: baritone, medium/high, comfort within ±2 of (49, 59);
* the existing gate (model answers, self-register prior, no pitch, seed 20261022, n 200): confident≤10 ≥ 0.80,
  adjacent ≥ 0.90, exact ≥ 0.70.

Each knob is tried over its grid with the others fixed, the best passing value is kept (ties keep the current
value: one or two singers in 300 is seed noise), and the pass repeats until nothing changes.

  poetry run python scripts/voice/sim_grid.py                 # about 3 minutes on 8 cores
  poetry run python scripts/voice/sim_grid.py --passes 1 --n 120 --workers 4
"""

from __future__ import annotations

import argparse
import copy
import random
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

FIXTURE = Path(__file__).resolve().parents[2] / "tests/unit/voice_session/fixtures/andrew_2026_10_25.json"
SEED = 20261025
OLD_SEED = 20261022

# knob -> (module attribute paths, grid). Every path is patched, since some modules import the value by name.
KNOBS: dict[str, tuple[tuple[str, ...], tuple[float, ...]]] = {
    "W_PITCH_HEADPHONES": (("weights.W_PITCH_HEADPHONES",), (1.5, 2.0, 3.0)),
    "W_TESSITURA": (("weights.W_TESSITURA",), (1.0, 2.0, 3.0)),
    "TESS_SD": (("weights.TESS_SD", "model.TESS_SD"), (2.5, 3.0, 4.0)),
    "PRIOR_SD": (("weights.PRIOR_SD", "model.PRIOR_SD"), (2.5, 3.0, 4.0)),
    "SEP_WEIGHT": (("weights.SEP_WEIGHT", "select.SEP_WEIGHT"), (0.3, 0.5, 0.7)),
    "LIKE_BASE": (("weights.LIKE_BASE", "select.LIKE_BASE"), (0.5, 0.6, 0.8)),
}


def apply(knobs: dict[str, float]) -> None:
    """Patch the voice-session modules in this process."""
    import importlib

    for name, value in knobs.items():
        for path in KNOBS[name][0]:
            mod, attr = path.split(".")
            setattr(importlib.import_module(f"karaoke_decide.voice_session.{mod}"), attr, value)


def current() -> dict[str, float]:
    from karaoke_decide.voice_session import weights

    return {name: float(getattr(weights, name)) for name in KNOBS}


@dataclass(frozen=True)
class Score:
    knobs: tuple[tuple[str, float], ...]
    exact5: float
    adjacent5: float
    confident5: float
    wrong5: float
    open_exact5: float
    andrew: str
    andrew_ok: bool
    old_ok: bool
    old: str

    @property
    def passes(self) -> bool:
        return (
            self.adjacent5 >= 0.92
            and self.confident5 >= 0.50
            and self.wrong5 <= 0.10
            and self.open_exact5 >= 0.60
            and self.andrew_ok
            and self.old_ok
        )

    def line(self) -> str:
        k = " ".join(f"{n}={v:g}" for n, v in self.knobs)
        return (
            f"{'PASS' if self.passes else 'fail'} exact@5 {self.exact5:.1%} adj@5 {self.adjacent5:.0%} "
            f"conf≤5 {self.confident5:.0%} wrong≤5 {self.wrong5:.1%} open@5 {self.open_exact5:.0%} "
            f"| andrew {self.andrew} | old {self.old} | {k}"
        )


def evaluate(args: tuple[dict[str, float], int]) -> Score:
    knobs, n = args
    apply(knobs)
    from karaoke_decide.voice_session import replay
    from karaoke_decide.voice_session.select import _answer_table, _stretch_vector
    from karaoke_decide.voice_session.simulate import run_population

    _answer_table.cache_clear()
    _stretch_vector.cache_clear()

    def pop(**kw: Any) -> Any:
        return run_population(random.Random(SEED), n_users=n, prior="mic", budget=10, answer_style="realistic", **kw)

    hp, op = pop(pitch="headphones"), pop(pitch="open")
    fx = replay.load_fixture(FIXTURE)
    rel = copy.deepcopy(fx)
    for a in rel["attempts"]:
        if a["pitch"] and a["pitch"]["held_high"] >= 69:
            a["pitch"]["held_high"] = 68.0
    last = replay.replay_fixture(rel)[3]
    andrew_ok = (
        last.voice_type == "baritone"
        and last.confidence_label in ("medium", "high")
        and abs(last.comfort[0] - 49) <= 2
        and abs(last.comfort[1] - 59) <= 2
    )
    old = run_population(random.Random(OLD_SEED), n_users=200, prior="self_register")
    return Score(
        knobs=tuple(sorted(knobs.items())),
        exact5=hp.exact_at[5],
        adjacent5=hp.adjacent_at[5],
        confident5=hp.confident_within[5],
        wrong5=hp.confidently_wrong_within[5],
        open_exact5=op.exact_at[5],
        andrew=f"{last.voice_type} {last.top_p:.2f} {last.confidence_label} {last.comfort}",
        andrew_ok=andrew_ok,
        old_ok=old.confident_within_10 >= 0.80 and old.adjacent >= 0.90 and old.exact >= 0.70,
        old=f"conf≤10 {old.confident_within_10:.0%} adj {old.adjacent:.0%} exact {old.exact:.0%}",
    )


def search(n: int, passes: int, workers: int) -> dict[str, float]:
    best = current()
    with ProcessPoolExecutor(max_workers=workers) as ex:
        base = next(iter(ex.map(evaluate, [(best, n)])))
        print("start  " + base.line(), flush=True)
        for p in range(1, passes + 1):
            changed = False
            for name, (_, grid) in KNOBS.items():
                trials = [{**best, name: v} for v in grid]
                scores = list(ex.map(evaluate, [(t, n) for t in trials]))
                for s in scores:
                    print(f"  {name:<18} " + s.line(), flush=True)
                keep = next(s for s in scores if dict(s.knobs)[name] == best[name])
                ok = [s for s in scores if s.passes]
                if ok:
                    top = max(ok, key=lambda s: s.exact5)
                    if top.exact5 > keep.exact5 or not keep.passes:
                        best = dict(top.knobs)
                        changed = changed or dict(top.knobs) != dict(keep.knobs)
            print(f"pass {p}: {best}", flush=True)
            if not changed:
                break
        final = next(iter(ex.map(evaluate, [(best, n)])))
    print("final  " + final.line())
    return best


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=300, help="simulated singers per run")
    ap.add_argument("--passes", type=int, default=3)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args(argv)
    search(args.n, args.passes, args.workers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
