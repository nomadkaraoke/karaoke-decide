"""Hand-off to karaoke-gen's GPU vocal separation job, and collection of its results.

``submit_batch`` claims the songs (``fetched`` -> ``separating``, transactionally), writes a
manifest (``gs://karaoke-gen-storage-nomadkaraoke/voice-pool/manifests/<run>.jsonl``, lines
``{"id", "audio"}``) and runs gen's ``voice-pool-separate-job`` (us-east4,
GPU, ``parallelism=1``, 3600 s per task, no retries) with up to ``SONGS_PER_TASK`` songs per task.
The worker writes ``<run>.results/part-<task>.jsonl`` lines ``{"id", "ok", "vocals", "seconds",
"error"}``; ``collect_results`` applies them. A song still without a result ``STALE_AFTER_S`` after
its batch was submitted (the execution timed out or died) goes back to ``fetched``, counting an
attempt, so a song that kills its task ends up ``failed`` instead of looping on the GPU.
"""

from __future__ import annotations

import json
import logging
import math
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from karaoke_decide.voice_pool.queue import FETCH_COLLECTION

logger = logging.getLogger(__name__)

GEN_BUCKET = "karaoke-gen-storage-nomadkaraoke"
SEPARATE_JOB = "voice-pool-separate-job"
SEPARATE_REGION = "us-east4"
WORKER_ARGS = ["python", "-m", "backend.workers.voice_pool_separate_worker", "--manifest"]
SONGS_PER_TASK = 20  # a task must finish inside Cloud Run's 1 h GPU task timeout
MAX_TASKS = 4
MAX_BATCH = SONGS_PER_TASK * MAX_TASKS
STALE_AFTER_S = 5 * 3600  # 4 serial 1 h tasks plus queueing
MAX_ATTEMPTS = 3


class JobRunner(Protocol):
    """Starts Cloud Run job executions and says whether a job has one in progress."""

    def run(self, job: str, region: str, args: list[str] | None = None, task_count: int | None = None) -> str: ...

    def is_running(self, job: str, region: str) -> bool: ...


class CloudRunJobRunner:
    """``JobRunner`` over ``google.cloud.run_v2``: ``JobsClient.run_job`` starts an execution (it
    doesn't wait for it), ``ExecutionsClient.list_executions`` says whether one is in progress."""

    RECENT_EXECUTIONS = 20  # newest first; a running execution is always among the latest

    def __init__(self, project: str, client: Any = None, executions_client: Any = None) -> None:
        self.project = project
        self._client = client
        self._executions = executions_client

    @property
    def client(self) -> Any:
        """The ``run_v2.JobsClient`` (created on first use)."""
        if self._client is None:
            from google.cloud import run_v2

            self._client = run_v2.JobsClient()
        return self._client

    @property
    def executions(self) -> Any:
        """The ``run_v2.ExecutionsClient`` (created on first use)."""
        if self._executions is None:
            from google.cloud import run_v2

            self._executions = run_v2.ExecutionsClient()
        return self._executions

    def _job(self, job: str, region: str) -> str:
        return f"projects/{self.project}/locations/{region}/jobs/{job}"

    def run(self, job: str, region: str, args: list[str] | None = None, task_count: int | None = None) -> str:
        from google.cloud import run_v2

        overrides = run_v2.RunJobRequest.Overrides(
            container_overrides=[run_v2.RunJobRequest.Overrides.ContainerOverride(args=args)] if args else [],
            task_count=task_count or 0,  # 0 = the job's own task count
        )
        op = self.client.run_job(request=run_v2.RunJobRequest(name=self._job(job, region), overrides=overrides))
        execution = getattr(op, "metadata", None)
        return str(getattr(execution, "name", "") or self._job(job, region))

    def is_running(self, job: str, region: str) -> bool:
        from google.cloud import run_v2

        request = run_v2.ListExecutionsRequest(parent=self._job(job, region), page_size=self.RECENT_EXECUTIONS)
        for i, execution in enumerate(self.executions.list_executions(request=request)):
            if i >= self.RECENT_EXECUTIONS:
                break
            if execution.completion_time is None:
                return True
        return False


def gen_uri(name: str | None) -> str | None:
    """gen's result ``vocals`` as a ``gs://`` URI: gen writes a bare object name in its own bucket
    (``voice-pool/stems/<id>/vocals.flac``); a ``gs://`` URI is kept as is, empty -> None."""
    if not isinstance(name, str) or not name.strip():
        return None
    name = name.strip()
    return name if name.startswith("gs://") else f"gs://{GEN_BUCKET}/{name.lstrip('/')}"


def canonical_audio(pid: str) -> str:
    return f"gs://{GEN_BUCKET}/voice-pool/audio/{pid}/original.flac"


def manifest_line(item: dict[str, Any]) -> dict[str, str]:
    return {"id": item["id"], "audio": item.get("audio_gcs") or canonical_audio(item["id"])}


def _manifest_name(run: str) -> str:
    return f"voice-pool/manifests/{run}.jsonl"


def task_count(n: int) -> int:
    return max(1, min(MAX_TASKS, math.ceil(n / SONGS_PER_TASK)))


def write_manifest(gcs: Any, run: str, items: list[dict[str, Any]]) -> str:
    """Write the JSONL manifest for ``items`` (fetch-queue docs with ``id``); returns its gs:// URI."""
    body = "".join(json.dumps(manifest_line(i)) + "\n" for i in items)
    name = _manifest_name(run)
    with gcs.bucket(GEN_BUCKET).blob(name).open("wb", content_type="application/x-ndjson") as f:
        f.write(body.encode())
    return f"gs://{GEN_BUCKET}/{name}"


