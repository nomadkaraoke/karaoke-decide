#!/usr/bin/env python3
"""Put back voice-pool songs that analyse marked ``failed`` because their ``vocals_gcs`` was gen's
bare object name (``voice-pool/stems/<id>/vocals.flac``) instead of a ``gs://`` URI (v0.28.1 fix).

A ``voice_pool_fetch`` doc is repaired when it is ``failed`` and either its ``vocals_gcs`` is a bare
``voice-pool/stems/...`` name or its ``error`` mentions "no vocals at voice-pool/stems". It goes back
to ``separated`` with ``vocals_gcs`` = ``gs://karaoke-gen-storage-nomadkaraoke/<name>`` and
``error`` None (``attempts`` untouched), but only when that stem object exists.

Writes to prod Firestore, so it needs Andrew's credentials (``/gcp-admin``):

  python scripts/voice/repair_failed_vocals.py --project nomadkaraoke --dry-run
  python scripts/voice/repair_failed_vocals.py --project nomadkaraoke --apply

The next hourly fetch hand-off (or ``gcloud run jobs execute voice-pool-analyse``) analyses them.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from karaoke_decide.voice_pool.queue import FETCH_COLLECTION  # noqa: E402
from karaoke_decide.voice_pool.separate import GEN_BUCKET, gen_uri  # noqa: E402

STEMS_PREFIX = "voice-pool/stems/"
ERROR_HINT = "no vocals at voice-pool/stems"
_STEM_IN_TEXT = re.compile(r"(voice-pool/stems/\S+?\.flac)")


@dataclass
class Change:
    doc_id: str
    old_vocals: Any
    new_vocals: str | None  # None: the stem object is missing, so the doc is left alone
    error: Any


def stem_name(doc_id: str, d: dict[str, Any]) -> str | None:
    """The stem object name to repair ``d`` to, or None when it isn't one of the affected docs."""
    if d.get("status") != "failed":
        return None
    vocals = d.get("vocals_gcs")
    if isinstance(vocals, str) and vocals.strip().lstrip("/").startswith(STEMS_PREFIX):
        return vocals.strip().lstrip("/")
    error = str(d.get("error") or "")
    if ERROR_HINT not in error:
        return None
    if isinstance(vocals, str) and vocals.startswith(f"gs://{GEN_BUCKET}/{STEMS_PREFIX}"):
        return vocals[len(f"gs://{GEN_BUCKET}/") :]
    m = _STEM_IN_TEXT.search(error)
    return m.group(1) if m else f"{STEMS_PREFIX}{doc_id}/vocals.flac"


def plan(db: Any, gcs: Any) -> list[Change]:
    """Every affected doc, with ``new_vocals`` None where the stem object does not exist."""
    bucket = gcs.bucket(GEN_BUCKET)
    out: list[Change] = []
    for snap in db.collection(FETCH_COLLECTION).where("status", "==", "failed").stream():
        d = snap.to_dict() or {}
        name = stem_name(snap.id, d)
        if name is None:
            continue
        exists = bucket.blob(name).exists()
        out.append(Change(snap.id, d.get("vocals_gcs"), gen_uri(name) if exists else None, d.get("error")))
    return out


def apply(db: Any, changes: list[Change], server_timestamp: Any) -> int:
    n = 0
    for c in changes:
        if c.new_vocals is None:
            continue
        db.collection(FETCH_COLLECTION).document(c.doc_id).update(
            {"status": "separated", "vocals_gcs": c.new_vocals, "error": None, "updated_at": server_timestamp}
        )
        n += 1
    return n


def main(argv: list[str] | None = None, *, db: Any = None, gcs: Any = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="print what would change, write nothing")
    mode.add_argument("--apply", action="store_true", help="reset the affected docs to separated")
    ap.add_argument("--project", default="nomadkaraoke")
    args = ap.parse_args(argv)

    server_timestamp: Any = "SERVER_TIMESTAMP"
    if db is None or gcs is None:
        from google.cloud import firestore, storage  # type: ignore[attr-defined]

        db = db or firestore.Client(project=args.project)
        gcs = gcs or storage.Client(project=args.project)
        server_timestamp = firestore.SERVER_TIMESTAMP

    changes = plan(db, gcs)
    for c in changes:
        if c.new_vocals is None:
            print(f"SKIP {c.doc_id}: stem missing ({c.old_vocals!r}); stays failed")
        else:
            print(f"{'RESET' if args.apply else 'WOULD RESET'} {c.doc_id}: failed -> separated, "
                  f"vocals_gcs {c.old_vocals!r} -> {c.new_vocals!r} (error was {str(c.error)[:120]!r})")  # fmt: skip
    ready = sum(c.new_vocals is not None for c in changes)
    if args.apply:
        done = apply(db, changes, server_timestamp)
        print(f"reset {done} docs; skipped {len(changes) - ready} with no stem")
    else:
        print(f"dry run: would reset {ready} docs; {len(changes) - ready} skipped (no stem). Re-run with --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
