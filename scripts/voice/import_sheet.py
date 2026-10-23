#!/usr/bin/env python3
"""Import Andrew's song sheet (CSV export) as voice labels.

ADMIN_TOKEN=... python scripts/voice/import_sheet.py --csv sheet.csv --email you@example.com --api https://API_HOST
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from karaoke_decide.voice_session.labels import rows_from_csv  # noqa: E402

BATCH = 200


def post(api: str, token: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"{api.rstrip('/')}/api/admin/voice/labels/import",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-Admin-Token": token},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
        return json.load(resp)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", required=True, type=Path)
    ap.add_argument("--email", required=True)
    ap.add_argument("--api", required=True)
    ap.add_argument("--no-enqueue", action="store_true")
    args = ap.parse_args()
    token = os.environ.get("ADMIN_TOKEN", "")
    if not token:
        print("ADMIN_TOKEN is not set", file=sys.stderr)
        return 2
    rows = [dataclasses.asdict(r) for r in rows_from_csv(args.csv.read_text(encoding="utf-8-sig"))]
    totals = {"imported": 0, "matched": 0, "enqueued": 0}
    unmatched: list[dict] = []
    for i in range(0, len(rows), BATCH):
        res = post(args.api, token, {"email": args.email, "rows": rows[i : i + BATCH], "enqueue": not args.no_enqueue})
        for k in totals:
            totals[k] += res[k]
        unmatched += res["unmatched"]
    print(json.dumps({**totals, "unmatched": len(unmatched)}))
    for u in unmatched:
        print(f"unmatched: {u['artist']} - {u['title']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
