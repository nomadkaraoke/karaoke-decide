"""song-index-build: build, eval-gate, publish and roll out the song-search indexes.

Runs as the ``song-index-build`` Cloud Run Job (daily, after kn-data-sync). Steps:

1. CHECK     Read ``gs://<source>/song-id/latest.json`` (``{run, shards[]}``, written by
             gen's kn-data-sync) and the published ``song-index/latest.json`` + its
             manifest. Skip when the source run and every schema/normalizer version
             are unchanged (unless ``--force``).
2. BUILD     In ``workdir`` (Cloud Run job disk is in-memory: it counts against the
             job's 8 GiB, so inputs are deleted as soon as they are consumed):
             ``song_id.db`` from the TSV shards, ``availability.db`` from the KN full +
             community exports, ``offline.db`` (gzipped to ``offline.db.gz``).
3. EVAL      ``run_eval`` (cloud matcher, labelled set) and ``dual_eval`` (offline vs
             cloud, labelled + frozen synthetic set). Gates: ``wrong_auto`` may rise by
             at most 1 and the ``auto`` rate drop by at most 2 points vs the previous
             manifest; ``confident_disagree / total`` <= ``DUAL_EVAL_MAX_DISAGREE``
             (both matchers confident on different songs, version/credit variants
             being the same song); ``offline_confident_cloud_unsure_other / total``
             <= ``DUAL_EVAL_MAX_UNSURE_OTHER`` (backstop); offline confident-wrong vs
             the labels at most ``DUAL_EVAL_MAX_OFFLINE_WRONG_EXCESS`` above the cloud's.
             A failed gate publishes nothing and logs one ERROR (the first time
             for that source run + versions; repeats are INFO, recorded in
             ``song-index/reported-failures.json``).
4. PUBLISH   ``song_id.db``, ``availability.db``, then ``manifest.json`` to
             ``gs://<index>/song-index/<run>-<token>/``; ``offline.db.gz`` to the public
             bucket at ``<run>-<token>/offline.db.gz`` (``<token>``: 16 random hex chars,
             the unguessable path).
5. ROLL OUT  Set ``SONG_INDEX_RUN=<run>-<token>`` on Cloud Run service
             ``karaoke-decide-search`` (a new revision; traffic moves only once its
             ``/api/search/ready`` startup probe passes) and wait for it.
             If traffic is pinned to an older revision (an operator rollback), the
             new revision gets none: one ERROR, and steps 6-7 are skipped.
6. POINT     Write ``song-index/latest.json`` = ``{run, token}`` (only after a good
             rollout, so a failed deploy is retried by the next run, not skipped).
7. PRUNE     Keep the new run, every run a revision with traffic serves, plus the
             previous ``KEEP_PREVIOUS_RUNS`` in both buckets.

Usage:
    python -m karaoke_decide.etl.song_index_build run [--force] [--no-deploy]
    python -m karaoke_decide.etl.song_index_build run --dry-run [--workdir DIR]
        [--songs TSV ...] [--kn-full JSON] [--kn-community JSON]   # local build, no GCP

Never run without ``--dry-run`` from a laptop: the indexes are built in GCP.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import logging
import os
import resource
import secrets
import shutil
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from karaoke_decide.etl import refresh_common as common
from karaoke_decide.song_search import availability as song_availability
from karaoke_decide.song_search import build_song_id as song_id_builder
from karaoke_decide.song_search import eval as song_eval
from karaoke_decide.song_search import offline as song_offline
from karaoke_decide.song_search.identify import SongIdentifier
from karaoke_decide.song_search.text_normalize import NORMALIZER_VERSION, SEARCH_INDEX_VERSION

logger = logging.getLogger("song_index_build")

PROJECT_ID = os.environ.get("GCP_PROJECT", "nomadkaraoke")
REGION = os.environ.get("SEARCH_SERVICE_REGION", "us-central1")
SEARCH_SERVICE = os.environ.get("SEARCH_SERVICE", "karaoke-decide-search")
INDEX_RUN_ENV = "SONG_INDEX_RUN"

# Source exports (gen kn-data-sync) and the server-side index share the kn-data bucket.
SOURCE_BUCKET = os.environ.get("KN_DATA_BUCKET", "nomadkaraoke-kn-data")
INDEX_BUCKET = os.environ.get("SONG_INDEX_BUCKET", "nomadkaraoke-kn-data")
# kn-data has uniform bucket-level access (no per-object public ACL), so the phone's
# file goes to a dedicated bucket whose public get-only access is set in Pulumi.
PUBLIC_BUCKET = os.environ.get("SONG_INDEX_PUBLIC_BUCKET", "nomadkaraoke-song-index-public")

SONG_ID_LATEST = "song-id/latest.json"
KN_FULL = "full/full-data-latest.json.gz"
KN_COMMUNITY = "community/community-data-latest.json.gz"

# Layout read by backend/search/index_loader.py (kept equal by a test).
INDEX_PREFIX = "song-index"
INDEX_LATEST = f"{INDEX_PREFIX}/latest.json"
# Failures already alerted on (key -> first seen), so a repeat logs INFO, not a daily ERROR.
REPORTED_FAILURES = f"{INDEX_PREFIX}/reported-failures.json"
REPORTED_FAILURES_KEEP = 20
SERVER_FILES = ("song_id.db", "availability.db")
OFFLINE_FILE = "offline.db.gz"

KEEP_PREVIOUS_RUNS = 3
EVAL_MAX_WRONG_AUTO_RISE = 1
EVAL_MAX_AUTO_RATE_DROP = 2.0  # percentage points
# Max share of dual-eval queries where offline and the cloud are both confident about
# different songs (``identify.same_song``: "Song (live)" / "A & B" vs "A feat. B" are the
# same song). Offline being sure where the cloud is unsure is reported, not gated.
DUAL_EVAL_MAX_DISAGREE = 0.01
# Offline may auto-apply a wrong song (run_eval's confident-WRONG, negatives included) on
# at most this many more dual-eval cases than the cloud does (absolute).
DUAL_EVAL_MAX_OFFLINE_WRONG_EXCESS = 2
# Backstop: max share of dual-eval queries where offline is confident but the cloud is
# unsure and its best guess (if any) is another song. Loose on purpose (prod A10: 9/1023
# = 0.9%); it catches offline turning confident wholesale on queries the cloud can't place.
DUAL_EVAL_MAX_UNSURE_OTHER = 0.02
DEPLOY_TIMEOUT_S = 900

SCHEMA_VERSION = song_id_builder.SCHEMA_VERSION
OFFLINE_SCHEMA_VERSION = song_offline.OFFLINE_SCHEMA_VERSION
# The manifest keys that, with the source run, decide whether a rebuild is needed.
VERSIONS: dict[str, Any] = {
    "schema": SCHEMA_VERSION,
    "normalizer": NORMALIZER_VERSION,
    "offline_schema": OFFLINE_SCHEMA_VERSION,
    "availability_schema": song_availability.AVAILABILITY_SCHEMA_VERSION,
    "search_index": SEARCH_INDEX_VERSION,
}

# Eval sets ship inside the package: the image copies karaoke_decide/ but not tests/.
# They are byte-identical copies of tests/fixtures/song_search/*.jsonl (a test pins it).
_CASES_DIR = Path(song_eval.__file__).resolve().parent / "eval_cases"
EVAL_CASES = _CASES_DIR / "song_id_eval.jsonl"
DUAL_EVAL_EXTRA_CASES = _CASES_DIR / "song_id_synthetic.jsonl"
_REPO_FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "song_search"

_CT_SQLITE = "application/vnd.sqlite3"
_CT_JSON = "application/json"
_CT_GZIP = "application/gzip"
_PUBLIC_CACHE = "public, max-age=31536000, immutable"  # the path is unique per run
_HASH_CHUNK = 8 * 1024 * 1024


class SourceError(RuntimeError):
    """The source exports can't be read."""


