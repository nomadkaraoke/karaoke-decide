"""Gate, publish and prune catalog.db on GCS behind a ``catalog/latest.json`` pointer.

Offline job only (runs inside the weekly mb-refresh Cloud Run job). The pointer is written
LAST, so readers never see a pointer whose object is missing, and a gate failure leaves the
previous pointer (and the API's current catalog) untouched.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from karaoke_decide.catalog.bq_source import fetch_catalog_rows
from karaoke_decide.catalog.build import CatalogStats, build_catalog_db
from karaoke_decide.catalog.schema import CATALOG_SCHEMA_VERSION

logger = logging.getLogger(__name__)

CATALOG_BUCKET = "nomadkaraoke-musicbrainz-data"
CATALOG_PREFIX = "catalog"
CATALOG_LATEST = "catalog/latest.json"
KEEP_RUNS = 3
MIN_SONGS_PROD = 100_000
MAX_COVERAGE_DROP = 0.10  # absolute (0.80 -> 0.70 is the limit)
MAX_SONG_DROP = 0.10  # relative to the previous build

_RUN_DIR = re.compile(rf"^{CATALOG_PREFIX}/(\d{{8}}-\d{{6}})/")


def catalog_gate(stats: CatalogStats, prev: dict[str, Any] | None, *, min_songs: int = MIN_SONGS_PROD) -> list[str]:
    """Return human-readable failures; an empty list means the build may be published."""
    failures: list[str] = []
    if stats.songs < min_songs:
        failures.append(f"songs {stats.songs} < minimum {min_songs}")
    if prev:
        prev_songs = int(prev.get("songs") or 0)
        if prev_songs and stats.songs < prev_songs * (1 - MAX_SONG_DROP):
            failures.append(f"songs dropped {prev_songs} -> {stats.songs} (> {MAX_SONG_DROP:.0%})")
        for facet, before in (prev.get("coverage") or {}).items():
            after = stats.coverage.get(facet, 0.0)
            if after < before - MAX_COVERAGE_DROP:
                failures.append(f"coverage[{facet}] dropped {before:.2f} -> {after:.2f}")
    return failures


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _prune(bucket: Any) -> None:
    runs: dict[str, list[Any]] = {}
    for blob in bucket.list_blobs(prefix=f"{CATALOG_PREFIX}/"):
        m = _RUN_DIR.match(blob.name)
        if m:
            runs.setdefault(m.group(1), []).append(blob)
    for run in sorted(runs)[:-KEEP_RUNS]:
        for blob in runs[run]:
            blob.delete()


def publish_catalog(bucket: Any, db_path: Path, stats: CatalogStats, run: str) -> dict[str, Any]:
    """Upload ``catalog.db.gz`` for ``run``, then move the pointer, then prune old runs."""
    path = f"{CATALOG_PREFIX}/{run}/catalog.db.gz"
    with bucket.blob(path).open("wb") as out:
        with gzip.GzipFile(fileobj=out, mode="wb", mtime=0) as gz, db_path.open("rb") as src:
            shutil.copyfileobj(src, gz, 1 << 20)
    gz_bytes = _gz_size(bucket, path)
    pointer: dict[str, Any] = {
        "run": run,
        "path": path,
        "sha256": _sha256(db_path),
        "bytes": db_path.stat().st_size,
        "gz_bytes": gz_bytes,
        "schema": CATALOG_SCHEMA_VERSION,
        "built_at": datetime.now(UTC).isoformat(),
        "coverage": stats.coverage,
        "songs": stats.songs,
    }
    with bucket.blob(CATALOG_LATEST).open("wb") as out:
        out.write(json.dumps(pointer, sort_keys=True).encode())
    try:
        _prune(bucket)
    except Exception:
        # The new catalog is already live; a failed cleanup is retried next run, not page-worthy.
        logger.warning("catalog prune failed after publish (old runs kept)", exc_info=True)
    return pointer


def _gz_size(bucket: Any, path: str) -> int:
    blob = bucket.blob(path)
    blob.reload()
    size = getattr(blob, "size", None)
    if isinstance(size, int):
        return size
    with blob.open("rb") as f:  # fakes without size metadata
        return len(f.read())


def _read_pointer(bucket: Any) -> dict[str, Any] | None:
    blob = bucket.blob(CATALOG_LATEST)
    try:
        if not blob.exists():
            return None
        with blob.open("rb") as f:
            data = json.loads(f.read())
    except (OSError, ValueError):
        logger.warning("Previous catalog pointer unreadable; treating this as a first build")
        return None
    return data if isinstance(data, dict) else None


def build_and_publish_catalog(
    bq: Any, gcs: Any, workdir: Path, *, min_songs: int = MIN_SONGS_PROD, publish: bool = True
) -> dict[str, Any] | None:
    """Fetch, build, gate and publish. Returns the new pointer, or None if the gate refused it.

    ``publish=False`` builds and gates only, leaving ``workdir/catalog.db`` for inspection (size
    checks); it uploads nothing and moves no pointer, and returns None.
    """
    run = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    bucket = gcs.bucket(CATALOG_BUCKET)
    db_path = workdir / "catalog.db"
    stats = build_catalog_db(fetch_catalog_rows(bq), db_path, run)
    logger.info(f"Catalog built: {stats.songs} songs, {stats.bytes} bytes, coverage={stats.coverage}")
    failures = catalog_gate(stats, _read_pointer(bucket), min_songs=min_songs)
    if failures:
        logger.warning("catalog gate failed: " + "; ".join(failures) + " (pointer unchanged)")
        return None
    if not publish:
        logger.info(f"--no-publish: gate passed; {db_path} left in place, nothing uploaded")
        return None
    pointer = publish_catalog(bucket, db_path, stats, run)
    logger.info(f"Catalog published: {pointer['path']} ({pointer['gz_bytes']} gz bytes)")
    return pointer
