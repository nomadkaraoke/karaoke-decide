"""The main API's voice session data: ``voice.db`` (pool songs, community versions, markers).

Mirrors ``catalog_store``: loaded in the lifespan (after the catalog), never blocking startup
and never raising.

* ``gs://{voice_data_bucket}/voice/latest.json`` = ``{run, sha256, songs, analysed, built_at}``
  (``karaoke_decide/voice_pool/voicedb.py``) names the run; ``voice/<run>/voice.db.gz`` is
  streamed, gunzipped into ``{catalog_dir}/voice/<run>/voice.db`` and checked against the
  pointer's sha256 (of the uncompressed db).
* ``VOICE_LOCAL_DIR`` (an already-built ``voice.db``) wins when set.

Hot refresh: a background asyncio task (``refresh_forever``) calls ``maybe_refresh`` off the
event loop. It re-reads the pointer at most every ``voice_refresh_s``; a new run is downloaded
and verified, then swapped in atomically. A bad download keeps the current reader. While
warming_up the same tick retries the load, so the store recovers without a restart.

Every failure is a WARNING (voice.db is optional for the API; the session answers 503
``warming_up`` without it), so a missing or broken voice.db never pages #alerts.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from backend.services import catalog_store
from karaoke_decide.song_search.thread_conns import ThreadConnections
from karaoke_decide.voice_pool.models import Version
from karaoke_decide.voice_pool.voicedb import ANALYSIS_COLUMNS, VOICE_LATEST, VOICE_PREFIX

if TYPE_CHECKING:
    from karaoke_decide.core.config import Settings

logger = logging.getLogger(__name__)

VOICE_DB = "voice.db"
REFRESH_TICK_S = 60.0  # how often the background task wakes; maybe_refresh applies voice_refresh_s
_CHUNK = 1 << 20
_SQL_CHUNK = 500

VoiceStatus = Literal["ready", "warming_up"]


class VoiceDataUnavailable(RuntimeError):  # noqa: N818  (name fixed by the plan interface)
    """voice.db isn't loaded (yet); the voice session answers 503 ``warming_up``."""


class VoiceLoadError(RuntimeError):
    """The published voice.db can't be used (bad pointer, bad download, no GCS client)."""


@dataclass(frozen=True)
class PoolSong:
    song_key: str
    artist: str
    title: str
    artist_key: str
    stratum: str | None
    source: str | None
    duration_ms: int | None
    popularity: int | None
    lead_gender: str | None


@dataclass(frozen=True)
class SongAnalysis:
    """A voice.db ``analysis`` row (MIDI note numbers and seconds; see voice_analysis.markers)."""

    song_key: str
    analysis_version: int | None
    low: float | None
    high: float | None
    p10: float | None
    p50: float | None
    p90: float | None
    voiced_s: float | None
    secs_above_json: str | None
    secs_below_json: str | None
    octave_split_male: bool | None
    octave_split_female: bool | None
    section_jump: float | None
    sustained_high_count: int | None
    sustained_high_s: float | None
    longest_held_s: float | None
    high_loudness_db: float | None
    syllables_per_s: float | None
    density_source: str | None
    longest_phrase_s: float | None
    intro_s: float | None
    key: int | None
    mode: int | None
    key_changes: int | None
    energy: float | None
    valence: float | None


_POOL_SELECT = (
    "SELECT song_key, artist, title, artist_key, stratum, source, duration_ms, popularity, lead_gender FROM pool"
)
_BOOL_COLUMNS = ("octave_split_male", "octave_split_female")


def _pool_song(row: sqlite3.Row) -> PoolSong:
    return PoolSong(*row)  # _POOL_SELECT lists the columns in PoolSong field order


def _analysis(row: sqlite3.Row) -> SongAnalysis:
    values = {c: row[c] for c in ANALYSIS_COLUMNS}
    for c in _BOOL_COLUMNS:
        if values[c] is not None:
            values[c] = bool(values[c])
    return SongAnalysis(**values)


