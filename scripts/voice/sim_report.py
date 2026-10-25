#!/usr/bin/env python3
"""Convergence of the voice session in 3-5 songs: simulated singers, prior × pitch × k.

For each answer style (realistic / model, see ``simulate.AnswerStyle``), prior (self_register / uniform / mic)
and pitch mode (none / headphones / open), n singers (cycling over every type × shift,
``simulate.run_population``) run a session capped at the largest k; each row reports, after k songs, the share
whose top voice type is exact, exact-or-adjacent, who had already stopped confident, and who had stopped
confident with the wrong type. Every combination restarts from the same seed, so rows are reproducible alone.

  poetry run python scripts/voice/sim_report.py                 # seed 20261025, n 300 (about 80 s)
  poetry run python scripts/voice/sim_report.py --n 60 --priors mic --pitches headphones --styles realistic
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path
from typing import get_args

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from karaoke_decide.voice_session.simulate import (  # noqa: E402
    KS,
    AnswerStyle,
    PopulationStats,
    SimPitch,
    SimPrior,
    run_population,
)

SEED = 20261025
N_USERS = 300


Row = tuple[str, str, str, PopulationStats]


def run(seed: int, n: int, priors: list[str], pitches: list[str], styles: list[str] | None = None) -> list[Row]:
    out = []
    for style in styles or ["realistic", "model"]:
        for prior in priors:
            for pitch in pitches:
                stats = run_population(
                    random.Random(seed),
                    n_users=n,
                    prior=prior,  # type: ignore[arg-type]
                    pitch=pitch,  # type: ignore[arg-type]
                    budget=max(KS),
                    answer_style=style,  # type: ignore[arg-type]
                )
                out.append((style, prior, pitch, stats))
    return out


def table(rows: list[Row]) -> str:
    lines = [
        "| answers | prior | pitch | k | exact | adjacent | confident | confidently wrong |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for style, prior, pitch, s in rows:
        for k in KS:
            lines.append(
                f"| {style} | {prior} | {pitch} | {k} | {s.exact_at[k]:.0%} | {s.adjacent_at[k]:.0%} "
                f"| {s.confident_within[k]:.0%} | {s.confidently_wrong_within[k]:.0%} |"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--n", type=int, default=N_USERS, help="simulated singers per prior × pitch")
    ap.add_argument("--priors", nargs="+", default=list(get_args(SimPrior)), choices=get_args(SimPrior))
    ap.add_argument("--pitches", nargs="+", default=list(get_args(SimPitch)), choices=get_args(SimPitch))
    ap.add_argument("--styles", nargs="+", default=["realistic", "model"], choices=get_args(AnswerStyle))
    args = ap.parse_args(argv)
    print(f"seed {args.seed}, n {args.n} per row, session capped at {max(KS)} songs\n")
    print(table(run(args.seed, args.n, args.priors, args.pitches, args.styles)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
