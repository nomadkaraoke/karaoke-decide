"""The main API's recommendations catalog: ``catalog.db`` + ``availability.db``.

Loaded once during startup (behind ``GET /api/ready``), the way the search service loads
its index, because Cloud Run throttles CPU once the port is open and a background
download after that can stall:

* ``catalog.db``: ``gs://{catalog_bucket}/catalog/latest.json`` names the run's gzipped
  object and the sha256/bytes of the *uncompressed* db (written by the weekly mb-refresh
  job, ``karaoke_decide/catalog/publish.py``). It is streamed, gunzipped into
  ``{catalog_dir}/<run>/catalog.db`` and verified.
* ``availability.db``: the search service's daily index, through ``index_loader`` (only
  that one file).

``CATALOG_LOCAL_DIR`` / ``SONG_INDEX_LOCAL_DIR`` (already-built files) win when set.

``load`` never raises and never runs past ``catalog_load_timeout_s``: a missing pointer
(before the first build), a corrupt download or a hung GCS read leaves the store
``warming_up`` (v2 answers 503) while health and search keep working. A load that is
still running when the budget runs out keeps going in its daemon thread and makes the
store ready if it finishes.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from backend.search import index_loader
from karaoke_decide.catalog.reader import CatalogReader

if TYPE_CHECKING:
    from karaoke_decide.core.config import Settings

logger = logging.getLogger(__name__)

# Same object names as karaoke_decide/catalog/publish.py (not imported: it pulls in BigQuery).
CATALOG_LATEST = "catalog/latest.json"
AVAILABILITY_FILES = ("availability.db",)
_CHUNK = 1 << 20

CatalogStatus = Literal["ready", "warming_up"]


class CatalogUnavailable(RuntimeError):  # noqa: N818  (name fixed by the plan interface)
    """catalog.db isn't loaded (yet); recommendations v2 answers 503 ``warming_up``."""


class CatalogLoadError(RuntimeError):
    """The published catalog can't be used (no pointer, bad pointer, bad download)."""


def _storage_client() -> Any:
    from google.cloud import storage  # type: ignore[attr-defined]

    return storage.Client()


def _is_not_found(exc: BaseException) -> bool:
    return isinstance(exc, FileNotFoundError) or getattr(exc, "code", None) == 404


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _read_pointer(bucket: Any, bucket_name: str) -> dict[str, Any] | None:
    try:
        raw = bucket.blob(CATALOG_LATEST).download_as_bytes()
    except Exception as exc:
        if _is_not_found(exc):
            logger.warning(
                "No catalog pointer gs://%s/%s yet (first mb-refresh build pending); "
                "recommendations v2 stays warming_up",
                bucket_name,
                CATALOG_LATEST,
            )
            return None
        raise CatalogLoadError(f"can't read gs://{bucket_name}/{CATALOG_LATEST}: {exc}") from exc
    try:
        pointer = json.loads(raw)
    except ValueError as exc:
        raise CatalogLoadError(f"{CATALOG_LATEST} is not JSON: {exc}") from exc
    if not isinstance(pointer, dict) or not all(pointer.get(k) for k in ("run", "path", "sha256", "bytes")):
        raise CatalogLoadError(f"{CATALOG_LATEST} lacks run/path/sha256/bytes: {pointer!r}")
    return pointer


def _download_gunzipped(blob: Any, dest: Path, sha256: str, size: int) -> None:
    """Stream ``blob`` (gzip) into ``dest``, verifying the uncompressed sha256 and size."""
    part = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    written = 0
    try:
        with blob.open("rb") as src, gzip.GzipFile(fileobj=src, mode="rb") as gz, part.open("wb") as out:
            while chunk := gz.read(_CHUNK):
                h.update(chunk)
                out.write(chunk)
                written += len(chunk)
        if written != size or h.hexdigest() != sha256:
            raise CatalogLoadError(
                f"catalog.db: got {written} bytes sha256 {h.hexdigest()}, pointer says {size} bytes sha256 {sha256}"
            )
        os.replace(part, dest)
    except CatalogLoadError:
        part.unlink(missing_ok=True)
        raise
    except Exception as exc:
        part.unlink(missing_ok=True)
        raise CatalogLoadError(f"catalog.db download failed: {exc}") from exc


class CatalogStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reader: CatalogReader | None = None
        self._run: str | None = None

    @property
    def status(self) -> CatalogStatus:
        return "ready" if self._reader is not None else "warming_up"

    @property
    def reader(self) -> CatalogReader | None:
        return self._reader

    @property
    def run(self) -> str | None:
        return self._run

    def close(self) -> None:
        """Close the reader's connections (shutdown); later queries reopen them."""
        if self._reader is not None:
            self._reader.close()

    def require(self) -> CatalogReader:
        reader = self._reader
        if reader is None:
            raise CatalogUnavailable("warming_up")
        return reader

    # ------------------------------------------------------------------ loading

    def load(self, settings: Settings, *, client: Any = None) -> None:
        """Load both databases, within ``settings.catalog_load_timeout_s``. Never raises."""
        timeout = float(settings.catalog_load_timeout_s)
        done = threading.Event()

        def work() -> None:
            try:
                self._load(settings, client)
            except Exception as exc:  # never let startup fail on the catalog
                logger.warning("Recommendations catalog not loaded: %s", exc)
            finally:
                done.set()

        threading.Thread(target=work, name="catalog-load", daemon=True).start()
        if not done.wait(timeout):
            logger.warning(
                "Recommendations catalog still loading after %.1fs; serving warming_up until it finishes", timeout
            )

    def _load(self, settings: Settings, client: Any) -> None:
        started = time.monotonic()
        local_catalog = settings.catalog_local_dir.strip()
        local_index = settings.song_index_local_dir.strip()
        if client is None and not (local_catalog and local_index):
            try:
                client = _storage_client()
            except Exception as exc:
                logger.warning("No GCS client for the recommendations catalog: %s", exc)

        availability: list[Path | None] = [None]
        side = threading.Thread(
            target=lambda: availability.__setitem__(0, self._fetch_availability(settings, client)),
            name="availability-load",
            daemon=True,
        )
        side.start()
        catalog = self._fetch_catalog(settings, client)
        side.join()
        if catalog is None:
            return
        catalog_path, run = catalog
        reader = self._open(catalog_path, availability[0])
        run = run or reader.meta().get("run") or catalog_path.parent.name
        with self._lock:
            self._reader, self._run = reader, run
        logger.info(
            "Recommendations catalog %s loaded in %.1fs (availability: %s)",
            run,
            time.monotonic() - started,
            "yes" if reader.has_availability else "no",
        )

    @staticmethod
    def _open(catalog_path: Path, availability_path: Path | None) -> CatalogReader:
        if availability_path is not None:
            try:
                return CatalogReader(catalog_path, availability_path)
            except Exception as exc:
                logger.warning("availability.db unreadable (%s); recommendations run without it", exc)
        return CatalogReader(catalog_path, None)

    @staticmethod
    def _fetch_catalog(settings: Settings, client: Any) -> tuple[Path, str | None] | None:
        local = settings.catalog_local_dir.strip()
        if local:
            path = Path(local) / "catalog.db"
            if not path.is_file():
                raise CatalogLoadError(f"{path} not found")
            return path, None
        if client is None:
            raise CatalogLoadError("no GCS client")
        bucket = client.bucket(settings.catalog_bucket)
        pointer = _read_pointer(bucket, settings.catalog_bucket)
        if pointer is None:
            return None
        run, sha256, size = str(pointer["run"]), str(pointer["sha256"]), int(pointer["bytes"])
        dest = Path(settings.catalog_dir) / run / "catalog.db"
        if dest.is_file() and dest.stat().st_size == size and _sha256(dest) == sha256:
            return dest, run  # already here (an earlier load in this instance)
        dest.parent.mkdir(parents=True, exist_ok=True)
        _download_gunzipped(bucket.blob(str(pointer["path"])), dest, sha256, size)
        return dest, run

    @staticmethod
    def _fetch_availability(settings: Settings, client: Any) -> Path | None:
        local = settings.song_index_local_dir.strip()
        try:
            if local:
                paths = index_loader.load_local(Path(local), files=AVAILABILITY_FILES)
            else:
                if client is None:
                    raise CatalogLoadError("no GCS client")
                paths = index_loader.load(
                    "",
                    settings.song_index_bucket,
                    Path(settings.catalog_dir) / "song-index",
                    client=client,
                    files=AVAILABILITY_FILES,
                )
        except Exception as exc:
            logger.warning("availability.db not loaded (%s); recommendations run without it", exc)
            return None
        return paths.availability


_store: CatalogStore | None = None
_store_lock = threading.Lock()


def get_catalog_store() -> CatalogStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = CatalogStore()
        return _store
