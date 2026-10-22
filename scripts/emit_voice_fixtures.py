#!/usr/bin/env python
"""Check (and pretty-print) the hand-written song-fit fixture table.

``tests/fixtures/voice/fit_cases.json`` is written BY HAND from the pinned algorithm in
``karaoke_decide/vocal_range/fit.py``; the mobile TS port must reproduce it exactly.
This script only re-checks every case against ``fit_song`` and exits non-zero on any
mismatch. Without ``--check`` it also rewrites the file in canonical pretty-printed form.

    poetry run python scripts/emit_voice_fixtures.py --check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karaoke_decide.vocal_range.fit import SongVocalRange, UserVoiceRange, fit_song  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "voice" / "fit_cases.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify only; do not rewrite the file")
    args = parser.parse_args()

    cases = json.loads(FIXTURE.read_text())
    failures = 0
    for case in cases:
        r = fit_song(UserVoiceRange(**case["user"]), SongVocalRange(**case["song"]))
        got = {
            "bucket": r.bucket,
            "transpose": r.transpose,
            "octave": r.octave,
            "cost": r.cost,
            "score": r.score,
        }
        if got != case["expect"]:
            failures += 1
            print(f"MISMATCH {case['name']}: expected {case['expect']} got {got}")
    if failures:
        print(f"{failures}/{len(cases)} fixture cases mismatch")
        return 1
    if not args.check:
        FIXTURE.write_text(json.dumps(cases, indent=2, ensure_ascii=False) + "\n")
    print(f"ok: {len(cases)} cases match fit_song")
    return 0


if __name__ == "__main__":
    sys.exit(main())
