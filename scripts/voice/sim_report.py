#!/usr/bin/env python3
"""Convergence of the voice session in 3-5 songs: simulated singers, prior × pitch × k.

For each prior (self_register / uniform / mic) and pitch mode (none / headphones / open), n singers
(cycling over every type × shift, ``simulate.run_population``) run a session capped at the largest k; each
row reports, after k songs, the share whose top voice type is exact, exact-or-adjacent, and who had
already stopped confident. Every combination restarts from the same seed, so rows are reproducible alone.

  poetry run python scripts/voice/sim_report.py                 # seed 20261025, n 300 (about 35 s)
  poetry run python scripts/voice/sim_report.py --n 60 --priors mic --pitches headphones
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path
from typing import get_args

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from karaoke_decide.voice_session.simulate import KS, PopulationStats, SimPitch, SimPrior, run_population  # noqa: E402

SEED = 20261025
N_USERS = 300


def run(seed: int, n: int, priors: list[str], pitches: list[str]) -> list[tuple[str, str, PopulationStats]]:
    out = []
    for prior in priors:
        for pitch in pitches:
            stats = run_population(random.Random(seed), n_users=n, prior=prior, pitch=pitch, budget=max(KS))  # type: ignore[arg-type]
            out.append((prior, pitch, stats))
    return out


def table(rows: list[tuple[str, str, PopulationStats]]) -> str:
    lines = [
        "| prior | pitch | k | exact | adjacent | confident |",
        "|---|---|---|---|---|---|",
    ]
    for prior, pitch, s in rows:
        for k in KS:
            lines.append(
                f"| {prior} | {pitch} | {k} | {s.exact_at[k]:.0%} | {s.adjacent_at[k]:.0%} "
                f"| {s.confident_within[k]:.0%} |"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--n", type=int, default=N_USERS, help="simulated singers per prior × pitch")
    ap.add_argument("--priors", nargs="+", default=list(get_args(SimPrior)), choices=get_args(SimPrior))
    ap.add_argument("--pitches", nargs="+", default=list(get_args(SimPitch)), choices=get_args(SimPitch))
    args = ap.parse_args(argv)
    print(f"seed {args.seed}, n {args.n} per row, session capped at {max(KS)} songs\n")
    print(table(run(args.seed, args.n, args.priors, args.pitches)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
