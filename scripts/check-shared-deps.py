#!/usr/bin/env python3
"""Check that karaoke-decide locks every package it shares with karaoke-gen to gen's version.

Both repos install into the one shared `nomadkaraoke` conda env, so a version mismatch means each
`poetry install` breaks the other repo. See karaoke-decide/docs/DEVELOPMENT.md.

Usage: python scripts/check-shared-deps.py [GEN_LOCK] [DECIDE_LOCK]
Defaults: GEN_LOCK = the sibling karaoke-gen main clone in the workspace, DECIDE_LOCK = this repo's
poetry.lock. Exits 1 if any shared package differs.
"""

import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GEN_LOCK = REPO_ROOT.parent / "karaoke-gen" / "poetry.lock"


def locked_versions(path: Path) -> dict[str, str]:
    with path.open("rb") as f:
        return {p["name"].lower(): p["version"] for p in tomllib.load(f)["package"]}


def main() -> int:
    gen_lock = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_GEN_LOCK
    decide_lock = Path(sys.argv[2]) if len(sys.argv) > 2 else REPO_ROOT / "poetry.lock"
    gen, decide = locked_versions(gen_lock), locked_versions(decide_lock)
    shared = sorted(gen.keys() & decide.keys())
    mismatched = [name for name in shared if gen[name] != decide[name]]
    for name in mismatched:
        print(f"{name:40} gen={gen[name]:15} decide={decide[name]}")
    print(f"{len(shared)} shared packages, {len(mismatched)} mismatched")
    return 1 if mismatched else 0


if __name__ == "__main__":
    sys.exit(main())