# --------------------------------------------------------------------------
# Storage + Cloud Run adapters
# --------------------------------------------------------------------------


class Storage(Protocol):
    def read_bytes(self, bucket: str, name: str) -> bytes | None: ...
    def download(self, bucket: str, name: str, dest: Path) -> None: ...
    def upload_file(
        self, bucket: str, name: str, src: Path, *, content_type: str, cache_control: str | None = None
    ) -> None: ...
    def upload_bytes(self, bucket: str, name: str, data: bytes, *, content_type: str) -> None: ...
    def list_names(self, bucket: str, prefix: str) -> list[str]: ...
    def delete(self, bucket: str, name: str) -> None: ...


@dataclass(frozen=True)
class Rollout:
    """Where the service's traffic went after an env update."""

    serving: bool  # 100% of traffic follows the latest revision (the new one)
    pinned: list[str] = field(default_factory=list)  # revisions holding traffic otherwise


class RunAdmin(Protocol):
    def set_env(self, name: str, value: str) -> Rollout: ...
    def serving_env_values(self, name: str) -> set[str]: ...


class GcsStore:
    """``Storage`` over a ``google.cloud.storage.Client``. Uploads retry transient
    errors (the client's DEFAULT_RETRY; every write here is safe to repeat)."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def read_bytes(self, bucket: str, name: str) -> bytes | None:
        from google.api_core.exceptions import NotFound

        try:
            return bytes(self.client.bucket(bucket).blob(name).download_as_bytes())
        except NotFound:
            return None

    def download(self, bucket: str, name: str, dest: Path) -> None:
        self.client.bucket(bucket).blob(name).download_to_filename(str(dest))

    def upload_file(
        self, bucket: str, name: str, src: Path, *, content_type: str, cache_control: str | None = None
    ) -> None:
        from google.cloud.storage.retry import DEFAULT_RETRY

        blob = self.client.bucket(bucket).blob(name)
        if cache_control:
            blob.cache_control = cache_control
        blob.upload_from_filename(str(src), content_type=content_type, retry=DEFAULT_RETRY)

    def upload_bytes(self, bucket: str, name: str, data: bytes, *, content_type: str) -> None:
        from google.cloud.storage.retry import DEFAULT_RETRY

        self.client.bucket(bucket).blob(name).upload_from_string(data, content_type=content_type, retry=DEFAULT_RETRY)

    def list_names(self, bucket: str, prefix: str) -> list[str]:
        return [b.name for b in self.client.list_blobs(bucket, prefix=prefix)]

    def delete(self, bucket: str, name: str) -> None:
        from google.api_core.exceptions import NotFound

        try:
            self.client.bucket(bucket).blob(name).delete()
        except NotFound:
            pass


def _main_container(containers: Any) -> Any:
    """The serving container: the only one, else the one that declares ports."""
    if len(containers) == 1:
        return containers[0]
    serving = [c for c in containers if c.ports]
    if len(serving) != 1:
        raise RuntimeError(f"can't tell the serving container among {[c.name for c in containers]}")
    return serving[0]


class CloudRunAdmin:
    """Sets one env var on a Cloud Run service (a new revision) and waits for it to
    become ready. Cloud Run keeps serving the old revision if the new one fails its
    startup probe; the operation then raises. Traffic pinned to a named revision (an
    operator rollback) is reported as ``Rollout(serving=False)``: the new revision
    exists but gets no traffic."""

    def __init__(self, service_path: str, client: Any = None, revisions_client: Any = None) -> None:
        self.service_path = service_path
        self._client = client
        self._revisions = revisions_client

    @property
    def client(self) -> Any:
        if self._client is None:
            from google.cloud import run_v2

            self._client = run_v2.ServicesClient()
        return self._client

    @property
    def revisions(self) -> Any:
        if self._revisions is None:
            from google.cloud import run_v2

            self._revisions = run_v2.RevisionsClient()
        return self._revisions

    def set_env(self, name: str, value: str) -> Rollout:
        from google.cloud import run_v2

        svc = self.client.get_service(name=self.service_path)
        container = _main_container(svc.template.containers)
        for env in container.env:
            if env.name == name:
                env.value = value
                break
        else:
            container.env.append(run_v2.EnvVar(name=name, value=value))
        svc.template.revision = ""  # let Cloud Run name the new revision
        logger.info(f"Rolling {self.service_path} to {name}={value}")
        updated = self.client.update_service(service=svc).result(timeout=DEPLOY_TIMEOUT_S)
        latest = run_v2.TrafficTargetAllocationType.TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST
        on_latest = sum(t.percent for t in updated.traffic if t.type_ == latest)
        if on_latest >= 100:
            logger.info(f"{self.service_path} is serving {name}={value}")
            return Rollout(serving=True)
        pinned = [t.revision for t in updated.traffic if t.percent > 0 and t.type_ != latest and t.revision]
        return Rollout(serving=False, pinned=pinned)

    def _revision_path(self, revision: str) -> str:
        return revision if "/" in revision else f"{self.service_path}/revisions/{revision}"

    def serving_env_values(self, name: str) -> set[str]:
        """``name``'s value on every revision currently receiving traffic."""
        from google.cloud import run_v2

        svc = self.client.get_service(name=self.service_path)
        latest = run_v2.TrafficTargetAllocationType.TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST
        revisions: set[str] = set()
        for t in svc.traffic_statuses:
            if t.percent > 0:
                rev = t.revision or (svc.latest_ready_revision if t.type_ == latest else "")
                if not rev:
                    raise RuntimeError(f"traffic status without a revision on {self.service_path}")
                revisions.add(self._revision_path(rev))
        values: set[str] = set()
        for rev in sorted(revisions):
            container = _main_container(self.revisions.get_revision(name=rev).containers)
            values.update(e.value for e in container.env if e.name == name and e.value)
        return values


