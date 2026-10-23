#!/usr/bin/env python3
"""Good/no prediction vs a singer's labels on held-out songs (the voice session sign-off gate).

From the API (ADMIN_TOKEN in the env, sent as X-Admin-Token; never printed):
  ADMIN_TOKEN=... python scripts/voice/agreement.py --email you@example.com --api https://API_HOST \
      [--export rows.json] [--weights '{"octave_split": -1.0}']

Offline weight trial against exported rows (no API, no token):
  python scripts/voice/agreement.py --rows rows.json --weights '{"octave_split": -1.0}'

--weights is merged over PREDICT_WEIGHTS. With it, the report is recomputed locally from the
rows; commit a weight change only as a ledger "Ruling:".
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from karaoke_decide.voice_session.predict import PREDICT_WEIGHTS, replay  # noqa: E402


def fetch(api: str, token: str, email: str) -> dict[str, Any]:
    query = urllib.parse.urlencode({"email": email})
    req = urllib.request.Request(
        f"{api.rstrip('/')}/api/admin/voice/agreement?{query}", headers={"X-Admin-Token": token}
    )
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
        body: dict[str, Any] = json.load(resp)
        return body


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--email")
    ap.add_argument("--api")
    ap.add_argument("--rows", type=Path, help="exported rows.json: replay offline, no API")
    ap.add_argument("--export", type=Path, help="write the API's rows here for offline trials")
    ap.add_argument("--weights", help="JSON object merged over PREDICT_WEIGHTS")
    args = ap.parse_args(argv)
    weights = {**PREDICT_WEIGHTS, **(json.loads(args.weights) if args.weights else {})}

    if args.rows:
        report = replay(json.loads(args.rows.read_text()), weights)
    else:
        if not args.email or not args.api:
            ap.error("--email and --api are required unless --rows is given")
        token = os.environ.get("ADMIN_TOKEN", "")
        if not token:
            print("ADMIN_TOKEN is not set", file=sys.stderr)
            return 2
        report = fetch(args.api, token, args.email)
        if args.export:
            args.export.write_text(json.dumps(report.get("rows", []), indent=2))
        if args.weights:
            report = {**replay(report.get("rows", []), weights), "skipped_no_profile": report.get("skipped_no_profile")}
    report.pop("rows", None)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
