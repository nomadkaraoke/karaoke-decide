#!/usr/bin/env python3
"""Put back voice-pool songs the fetch job marked ``failed`` with ``"no spotify id"`` before it
resolved Spotify ids itself (v0.28.3): e.g. Andrew's sheet songs, enqueued without one.

A ``voice_pool_fetch`` doc is requeued when ``status == "failed"`` and ``error == "no spotify id"``:
it goes back to ``queued`` with ``error`` None; ``priority``, ``source`` and ``attempts`` are kept.
The next hourly fetch run resolves its Spotify track (catalog.db + BigQuery) and downloads it;
songs that still have none fail again with one aggregated WARNING.

Writes to prod Firestore, so it needs Andrew's credentials (``/gcp-admin``):

  python scripts/voice/requeue_unresolved.py --project nomadkaraoke --dry-run
  python scripts/voice/requeue_unresolved.py --project nomadkaraoke --apply
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from karaoke_decide.voice_pool.fetch import NO_SPOTIFY_ID  # noqa: E402
from karaoke_decide.voice_pool.queue import FETCH_COLLECTION  # noqa: E402


@dataclass
class Change:
    doc_id: str
    song_key: Any
    source: Any
    priority: Any


def plan(db: Any) -> list[Change]:
    """Every ``failed`` doc whose error is exactly ``"no spotify id"``."""
    out: list[Change] = []
    for snap in db.collection(FETCH_COLLECTION).where("status", "==", "failed").stream():
        d = snap.to_dict() or {}
        if d.get("error") == NO_SPOTIFY_ID:
            out.append(Change(snap.id, d.get("song_key"), d.get("source"), d.get("priority")))
    return out


def apply(db: Any, changes: list[Change], server_timestamp: Any) -> int:
    for c in changes:
        db.collection(FETCH_COLLECTION).document(c.doc_id).update(
            {"status": "queued", "error": None, "updated_at": server_timestamp}
        )
    return len(changes)


def main(argv: list[str] | None = None, *, db: Any = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="print what would change, write nothing")
    mode.add_argument("--apply", action="store_true", help='reset the "no spotify id" failures to queued')
    ap.add_argument("--project", default="nomadkaraoke")
    args = ap.parse_args(argv)

    server_timestamp: Any = "SERVER_TIMESTAMP"
    if db is None:
        from google.cloud import firestore  # type: ignore[attr-defined]

        db = firestore.Client(project=args.project)
        server_timestamp = firestore.SERVER_TIMESTAMP

    changes = plan(db)
    for c in changes:
        print(f"{'REQUEUE' if args.apply else 'WOULD REQUEUE'} {c.doc_id} ({c.song_key}, {c.source}, "
              f"priority {c.priority}): failed -> queued")  # fmt: skip
    by_source = ", ".join(f"{s} {n}" for s, n in Counter(str(c.source) for c in changes).most_common())
    if args.apply:
        print(f"requeued {apply(db, changes, server_timestamp)} docs ({by_source or 'none'})")
    else:
        print(f"dry run: would requeue {len(changes)} docs ({by_source or 'none'}). Re-run with --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
