"""Load the song index the search service answers from.

The build job (Task A7) publishes, under ``gs://<bucket>/song-index/<run>-<token>/``:
``song_id.db``, ``availability.db``, ``offline.db.gz`` and ``manifest.json``
(``{run, token, schema, normalizer, offline_schema, files: {name: {bytes, sha256}}}``),
plus ``song-index/latest.json`` (``{run, token}``). The service's ``SONG_INDEX_RUN``
env names the ``<run>-<token>`` directory (empty → ``latest.json``).

At startup ``load`` downloads the two server databases, verifies each against the
manifest's sha256, and ``open_index`` opens them read-only. ``load_local`` reads an
already-built directory instead (``SONG_INDEX_LOCAL_DIR``, the local stack).
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from karaoke_decide.song_search.availability import AvailabilityIndex
from karaoke_decide.song_search.identify import SongIdentifier

PREFIX = "song-index"
LATEST = f"{PREFIX}/latest.json"
SERVER_FILES = ("song_id.db", "availability.db")
OFFLINE_FILE = "offline.db.gz"
_HASH_CHUNK = 8 * 1024 * 1024


class IndexLoadError(RuntimeError):
    """The index can't be served (missing, unverifiable or unreadable)."""


@dataclass(frozen=True)
class IndexPaths:
    run: str  # the published directory id, "<run>-<token>" (or the local run id)
    song_id: Path
    availability: Path
    manifest: dict[str, Any] | None  # the run's manifest.json, if there is one


@dataclass(frozen=True)
class IndexInfoData:
    run: str
    built_at: int


@dataclass(frozen=True)
class LoadedIndex:
    paths: IndexPaths
    identifier: SongIdentifier
    availability: AvailabilityIndex
    info: IndexInfoData


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_HASH_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _read_json(bucket: Any, name: str) -> dict[str, Any]:
    try:
        data = json.loads(bucket.blob(name).download_as_bytes())
    except Exception as exc:
        raise IndexLoadError(f"can't read gs://…/{name}: {exc}") from exc
    if not isinstance(data, dict):
        raise IndexLoadError(f"{name} is not a JSON object")
    return data


def _run_id(manifest_or_latest: dict[str, Any]) -> str:
    run, token = manifest_or_latest.get("run"), manifest_or_latest.get("token")
    if not run:
        raise IndexLoadError("no run in manifest")
    return f"{run}-{token}" if token else str(run)


def _download_verified(bucket: Any, name: str, dest: Path, expected: dict[str, Any]) -> None:
    part = dest.with_name(dest.name + ".part")
    try:
        bucket.blob(name).download_to_filename(str(part))
        size, digest = part.stat().st_size, _sha256(part)
        if size != expected.get("bytes") or digest != expected.get("sha256"):
            raise IndexLoadError(
                f"{dest.name}: got {size} bytes sha256 {digest}, manifest says "
                f"{expected.get('bytes')} bytes sha256 {expected.get('sha256')}"
            )
        os.replace(part, dest)
    except IndexLoadError:
        part.unlink(missing_ok=True)
        raise
    except Exception as exc:
        part.unlink(missing_ok=True)
        raise IndexLoadError(f"{dest.name}: download failed: {exc}") from exc


def load(run: str, bucket: str, dest_dir: Path, *, client: Any = None) -> IndexPaths:
    """Download and verify ``song_id.db`` + ``availability.db`` for ``run``
    (``"<run>-<token>"``; empty → ``song-index/latest.json``) into ``dest_dir/<run>``."""
    if client is None:
        from google.cloud import storage

        client = storage.Client()
    b = client.bucket(bucket)
    run = run.strip() or _run_id(_read_json(b, LATEST))
    manifest = _read_json(b, f"{PREFIX}/{run}/manifest.json")
    files = manifest.get("files") or {}
    for name in SERVER_FILES:
        if not isinstance(files.get(name), dict):
            raise IndexLoadError(f"manifest for {run} has no entry for {name}")
    out = Path(dest_dir) / run
    out.mkdir(parents=True, exist_ok=True)
    # Both files at once: cold-start time is mostly this download.
    with ThreadPoolExecutor(max_workers=len(SERVER_FILES)) as pool:
        futures = [
            pool.submit(_download_verified, b, f"{PREFIX}/{run}/{name}", out / name, files[name])
            for name in SERVER_FILES
        ]
        for f in futures:
            f.result()
    return IndexPaths(run=run, song_id=out / "song_id.db", availability=out / "availability.db", manifest=manifest)


def _meta(db: Path) -> dict[str, str]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return dict(conn.execute("SELECT key, value FROM meta").fetchall())
    finally:
        conn.close()


def load_local(local_dir: Path) -> IndexPaths:
    """An already-built index directory (no download, no sha check)."""
    d = Path(local_dir)
    for name in SERVER_FILES:
        if not (d / name).is_file():
            raise IndexLoadError(f"{d / name} not found")
    manifest_path = d / "manifest.json"
    manifest: dict[str, Any] | None = None
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text())
        except ValueError as exc:
            raise IndexLoadError(f"{manifest_path}: {exc}") from exc
        run = _run_id(manifest)
    else:
        try:
            run = _meta(d / "availability.db").get("run") or d.name
        except sqlite3.Error as exc:
            raise IndexLoadError(f"{d / 'availability.db'}: {exc}") from exc
    return IndexPaths(run=run, song_id=d / "song_id.db", availability=d / "availability.db", manifest=manifest)


def open_index(paths: IndexPaths) -> LoadedIndex:
    """Open both databases read-only, failing now (not on the first search) if
    either is unreadable."""
    try:
        meta = _meta(paths.availability)
        _meta(paths.song_id)
        availability = AvailabilityIndex(paths.availability)
    except sqlite3.Error as exc:
        raise IndexLoadError(f"index {paths.run} unreadable: {exc}") from exc
    try:
        built_at = int(meta.get("built_at") or 0)
    except ValueError:
        built_at = 0
    return LoadedIndex(
        paths=paths,
        identifier=SongIdentifier(paths.song_id),
        availability=availability,
        info=IndexInfoData(run=paths.run, built_at=built_at),
    )


def offline_manifest(paths: IndexPaths) -> dict[str, Any] | None:
    """``{run, schema, normalizer, bytes, sha256}`` of the run's offline file, or None."""
    m = paths.manifest
    if not m:
        return None
    entry = (m.get("files") or {}).get(OFFLINE_FILE)
    if not isinstance(entry, dict) or "offline_schema" not in m:
        return None
    return {
        "run": paths.run,
        "schema": int(m["offline_schema"]),
        "normalizer": int(m.get("normalizer", 0)),
        "bytes": int(entry["bytes"]),
        "sha256": str(entry["sha256"]),
    }