class VoiceReader:
    """Read-only queries over one ``voice.db``; one connection per thread (like ``CatalogReader``)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._local = threading.local()
        self._conns = ThreadConnections()
        self._generation = 0
        self.conn()  # fail fast on a missing / unreadable file

    def conn(self) -> sqlite3.Connection:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None or getattr(self._local, "gen", -1) != self._generation:
            conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
            try:
                conn.row_factory = sqlite3.Row
                conn.execute("SELECT 1 FROM pool LIMIT 1")
                conn.execute("SELECT 1 FROM analysis LIMIT 1")
            except BaseException:
                conn.close()
                raise
            self._conns.add(conn)
            self._local.conn, self._local.gen = conn, self._generation
        return conn

    def close(self) -> None:
        """Close every thread's connection; later queries reopen."""
        self._generation += 1
        self._conns.close_all()

    def meta(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in self.conn().execute("SELECT key, value FROM meta")}

    def song(self, song_key: str) -> PoolSong | None:
        """Any pool song, analysed or not (``too_little_voice`` songs have no analysis)."""
        row = self.conn().execute(f"{_POOL_SELECT} WHERE song_key = ?", (song_key,)).fetchone()
        return _pool_song(row) if row else None

    def analysis(self, song_key: str) -> SongAnalysis | None:
        row = self.conn().execute("SELECT * FROM analysis WHERE song_key = ?", (song_key,)).fetchone()
        return _analysis(row) if row else None

    def versions(self, song_key: str) -> list[Version]:
        """Community versions by rank; ``embeddable`` as stored (None = unknown)."""
        rows = self.conn().execute(
            "SELECT video_id, brand_code, brand_name, rank, embeddable FROM versions"
            " WHERE song_key = ? ORDER BY rank, video_id",
            (song_key,),
        )
        return [
            Version(
                r["video_id"],
                r["brand_code"],
                r["brand_name"],
                r["rank"],
                None if r["embeddable"] is None else bool(r["embeddable"]),
            )
            for r in rows
        ]

    def pool_keys(self) -> list[str]:
        """Songs the session may offer: those with an ``analysis`` row, by song_key."""
        rows = self.conn().execute(
            "SELECT p.song_key FROM pool p JOIN analysis a ON a.song_key = p.song_key ORDER BY p.song_key"
        )
        return [r[0] for r in rows]

    def pool_songs(self, keys: Sequence[str]) -> list[PoolSong]:
        """The analysed pool songs among ``keys``, in ``keys`` order (others are dropped)."""
        found: dict[str, PoolSong] = {}
        conn = self.conn()
        for i in range(0, len(keys), _SQL_CHUNK):
            chunk = list(keys[i : i + _SQL_CHUNK])
            marks = ",".join("?" * len(chunk))
            sql = f"{_POOL_SELECT} WHERE song_key IN ({marks}) AND song_key IN (SELECT song_key FROM analysis)"
            for r in conn.execute(sql, chunk):
                found[r["song_key"]] = _pool_song(r)
        return [found[k] for k in dict.fromkeys(keys) if k in found]

    def stratum_rows(self) -> list[tuple[str | None, bool]]:
        """``(stratum, analysed)`` per pool song (admin coverage)."""
        rows = self.conn().execute(
            "SELECT p.stratum, a.song_key IS NOT NULL FROM pool p LEFT JOIN analysis a ON a.song_key = p.song_key"
        )
        return [(r[0], bool(r[1])) for r in rows]


# ---------------------------------------------------------------- download


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _read_pointer(bucket: Any, bucket_name: str) -> dict[str, Any] | None:
    try:
        raw = bucket.blob(VOICE_LATEST).download_as_bytes()
    except Exception as exc:
        if catalog_store._is_not_found(exc):
            return None
        raise VoiceLoadError(f"can't read gs://{bucket_name}/{VOICE_LATEST}: {exc}") from exc
    try:
        pointer = json.loads(raw)
    except ValueError as exc:
        raise VoiceLoadError(f"{VOICE_LATEST} is not JSON: {exc}") from exc
    if not isinstance(pointer, dict) or not all(pointer.get(k) for k in ("run", "sha256")):
        raise VoiceLoadError(f"{VOICE_LATEST} lacks run/sha256: {pointer!r}")
    return pointer


def _download_gunzipped(blob: Any, dest: Path, sha256: str) -> None:
    """Stream ``blob`` (gzip) into ``dest``, verifying the uncompressed sha256."""
    part = dest.with_name(f"{dest.name}.{threading.get_ident()}.part")
    h = hashlib.sha256()
    try:
        with blob.open("rb") as src, gzip.GzipFile(fileobj=src, mode="rb") as gz, part.open("wb") as out:
            while chunk := gz.read(_CHUNK):
                h.update(chunk)
                out.write(chunk)
        if h.hexdigest() != sha256:
            raise VoiceLoadError(f"voice.db sha256 {h.hexdigest()} != pointer {sha256}")
        os.replace(part, dest)
    except VoiceLoadError:
        part.unlink(missing_ok=True)
        raise
    except Exception as exc:
        part.unlink(missing_ok=True)
        raise VoiceLoadError(f"voice.db download failed: {exc}") from exc


# ---------------------------------------------------------------- store


class VoiceDataStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()  # guards the swap
        self._refresh_lock = threading.Lock()  # one load / refresh at a time
        self._reader: VoiceReader | None = None
        self._run: str | None = None
        self._retired: VoiceReader | None = None  # the previous run's reader, closed on the next swap
        self._settings: Settings | None = None
        self._client: Any = None
        self._last_check: float | None = None

    @property
    def status(self) -> VoiceStatus:
        return "ready" if self._reader is not None else "warming_up"

    @property
    def run(self) -> str | None:
        return self._run

    @property
    def reader(self) -> VoiceReader | None:
        return self._reader

    def require(self) -> VoiceReader:
        reader = self._reader
        if reader is None:
            raise VoiceDataUnavailable("warming_up")
        return reader

    def close(self) -> None:
        """Close the readers' connections (shutdown); later queries reopen them."""
        for reader in (self._reader, self._retired):
            if reader is not None:
                reader.close()

    # ------------------------------------------------------------ loading

    def load(self, settings: Settings, *, client: Any = None, now: float | None = None) -> None:
        """Load voice.db (local dir, else the published pointer). Never raises."""
        with self._refresh_lock:
            self._settings, self._client = settings, client
            self._last_check = time.monotonic() if now is None else now
            self._try_update()

    def maybe_refresh(self, now: float) -> bool:
        """Re-read the pointer when ``voice_refresh_s`` has passed; True when a new run was swapped in.

        Before ``load`` this does nothing. Never raises.
        """
        if not self._refresh_lock.acquire(blocking=False):
            return False  # a load / refresh is already running
        try:
            if self._settings is None or self._last_check is None:
                return False
            if now - self._last_check < float(self._settings.voice_refresh_s):
                return False
            self._last_check = now
            return self._try_update()
        finally:
            self._refresh_lock.release()

    async def refresh_forever(self) -> None:
        """The lifespan's background task: ``maybe_refresh`` every ``REFRESH_TICK_S``, off the loop."""
        while True:
            await asyncio.sleep(REFRESH_TICK_S)
            try:
                await asyncio.to_thread(self.maybe_refresh, time.monotonic())
            except Exception:  # noqa: BLE001  (maybe_refresh never raises; belt and braces)
                logger.warning("voice.db refresh tick failed", exc_info=True)

    def _try_update(self) -> bool:
        settings = self._settings
        assert settings is not None
        try:
            found = self._fetch(settings)
        except Exception as exc:
            what = f"keeping run {self._run}" if self._reader is not None else "voice session stays warming_up"
            logger.warning("voice.db not loaded (%s); %s", exc, what)
            return False
        if found is None:
            return False
        path, run = found
        try:
            reader = VoiceReader(path)
            run = run or reader.meta().get("run") or path.parent.name
        except Exception as exc:
            logger.warning("voice.db %s unreadable (%s); keeping run %s", path, exc, self._run)
            return False
        self._swap(reader, run)
        return True

    def _fetch(self, settings: Settings) -> tuple[Path, str | None] | None:
        """(path, run) of a voice.db newer than the loaded one; None when there's nothing new."""
        local = settings.voice_local_dir.strip()
        if local:
            if self._reader is not None:
                return None  # a local voice.db is loaded once
            path = Path(local) / VOICE_DB
            if not path.is_file():
                logger.warning("No %s; voice session stays warming_up", path)
                return None
            return path, None
        client = self._client
        if client is None:
            try:
                client = catalog_store._storage_client()
            except Exception as exc:
                raise VoiceLoadError(f"no GCS client: {exc}") from exc
            self._client = client
        bucket = client.bucket(settings.voice_data_bucket)
        pointer = _read_pointer(bucket, settings.voice_data_bucket)
        if pointer is None:
            if self._reader is None:
                logger.warning(
                    "No voice pointer gs://%s/%s yet (voice.db not published); voice session stays warming_up",
                    settings.voice_data_bucket,
                    VOICE_LATEST,
                )
            return None
        run, sha256 = str(pointer["run"]), str(pointer["sha256"])
        if run == self._run:
            return None
        dest = Path(settings.catalog_dir) / VOICE_PREFIX / run / VOICE_DB
        if not (dest.is_file() and _sha256(dest) == sha256):
            dest.parent.mkdir(parents=True, exist_ok=True)
            _download_gunzipped(bucket.blob(f"{VOICE_PREFIX}/{run}/voice.db.gz"), dest, sha256)
        return dest, run

    def _swap(self, reader: VoiceReader, run: str) -> None:
        with self._lock:
            stale, self._retired = self._retired, self._reader
            self._reader, self._run = reader, run
        # The previous reader stays open for queries in flight; the one before it is closed now.
        if stale is not None:
            stale.close()
            self._remove_download(stale.path)
        logger.info("voice.db %s loaded (%d songs offered)", run, len(reader.pool_keys()))

    def _remove_download(self, path: Path) -> None:
        settings = self._settings
        if settings is None:
            return
        root = (Path(settings.catalog_dir) / VOICE_PREFIX).resolve()
        run_dir = path.parent.resolve()
        if run_dir.parent == root:  # only our own downloads, never a local dir
            shutil.rmtree(run_dir, ignore_errors=True)


_store: VoiceDataStore | None = None
_store_lock = threading.Lock()


def get_voice_data_store() -> VoiceDataStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = VoiceDataStore()
        return _store