def _storage_client() -> Any:
    from google.cloud import storage  # type: ignore[attr-defined]

    return storage.Client(project=PROJECT_ID)


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceFiles:
    shards: list[Path]
    kn_full: Path
    kn_community: Path


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_HASH_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _peak_rss_mib() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024  # bytes on macOS, KiB on Linux


def _dir_mib(path: Path) -> float:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / (1024 * 1024)


class _Stats:
    """Per-stage wall time, peak RSS and workdir size, logged at INFO for job sizing."""

    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir
        self.seconds: dict[str, float] = {}
        self.peak_workdir_mib = 0.0

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        t0 = time.monotonic()
        yield
        self.seconds[name] = round(time.monotonic() - t0, 2)
        size = _dir_mib(self.workdir)
        self.peak_workdir_mib = max(self.peak_workdir_mib, size)
        logger.info(
            f"Stage {name}: {self.seconds[name]:.1f}s, peak RSS {_peak_rss_mib():.0f} MiB, workdir {size:.0f} MiB"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "seconds": self.seconds,
            "peak_rss_mib": round(_peak_rss_mib(), 1),
            "peak_workdir_mib": round(self.peak_workdir_mib, 1),
        }


def load_eval_cases() -> list[dict[str, Any]]:
    return song_eval.load_cases(EVAL_CASES)


