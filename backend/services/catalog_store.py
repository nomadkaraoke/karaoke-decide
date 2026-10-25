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

``load`` never raises and never runs past ``catalog_load_timeout_s``: a missing pointer,
a corrupt download or a hung GCS read leaves the store ``warming_up`` (v2 answers 503)
while health and search keep working. A load still running when the budget runs out keeps
going in its daemon thread and makes the store ready if it finishes; availability.db that
lands after the catalog is attached then.

Log levels follow the workspace alert rule (ERROR pages #alerts): a missing pointer (before
the first build) and a budget overrun are WARNING; a pointer that exists but whose artifact
is unusable (sha/size mismatch, not gzip, malformed pointer, a non-404 read failure such as
403) and "no GCS client" are ERROR, once per load attempt.

Recovery: ``require()`` while warming_up, when the last attempt ended more than
``RETRY_AFTER_S`` ago and none is running, makes one more attempt. Off the event loop it
runs inside the request (CPU is allocated, and that request can succeed); on the event
loop thread it starts in the background instead of blocking it.
"""

from __future__ import annotations

import asyncio
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
RETRY_AFTER_S = 300.0
_CHUNK = 1 << 20

CatalogStatus = Literal["ready", "warming_up"]


class CatalogUnavailable(RuntimeError):  # noqa: N818  (name fixed by the plan interface)
    """catalog.db isn't loaded (yet); recommendations v2 answers 503 ``warming_up``."""


