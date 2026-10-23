"""``python -m karaoke_decide.voice_pool <select|fetch|analyse|publish|status>``.

``select --n 1000 [--dry-run] [--no-probe] [--run RUN] [--catalog PATH --availability PATH]``
picks the shared pool (``select.run_select``) and prints its coverage report. catalog.db and
availability.db load like the API's (``CATALOG_LOCAL_DIR`` / ``SONG_INDEX_LOCAL_DIR``, else GCS)
unless both paths are given. ``BIGQUERY_DISABLED=1`` (local stack) is allowed for ``--dry-run``
only: no Spotify ids then, so nothing could be fetched.

A failure logs ONE ERROR and exits 1.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("voice_pool")

CATALOG_WAIT_S = 240.0
PENDING = {"fetch": "Task A4", "status": "Task A4", "analyse": "Task A7", "publish": "Task A7"}


def format_report(summary: dict[str, Any]) -> str:
    lines = [
        f"voice pool selection {summary['run']}{' (dry run)' if summary['dry_run'] else ''}",
        f"  picked {summary['picked']}/{summary['target']} from {summary['candidates']} candidates"
        f"{'  BELOW MINIMUM' if summary['below_minimum'] else ''}",
        f"  probed {summary['probed']}, non-embeddable dropped {summary['non_embeddable']},"
        f" enqueued {summary['enqueued']}",
    ]
    for dim, values in summary["per_dimension"].items():
        cells = ", ".join(f"{v} {actual}/{target}" for v, (target, actual) in values.items())
        lines.append(f"  {dim:<9} {cells}")
    if summary["empty_values"]:
        lines.append(f"  empty: {', '.join(summary['empty_values'])}")
    if summary.get("coverage_uri"):
        lines.append(f"  coverage: {summary['coverage_uri']}")
    return "\n".join(lines)


def _open_reader(args: argparse.Namespace, settings: Any) -> Any:
    from karaoke_decide.catalog.reader import CatalogReader

    if args.catalog or args.availability:
        if not (args.catalog and args.availability):
            raise SystemExit("--catalog and --availability go together")
        return CatalogReader(Path(args.catalog), Path(args.availability))
    from backend.services.catalog_store import get_catalog_store

    store = get_catalog_store()
    store.load(settings.model_copy(update={"catalog_load_timeout_s": CATALOG_WAIT_S}))
    reader = store.reader
    if reader is None or not reader.has_availability:
        raise RuntimeError("catalog.db + availability.db not loaded; nothing to select from")
    return reader


def _select(args: argparse.Namespace) -> int:
    import httpx

    from backend.config import get_backend_settings
    from karaoke_decide.voice_pool.select import run_select

    settings = get_backend_settings()
    if settings.bigquery_disabled and not args.dry_run:
        print("BIGQUERY_DISABLED is set: only --dry-run is possible (no Spotify ids)", file=sys.stderr)
        return 2
    reader = _open_reader(args, settings)
    bq = fs = gcs = None
    if not settings.bigquery_disabled:
        from google.cloud import bigquery

        bq = bigquery.Client(project=settings.google_cloud_project or None)
    if not args.dry_run:
        from google.cloud import storage  # type: ignore[attr-defined]

        from backend.services.firestore_service import FirestoreService

        fs = FirestoreService(settings)
        gcs = storage.Client(project=settings.google_cloud_project or None)
    headers = {"User-Agent": "nomadkaraoke-voice-pool/1.0 (https://nomadkaraoke.com)"}
    with httpx.Client(headers=headers) as http:
        summary = run_select(
            reader, bq, fs, http, n=args.n, run=args.run, dry_run=args.dry_run, gcs=gcs, probe=not args.no_probe
        )
    print(format_report(summary))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m karaoke_decide.voice_pool", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("select", help="Pick the shared pool, probe embeddability, queue it for fetch")
    p.add_argument("--n", type=int, default=1000, help="pool target (default 1000)")
    p.add_argument("--dry-run", action="store_true", help="report only: no queue, BigQuery or GCS writes")
    p.add_argument("--no-probe", action="store_true", help="skip the YouTube oEmbed probe")
    p.add_argument("--run", default=datetime.now(UTC).strftime("%Y%m%d-%H%M%S"), help="run id (default: now, UTC)")
    p.add_argument("--catalog", help="catalog.db path (with --availability)")
    p.add_argument("--availability", help="availability.db path (with --catalog)")
    for name, task in PENDING.items():
        sub.add_parser(name, help=f"not implemented yet ({task})")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd in PENDING:
        print(f"voice_pool {args.cmd}: not implemented yet ({PENDING[args.cmd]})", file=sys.stderr)
        return 2
    from karaoke_decide.etl.refresh_common import configure_logging

    configure_logging()
    try:
        return _select(args)
    except Exception:
        logger.exception("voice pool %s failed", args.cmd)
        return 1