def load_dual_eval_cases() -> list[dict[str, Any]]:
    """The extra (frozen synthetic) cases; dual eval runs these plus the eval set."""
    return song_eval.load_cases(DUAL_EVAL_EXTRA_CASES)


def _gzip_file(src: Path, dst: Path) -> None:
    with open(src, "rb") as f, open(dst, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as g:
        shutil.copyfileobj(f, g, _HASH_CHUNK)


def _brief(result: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in result.items() if not isinstance(v, list)}


# How many dual-eval disagreements are logged and kept in the manifest.
DUAL_EVAL_DISAGREEMENTS_CAP = 100


def cap_disagreements(
    disagreements: list[dict[str, Any]], cap: int = DUAL_EVAL_DISAGREEMENTS_CAP
) -> list[dict[str, Any]]:
    """First ``cap`` disagreements in ``song_eval.DISAGREEMENT_ORDER`` (the gated
    ``confident_disagree`` first; stable within a type; unknown types last).
    The ``q`` strings are eval-set test queries, not user data, so they are safe to log."""
    order = song_eval.DISAGREEMENT_ORDER

    def rank(d: dict[str, Any]) -> int:
        t = d.get("type")
        return order.index(t) if t in order else len(order)

    return sorted(disagreements, key=rank)[:cap]


def cap_wrong_auto_cases(cases: list[dict[str, Any]], cap: int = DUAL_EVAL_DISAGREEMENTS_CAP) -> list[dict[str, Any]]:
    """First ``cap`` label misses, offline-wrong ones first (what the label gate is about)."""
    return sorted(cases, key=lambda w: not w.get("offline_wrong"))[:cap]


def build_local(src: SourceFiles, workdir: Path, run: str, token: str, stats: _Stats | None = None) -> dict[str, Any]:
    """Build the three indexes into ``workdir`` from local inputs (deleted once used),
    evaluate them, and return the manifest (``files`` hashed from the built files).
    ``stats`` carries earlier stages (the download) into ``manifest.build``."""
    stats = stats or _Stats(workdir)
    song_id_db = workdir / "song_id.db"
    availability_db = workdir / "availability.db"
    offline_db = workdir / "offline.db"
    offline_gz = workdir / OFFLINE_FILE
    counts: dict[str, Any] = {}

    with stats.stage("song_id"):
        counts["song_id"] = song_id_builder.build(src.shards, song_id_db, run)
    for shard in src.shards:
        shard.unlink(missing_ok=True)
    with stats.stage("availability"):
        counts["availability"] = song_availability.build_availability_db(
            src.kn_full, src.kn_community, song_id_db, availability_db, run
        )
    src.kn_full.unlink(missing_ok=True)
    src.kn_community.unlink(missing_ok=True)
    with stats.stage("offline"):
        counts["offline"] = song_offline.build_offline_db(song_id_db, availability_db, offline_db, run)
        _gzip_file(offline_db, offline_gz)

    cases = load_eval_cases()
    cloud = SongIdentifier(song_id_db)
    offline = SongIdentifier(offline_db, margin_bonus=song_offline.OFFLINE_MARGIN_BONUS)
    try:
        with stats.stage("eval"):
            ev = song_eval.run_eval(cloud, cases)
        with stats.stage("dual_eval"):
            dual = song_eval.dual_eval(cloud, offline, cases + load_dual_eval_cases())
    finally:
        cloud.close()
        offline.close()
    offline_db.unlink(missing_ok=True)  # only the .gz is published
    all_disagreements = dual.get("disagreements", [])
    dual = {
        **dual,
        "disagreements": cap_disagreements(all_disagreements),
        "wrong_auto_cases": cap_wrong_auto_cases(dual.get("wrong_auto_cases", [])),
        "disagree_rate": round(dual["confident_disagree"] / dual["total"], 6) if dual["total"] else 0.0,
        "max_disagree": DUAL_EVAL_MAX_DISAGREE,
    }
    logger.info(f"Eval result: {json.dumps(_brief(ev))}")
    logger.info(f"Dual eval result: {json.dumps(_brief(dual))}")
    logger.info(
        f"Dual eval disagreements (first {len(dual['disagreements'])} of "
        f"{len(all_disagreements)}, confident_disagree first): "
        f"{json.dumps(dual['disagreements'], ensure_ascii=False)}",
        extra={"json_fields": {"dual_eval_disagreements": dual["disagreements"]}},
    )

    index_run = f"{run}-{token}"
    files: dict[str, dict[str, Any]] = {
        name: {"bytes": (workdir / name).stat().st_size, "sha256": _sha256(workdir / name)} for name in SERVER_FILES
    }
    files[OFFLINE_FILE] = {
        "bytes": offline_gz.stat().st_size,
        "sha256": _sha256(offline_gz),
        "bucket": PUBLIC_BUCKET,
        "path": f"{index_run}/{OFFLINE_FILE}",
    }
    return {
        "run": run,
        "token": token,
        **VERSIONS,
        "built_at": int(time.time()),
        "files": files,
        "eval": ev,
        "dual_eval": dual,
        "counts": counts,
        "build": stats.as_dict(),
    }


def gate_failures(ev: dict[str, Any], dual: dict[str, Any], prev_eval: dict[str, Any] | None) -> list[str]:
    """Why this build must not be published ([] = publish)."""
    failures = []
    if prev_eval:
        rise = ev["wrong_auto"] - prev_eval.get("wrong_auto", 0)
        if rise > EVAL_MAX_WRONG_AUTO_RISE:
            failures.append(
                f"eval wrong_auto {ev['wrong_auto']} vs previous {prev_eval.get('wrong_auto', 0)} "
                f"(max rise {EVAL_MAX_WRONG_AUTO_RISE})"
            )
        if ev["total"] and prev_eval.get("total"):
            rate = 100.0 * ev["auto"] / ev["total"]
            prev_rate = 100.0 * prev_eval["auto"] / prev_eval["total"]
            if prev_rate - rate > EVAL_MAX_AUTO_RATE_DROP:
                failures.append(
                    f"eval auto rate {rate:.1f}% vs previous {prev_rate:.1f}% "
                    f"(max drop {EVAL_MAX_AUTO_RATE_DROP:g} points)"
                )
    total = dual["total"]
    rate = dual["confident_disagree"] / total if total else 0.0
    if rate > DUAL_EVAL_MAX_DISAGREE:
        failures.append(
            f"dual_eval confident_disagree {dual['confident_disagree']}/{total} = {rate:.2%} "
            f"(max {DUAL_EVAL_MAX_DISAGREE:.2%})"
        )
    unsure_other = dual.get("offline_confident_cloud_unsure_other", 0)
    if total and unsure_other / total > DUAL_EVAL_MAX_UNSURE_OTHER:
        failures.append(
            f"dual_eval offline_confident_cloud_unsure_other {unsure_other}/{total} = "
            f"{unsure_other / total:.2%} (max {DUAL_EVAL_MAX_UNSURE_OTHER:.2%})"
        )
    offline_wrong, cloud_wrong = dual.get("offline_wrong_auto", 0), dual.get("cloud_wrong_auto", 0)
    if offline_wrong > cloud_wrong + DUAL_EVAL_MAX_OFFLINE_WRONG_EXCESS:
        failures.append(
            f"dual_eval offline_wrong_auto {offline_wrong} vs cloud_wrong_auto {cloud_wrong} "
            f"of {dual.get('labelled', total)} cases (max excess {DUAL_EVAL_MAX_OFFLINE_WRONG_EXCESS})"
        )
    return failures


# --------------------------------------------------------------------------
# Publish
# --------------------------------------------------------------------------


def _read_json(storage: Storage, bucket: str, name: str) -> dict[str, Any] | None:
    data = storage.read_bytes(bucket, name)
    if data is None:
        return None
    parsed = json.loads(data)
    return parsed if isinstance(parsed, dict) else None


def _previous(storage: Storage) -> tuple[str | None, dict[str, Any] | None]:
    """(published "<run>-<token>", its manifest) or (None, None) on the first run."""
    latest = _read_json(storage, INDEX_BUCKET, INDEX_LATEST)
    if not latest or not latest.get("run"):
        logger.info("No published song index yet (first run)")
        return None, None
    index_run = f"{latest['run']}-{latest['token']}" if latest.get("token") else str(latest["run"])
    try:
        manifest = _read_json(storage, INDEX_BUCKET, f"{INDEX_PREFIX}/{index_run}/manifest.json")
    except ValueError:
        manifest = None
    if manifest is None:
        logger.warning(f"Published index {index_run} has no readable manifest; eval gate has no baseline")
    return index_run, manifest


def _up_to_date(prev: dict[str, Any] | None, run: str) -> bool:
    return prev is not None and prev.get("run") == run and all(prev.get(k) == v for k, v in VERSIONS.items())


def _fetch_sources(storage: Storage, inputs: Path) -> tuple[str, SourceFiles]:
    try:
        latest = _read_json(storage, SOURCE_BUCKET, SONG_ID_LATEST)
    except ValueError as exc:
        raise SourceError(f"gs://{SOURCE_BUCKET}/{SONG_ID_LATEST} is not JSON: {exc}") from exc
    if not latest or not latest.get("run") or not latest.get("shards"):
        raise SourceError(f"gs://{SOURCE_BUCKET}/{SONG_ID_LATEST} is missing or has no run/shards")
    inputs.mkdir(parents=True, exist_ok=True)
    shards = []
    for i, uri in enumerate(latest["shards"]):
        bucket, _, name = str(uri).removeprefix("gs://").partition("/")
        dest = inputs / f"{i:05d}-{Path(name).name}"
        storage.download(bucket, name, dest)
        shards.append(dest)
    full, community = inputs / "full.json.gz", inputs / "community.json.gz"
    storage.download(SOURCE_BUCKET, KN_FULL, full)
    storage.download(SOURCE_BUCKET, KN_COMMUNITY, community)
    return str(latest["run"]), SourceFiles(shards, full, community)


def _source_run(storage: Storage) -> str:
    latest = _read_json(storage, SOURCE_BUCKET, SONG_ID_LATEST)
    if not latest or not latest.get("run"):
        raise SourceError(f"gs://{SOURCE_BUCKET}/{SONG_ID_LATEST} is missing or has no run")
    return str(latest["run"])


def _gate_message(run: str, failures: list[str], serving: str | None) -> str:
    return (
        f"song-index-build: index for source run {run} not published (eval gate failed: "
        f"{'; '.join(failures)}). Search keeps serving {serving or 'its current index'}. "
        "Details: the 'Eval result' / 'Dual eval result' INFO lines of this song-index-build execution."
    )


def _report(storage: Storage | None, key: str, message: str) -> bool:
    """Log ``message`` as the run's one ERROR the first time ``key`` fails; INFO on
    repeats (recorded in ``REPORTED_FAILURES``), so one root cause pages once.
    Returns True when this call was the first report (the task should exit non-zero)."""
    state: dict[str, Any] = {}
    if storage is not None:
        try:
            state = _read_json(storage, INDEX_BUCKET, REPORTED_FAILURES) or {}
        except Exception as exc:  # noqa: BLE001 - alerting must not depend on it
            logger.warning(f"Can't read gs://{INDEX_BUCKET}/{REPORTED_FAILURES}: {exc}")
    if key in state:
        logger.info(f"Already reported {state[key]}: {message}")
        return False
    logger.error(message)
    if storage is None:
        return True
    state[key] = datetime.now(UTC).isoformat(timespec="seconds")
    state = dict(sorted(state.items(), key=lambda kv: str(kv[1]))[-REPORTED_FAILURES_KEEP:])
    try:
        storage.upload_bytes(INDEX_BUCKET, REPORTED_FAILURES, json.dumps(state).encode(), content_type=_CT_JSON)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Can't record the failure in gs://{INDEX_BUCKET}/{REPORTED_FAILURES}: {exc}")
    return True


def _prune(storage: Storage, keep: set[str]) -> None:
    """Keep every run in ``keep`` (the new one + those live revisions serve) plus the
    ``KEEP_PREVIOUS_RUNS`` newest other runs, in both buckets. Best effort: a failure
    is a WARNING (the next run prunes again)."""
    targets = [
        (INDEX_BUCKET, f"{INDEX_PREFIX}/", 1),
        (PUBLIC_BUCKET, "", 0),
    ]
    for bucket, prefix, depth in targets:
        try:
            names = storage.list_names(bucket, prefix)
            runs = sorted({n.split("/")[depth] for n in names if n.count("/") > depth})
            others = [r for r in runs if r not in keep]
            doomed = set(others[: max(0, len(others) - KEEP_PREVIOUS_RUNS)])
            for n in names:
                if n.count("/") > depth and n.split("/")[depth] in doomed:
                    storage.delete(bucket, n)
            if doomed:
                logger.info(f"Pruned {len(doomed)} old run(s) from gs://{bucket}/{prefix}: {sorted(doomed)}")
        except Exception as exc:  # noqa: BLE001 - best effort
            logger.warning(f"Pruning gs://{bucket}/{prefix} failed (retried next run): {exc}")


def build_and_publish(
    *, storage: Storage, run_admin: RunAdmin, workdir: Path, force: bool = False, deploy: bool = True
) -> dict[str, Any]:
    """The whole job. → ``{skipped: True, ...}``, ``{published: False, failures, ...}``
    (gate failed: nothing uploaded) or ``{published: True, run, token, index_run, ...}``.
    Raises on source, build, upload or rollout errors (latest.json then still names the
    previous index, so the next run retries).

    ``deploy=False`` (``--no-deploy``) is the first-rollout bootstrap: publish and point
    ``latest.json`` at the new index without touching the search service (which can't
    exist yet: its startup probe needs an index). No pruning: nothing to prune yet."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    run = _source_run(storage)
    serving, prev = _previous(storage)
    if not force and _up_to_date(prev, run):
        logger.info(f"Source run {run} already published as {serving} with the same versions; skipping")
        return {"skipped": True, "run": run, "index_run": serving}

    t0 = time.monotonic()
    token = secrets.token_hex(8)
    index_run = f"{run}-{token}"
    logger.info(f"Building song index {index_run} (previous: {serving or 'none'}, force={force})")
    stats = _Stats(workdir)
    with stats.stage("download"):
        run, src = _fetch_sources(storage, workdir / "inputs")
        index_run = f"{run}-{token}"  # the source may have moved on since the check
    manifest = build_local(src, workdir, run, token, stats)

    failures = gate_failures(manifest["eval"], manifest["dual_eval"], (prev or {}).get("eval"))
    if failures:
        key = f"gate:{run}:{json.dumps(VERSIONS, sort_keys=True)}"
        first = _report(storage, key, _gate_message(run, failures, serving))
        return {"published": False, "run": run, "token": token, "failures": failures, "already_reported": not first}

    run_dir = f"{INDEX_PREFIX}/{index_run}"
    for name in SERVER_FILES:
        storage.upload_file(INDEX_BUCKET, f"{run_dir}/{name}", workdir / name, content_type=_CT_SQLITE)
    storage.upload_file(
        PUBLIC_BUCKET,
        manifest["files"][OFFLINE_FILE]["path"],
        workdir / OFFLINE_FILE,
        content_type=_CT_GZIP,
        cache_control=_PUBLIC_CACHE,
    )
    storage.upload_bytes(
        INDEX_BUCKET, f"{run_dir}/manifest.json", json.dumps(manifest, indent=1).encode(), content_type=_CT_JSON
    )
    logger.info(f"Uploaded {index_run} (offline.db.gz {manifest['files'][OFFLINE_FILE]['bytes']:,} bytes)")

    if not deploy:
        storage.upload_bytes(
            INDEX_BUCKET, INDEX_LATEST, json.dumps({"run": run, "token": token}).encode(), content_type=_CT_JSON
        )
        logger.info(f"Published song index {index_run} without a rollout (--no-deploy): latest.json points at it")
        return {"published": True, "deployed": False, "run": run, "token": token, "index_run": index_run}

    rollout = run_admin.set_env(INDEX_RUN_ENV, index_run)
    if not rollout.serving:
        pinned = ", ".join(rollout.pinned) or "an older revision"
        first = _report(
            storage,
            f"pinned:{','.join(sorted(rollout.pinned))}",
            f"song-index-build: {SEARCH_SERVICE} traffic is pinned to {pinned}; new index {index_run} is "
            f"uploaded but not serving (its revision gets no traffic); latest.json and pruning skipped. "
            f"To serve it: gcloud run services update-traffic {SEARCH_SERVICE} --to-latest "
            f"--region {REGION}.",
        )
        return {
            "published": False,
            "run": run,
            "token": token,
            "index_run": index_run,
            "pinned": rollout.pinned,
            "already_reported": not first,
        }
    storage.upload_bytes(
        INDEX_BUCKET, INDEX_LATEST, json.dumps({"run": run, "token": token}).encode(), content_type=_CT_JSON
    )
    try:
        keep = {index_run} | run_admin.serving_env_values(INDEX_RUN_ENV)
    except Exception as exc:  # noqa: BLE001 - never prune blind
        logger.warning(f"Pruning skipped: can't read the runs live revisions serve: {exc}")
    else:
        _prune(storage, keep)
    logger.info(f"Published song index {index_run} in {time.monotonic() - t0:.0f}s")
    return {
        "published": True,
        "deployed": True,
        "run": run,
        "token": token,
        "index_run": index_run,
        "eval": _brief(manifest["eval"]),
        "dual_eval": _brief(manifest["dual_eval"]),
    }


# --------------------------------------------------------------------------
# Local dry run
# --------------------------------------------------------------------------


def _copy_input(src: Path, dest: Path, *, gz: bool) -> Path:
    """Copy ``src`` into the workdir (the build deletes its inputs), gzipping if asked
    and it isn't already."""
    if gz and src.suffix != ".gz":
        with open(src, "rb") as f, open(dest, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as g:
            shutil.copyfileobj(f, g)
    else:
        shutil.copyfile(src, dest)
    return dest


def dry_run(workdir: Path, songs: list[Path], kn_full: Path, kn_community: Path, run: str) -> dict[str, Any]:
    """Build + evaluate from local files into ``workdir``; write ``manifest.json`` there.
    Nothing is uploaded or deployed. Gates run without a baseline (dual eval only)."""
    workdir = Path(workdir)
    inputs = workdir / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    src = SourceFiles(
        shards=[_copy_input(p, inputs / f"{i:05d}-{p.name}", gz=False) for i, p in enumerate(songs)],
        kn_full=_copy_input(kn_full, inputs / "full.json.gz", gz=True),
        kn_community=_copy_input(kn_community, inputs / "community.json.gz", gz=True),
    )
    token = secrets.token_hex(8)
    manifest = build_local(src, workdir, run, token)
    (workdir / "manifest.json").write_text(json.dumps(manifest, indent=1))
    failures = gate_failures(manifest["eval"], manifest["dual_eval"], None)
    if failures:
        _report(None, "", _gate_message(run, failures, None))
        return {"published": False, "dry_run": True, "run": run, "failures": failures}
    logger.info(f"Dry run built {run}-{token} in {workdir} (not uploaded)")
    return {"published": True, "dry_run": True, "run": run, "token": token, "workdir": str(workdir)}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build, eval-gate and publish the song-search indexes")
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run", help="Build + publish (skipped when the source is unchanged)")
    p_run.add_argument("--force", action="store_true", help="Rebuild even if the source run is already published")
    p_run.add_argument("--workdir", type=Path, help="Local build directory (default: a new temp dir)")
    p_run.add_argument(
        "--no-deploy",
        action="store_true",
        help="First rollout only, before the search service exists: publish + point latest.json, no rollout",
    )
    p_run.add_argument("--dry-run", action="store_true", help="Build from local files only; no GCS, no deploy")
    p_run.add_argument("--songs", type=Path, nargs="+", help="dry run: song-id TSV(.gz) shard(s)")
    p_run.add_argument("--kn-full", type=Path, help="dry run: KN full export (.json or .json.gz)")
    p_run.add_argument("--kn-community", type=Path, help="dry run: KN community export (.json or .json.gz)")
    p_run.add_argument("--run", default="local", help="dry run: run id (default: local)")
    args = parser.parse_args(argv)

    common.configure_logging()
    workdir = args.workdir or Path(os.environ.get("SONG_INDEX_WORKDIR") or tempfile.mkdtemp(prefix="song-index-build-"))
    try:
        if args.dry_run:
            out = dry_run(
                workdir,
                args.songs or [_REPO_FIXTURES / "songs_fixture.tsv"],
                args.kn_full or _REPO_FIXTURES / "kn_full_fixture.json",
                args.kn_community or _REPO_FIXTURES / "kn_community_fixture.json",
                args.run,
            )
        else:
            out = build_and_publish(
                storage=GcsStore(_storage_client()),
                run_admin=CloudRunAdmin(f"projects/{PROJECT_ID}/locations/{REGION}/services/{SEARCH_SERVICE}"),
                workdir=workdir,
                force=args.force,
                deploy=not args.no_deploy,
            )
    except Exception as exc:
        logger.exception(f"song-index-build failed; search keeps serving its current index: {exc}")
        return 1
    logger.info(f"song-index-build finished {datetime.now(UTC):%Y-%m-%d %H:%M} UTC: {json.dumps(out)}")
    # A repeat of an already-alerted failure exits 0 so Cloud Run's task-failure log
    # doesn't page again every day; a first-time failure or a crash exits 1.
    return 0 if out.get("published") or out.get("skipped") or out.get("already_reported") else 1


if __name__ == "__main__":
    sys.exit(main())