class CatalogLoadError(RuntimeError):
    """The published catalog exists but can't be used (bad pointer, bad download, no GCS client)."""


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
    # Per-thread part name: an abandoned (hung) attempt and a later retry never share one.
    part = dest.with_name(f"{dest.name}.{threading.get_ident()}.part")
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
        # Retry bookkeeping (guarded by _lock).
        self._settings: Settings | None = None
        self._client: Any = None
        self._attempt_running = False
        self._last_attempt_end: float | None = None
        self._early: threading.Thread | None = None  # start_load's thread (backend.serve)

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
        """The loaded reader, or ``CatalogUnavailable`` (after maybe one retry attempt)."""
        reader = self._reader
        if reader is None:
            self._maybe_retry()
            reader = self._reader
        if reader is None:
            raise CatalogUnavailable("warming_up")
        return reader

    def _maybe_retry(self) -> None:
        with self._lock:
            if self._settings is None or self._attempt_running or self._last_attempt_end is None:
                return
            if time.monotonic() - self._last_attempt_end < RETRY_AFTER_S:
                return
            self._attempt_running = True  # claim: one attempt at a time
            settings, client = self._settings, self._client
        logger.info("Recommendations catalog still warming_up; retrying the load")
        try:
            asyncio.get_running_loop()
        except RuntimeError:  # a worker thread: load inside this request
            self.load(settings, client=client)
        else:  # never block the event loop for the load budget
            threading.Thread(
                target=self.load, args=(settings,), kwargs={"client": client}, name="catalog-retry", daemon=True
            ).start()

    # ------------------------------------------------------------------ loading

    def start_load(self, settings: Settings) -> None:
        """Begin ``load`` in a daemon thread now, before the app is imported (``backend.serve``),
        so the download overlaps the import; the lifespan's ``load_at_startup`` joins it."""
        with self._lock:
            if self._early is not None:
                return
            self._early = threading.Thread(target=self.load, args=(settings,), name="catalog-early-load", daemon=True)
        self._early.start()

    def load_at_startup(self, settings: Settings) -> None:
        """Finish the load ``start_load`` began (``load`` bounds it by its budget), else load now."""
        early = self._early
        if early is None:
            self.load(settings)
            return
        early.join(float(settings.catalog_load_timeout_s) + 1.0)

    def load(self, settings: Settings, *, client: Any = None) -> None:
        """Load both databases, within ``settings.catalog_load_timeout_s``. Never raises."""
        timeout = float(settings.catalog_load_timeout_s)
        # Stop waiting for availability.db a little before the budget, so the catalog can
        # still open (and the store be ready) within it.
        deadline = time.monotonic() + timeout - min(1.0, timeout * 0.2)
        done = threading.Event()
        with self._lock:
            self._settings, self._client, self._attempt_running = settings, client, True

        def work() -> None:
            try:
                self._load(settings, client, deadline)
            except Exception as exc:  # never let startup fail on the catalog; one ERROR per attempt
                logger.error("Recommendations catalog not loaded: %s", exc)
            finally:
                done.set()

        try:
            threading.Thread(target=work, name="catalog-load", daemon=True).start()
            if not done.wait(timeout):
                logger.warning(
                    "Recommendations catalog still loading after %.1fs; serving warming_up until it finishes",
                    timeout,
                )
        finally:
            with self._lock:
                self._attempt_running = False
                self._last_attempt_end = time.monotonic()

    def _load(self, settings: Settings, client: Any, deadline: float) -> None:
        started = time.monotonic()
        local_catalog = settings.catalog_local_dir.strip()
        local_index = settings.song_index_local_dir.strip()
        client_error: str | None = None
        if client is None and not (local_catalog and local_index):
            try:
                client = _storage_client()
            except Exception as exc:
                client_error = str(exc)

        # availability.db downloads alongside the catalog. If it isn't in by the deadline the
        # reader opens without it and the side thread attaches it when it lands.
        handoff: dict[str, Any] = {"availability": None, "late": False, "catalog": None}

        def fetch_availability() -> None:
            path = self._fetch_availability(settings, client, client_error)
            with self._lock:
                if not handoff["late"]:
                    handoff["availability"] = path
                    return
                catalog = handoff["catalog"]
            if path is not None and catalog is not None:
                self._attach_late(catalog, path)

        side = threading.Thread(target=fetch_availability, name="availability-load", daemon=True)
        side.start()
        catalog = self._fetch_catalog(settings, client, client_error)
        side.join(timeout=max(0.0, deadline - time.monotonic()))
        if catalog is None:
            with self._lock:
                handoff["late"] = True
            return
        catalog_path, run = catalog
        with self._lock:
            if side.is_alive():
                logger.warning("availability.db not in yet; recommendations start without it and attach it later")
            reader = self._open(catalog_path, handoff["availability"])
            run = run or reader.meta().get("run") or catalog_path.parent.name
            self._reader, self._run = reader, run
            handoff["late"], handoff["catalog"] = True, catalog_path
        logger.info(
            "Recommendations catalog %s loaded in %.1fs (availability: %s)",
            run,
            time.monotonic() - started,
            "yes" if reader.has_availability else "no",
        )

    def _attach_late(self, catalog_path: Path, availability_path: Path) -> None:
        reader = self._open(catalog_path, availability_path)
        if not reader.has_availability:
            return
        with self._lock:
            current = self._reader
            if current is None or current.catalog_path != catalog_path or current.has_availability:
                reader.close()
                return
            # The previous reader is left open: other threads may be mid-query on it.
            self._reader = reader
        logger.info("availability.db attached to recommendations catalog %s", self._run)

    @staticmethod
    def _open(catalog_path: Path, availability_path: Path | None) -> CatalogReader:
        if availability_path is not None:
            try:
                return CatalogReader(catalog_path, availability_path)
            except Exception as exc:
                logger.warning("availability.db unreadable (%s); recommendations run without it", exc)
        return CatalogReader(catalog_path, None)

    @staticmethod
    def _fetch_catalog(settings: Settings, client: Any, client_error: str | None) -> tuple[Path, str | None] | None:
        local = settings.catalog_local_dir.strip()
        if local:
            path = Path(local) / "catalog.db"
            if not path.is_file():  # local stack before building the fixture: expected
                logger.warning("No %s; recommendations v2 stays warming_up", path)
                return None
            return path, None
        if client is None:
            raise CatalogLoadError(f"no GCS client: {client_error}")
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
    def _fetch_availability(settings: Settings, client: Any, client_error: str | None) -> Path | None:
        local = settings.song_index_local_dir.strip()
        try:
            if local:
                paths = index_loader.load_local(Path(local), files=AVAILABILITY_FILES)
            else:
                if client is None:
                    raise CatalogLoadError(f"no GCS client: {client_error}")
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


def load_catalog_only(settings: Settings, client: Any = None) -> CatalogReader:
    """catalog.db alone (no availability.db, no background threads) for a batch job that only needs
    ``kn_song_map`` lookups, e.g. the voice-pool fetch job (2 GiB). Raises ``CatalogLoadError`` when
    it isn't published, can't be downloaded or opened, or there is no GCS client."""
    client_error: str | None = None
    if client is None and not settings.catalog_local_dir.strip():
        try:
            client = _storage_client()
        except Exception as exc:
            client_error = str(exc)
    fetched = CatalogStore._fetch_catalog(settings, client, client_error)
    if fetched is None:
        raise CatalogLoadError("no published catalog.db")
    try:
        return CatalogReader(fetched[0], None)
    except Exception as exc:  # a corrupt / unreadable catalog.db: the caller treats it as "not loaded"
        raise CatalogLoadError(f"catalog.db could not be opened: {exc}") from exc


def get_catalog_store() -> CatalogStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = CatalogStore()
        return _store