def _ts(now: Callable[[], float]) -> datetime:
    return datetime.fromtimestamp(now(), UTC)


class _NotFetchedError(Exception):
    pass


async def claim_for_separation(fs: Any, pid: str, run: str, stamp: datetime) -> bool:
    """Atomically move ``pid`` from ``fetched`` to ``separating`` under ``run``; False when it is no
    longer ``fetched`` (an overlapping fetch execution took it)."""

    def transform(data: dict[str, Any] | None) -> dict[str, Any]:
        if data is None or data.get("status") != "fetched":
            raise _NotFetchedError
        return {**data, "status": "separating", "manifest_run": run, "separating_at": stamp, "updated_at": stamp}

    try:
        await fs.transform_document_atomically(FETCH_COLLECTION, pid, transform)
    except _NotFetchedError:
        return False
    return True


async def submit_batch(
    fs: Any, gcs: Any, runner: JobRunner, items: list[dict[str, Any]], run: str, *, now: Callable[[], float] = time.time
) -> tuple[str | None, int]:
    """Separate up to ``MAX_BATCH`` of ``items`` (the rest wait for the next run); returns
    ``(execution name, songs)``, ``(None, 0)`` when another run had already claimed them all.

    Each song is claimed transactionally (``fetched`` -> ``separating``) before the manifest is
    written, so overlapping fetch executions never separate a song twice. If the manifest write
    or the job start fails, the claims are reverted (back to ``fetched``) and the error re-raised.
    """
    stamp = _ts(now)
    batch: list[dict[str, Any]] = []
    for item in items:
        if len(batch) >= MAX_BATCH:
            break
        if await claim_for_separation(fs, item["id"], run, stamp):
            batch.append(item)
    if not batch:
        logger.info("voice pool: every fetched song was claimed by another run; nothing to separate")
        return None, 0
    try:
        uri = write_manifest(gcs, run, batch)
        execution = runner.run(
            SEPARATE_JOB, SEPARATE_REGION, args=[*WORKER_ARGS, uri], task_count=task_count(len(batch))
        )
    except BaseException:
        for item in batch:
            await fs.update_document(
                FETCH_COLLECTION,
                item["id"],
                {"status": "fetched", "manifest_run": None, "separating_at": None, "updated_at": _ts(now)},
            )
        raise
    logger.info("voice pool: %d songs to separation (%s, %d tasks)", len(batch), execution, task_count(len(batch)))
    return execution, len(batch)


def _read_results(gcs: Any, run: str) -> dict[str, dict[str, Any]]:
    prefix = f"voice-pool/manifests/{run}.results/"
    out: dict[str, dict[str, Any]] = {}
    for blob in gcs.bucket(GEN_BUCKET).list_blobs(prefix=prefix):
        if not blob.name[len(prefix) :].startswith("part-") or not blob.name.endswith(".jsonl"):
            continue
        with blob.open("rb") as f:
            for raw in f.read().decode().splitlines():
                if not raw.strip():
                    continue
                try:
                    line = json.loads(raw)
                    out[str(line["id"])] = line
                except (ValueError, KeyError, TypeError):
                    logger.warning("voice pool: unreadable result line in %s: %.200s", blob.name, raw)
    return out


async def collect_results(
    fs: Any, gcs: Any, run: str, *, now: Callable[[], float] = time.time, requeue_stale: bool = True
) -> tuple[int, int]:
    """Apply run ``run``'s separation results; returns ``(separated, sent_back)``.

    ``ok`` -> ``separated`` with ``vocals_gcs`` (gen's bare object name made a gen-bucket URI); not ok -> ``fetched`` with ``attempts += 1``
    (``failed`` at ``MAX_ATTEMPTS``). Songs with no result after ``STALE_AFTER_S`` go back to
    ``fetched`` with ``attempts += 1`` too (``requeue_stale=False`` while a separation execution
    is still running).
    """
    docs = await fs.query_documents(
        FETCH_COLLECTION, filters=[("status", "==", "separating"), ("manifest_run", "==", run)]
    )
    if not docs:
        return 0, 0
    results = _read_results(gcs, run)
    stamp = _ts(now)
    separated = sent_back = 0
    for doc in docs:
        res = results.get(doc["id"])
        update: dict[str, Any]
        if res is not None and res.get("ok"):
            update = {
                "status": "separated",
                "vocals_gcs": gen_uri(res.get("vocals")),
                "separation_s": res.get("seconds"),
            }
            update["error"] = None
            separated += 1
        elif res is not None:
            attempts = int(doc.get("attempts") or 0) + 1
            status = "failed" if attempts >= MAX_ATTEMPTS else "fetched"
            update = {"status": status, "attempts": attempts, "error": res.get("error"), "manifest_run": None}
            sent_back += 1
        elif (
            requeue_stale and (stamp - (doc.get("separating_at") or doc["updated_at"])).total_seconds() > STALE_AFTER_S
        ):
            # The task died or timed out on it: maybe this song (OOM, too slow), so it counts.
            attempts = int(doc.get("attempts") or 0) + 1
            status = "failed" if attempts >= MAX_ATTEMPTS else "fetched"
            error = f"no separation result {STALE_AFTER_S // 3600} h after run {run}"
            update = {"status": status, "attempts": attempts, "error": error, "manifest_run": None}
            sent_back += 1
        else:
            continue
        await fs.update_document(FETCH_COLLECTION, doc["id"], {**update, "updated_at": stamp})
    return separated, sent_back
