"""Hourly voice-pool fetch job: fetch queue -> flacfetch (Spotify, batch) -> gen separation -> analyse.

One run (``run_fetch``):

1. Poll every ``downloading`` song: flacfetch ``complete`` -> ``fetched`` (``audio_gcs``);
   ``failed`` / ``cancelled`` -> ``attempts += 1``, back to ``queued`` (``failed`` at 3). An id
   flacfetch forgot (restart) counts as fetched when the audio is already in GCS, else failed.
2. Submit queued songs, best ``(priority, added_at)`` first, while the run has time left and
   flacfetch's Spotify budget allows a batch download; ``uniform(45, 180)`` s between
   submissions (``rng`` is seeded from the run id). Each song is claimed transactionally first
   (overlapping executions skip it); a 400 / 422 for one song is that song's failed attempt. When flacfetch says "not now" (budget
   closed or a 429), wait its ``retry_after_s`` if that still fits in the run, else stop: the
   gates that clear in seconds or minutes (``batch_busy``, ``batch_gap``,
   ``interactive_active``) are waited out, an exhausted 24 h budget ends the run.
3. Collect separation results (``separate.collect_results``) for every run with songs still
   ``separating``.
4. With >= ``SEPARATE_AT`` songs ``fetched``, or the oldest fetched > ``SEPARATE_AFTER_S`` ago,
   and no separation execution running: ``separate.submit_batch``.
5. Anything ``separated`` and no ``voice-pool-analyse`` execution running: start one.

flacfetch outages and missing / forbidden jobs or GCS objects (the infra lands separately) are
WARNINGs: the run carries on with the other steps. Misconfiguration (e.g. a rejected API key) raises.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from google.api_core.exceptions import Forbidden, GoogleAPICallError, NotFound

from karaoke_decide.voice_pool import queue, separate
from karaoke_decide.voice_pool.flacfetch_client import (
    FlacfetchClient,
    FlacfetchRejectedError,
    FlacfetchUnavailableError,
    Refused,
)
from karaoke_decide.voice_pool.separate import JobRunner

logger = logging.getLogger(__name__)

FETCH = queue.FETCH_COLLECTION
MAX_ATTEMPTS = 3
SPACING_S = (45.0, 180.0)
MIN_WAIT_S = 30.0  # flacfetch's minimum gap between batch starts
MAX_EXPECTED_DURATION_S = 1200  # flacfetch's expected_duration_s limit
QUEUE_SCAN = 1000
CLAIM_GRACE_S = 600  # a claimed song without a flacfetch id is orphaned (crash) after this
SEPARATE_AT = 25
SEPARATE_AFTER_S = 6 * 3600
ANALYSE_JOB = "voice-pool-analyse"
ANALYSE_REGION = "us-central1"
_JOB_UNAVAILABLE = (NotFound, Forbidden)  # PermissionDenied (gRPC) subclasses Forbidden (GCS 403)

Sleep = Callable[[float], Awaitable[None]]
Now = Callable[[], float]


def audio_dir(pid: str) -> str:
    """flacfetch ``gcs_path`` for a pool song; with ``output_filename="original"`` the file lands
    at ``voice-pool/audio/<pool_id>/original.flac``."""
    return f"voice-pool/audio/{pid}/"


def _stamp(now: Now) -> datetime:
    return datetime.fromtimestamp(now(), UTC)


async def _update(fs: Any, pid: str, data: dict[str, Any], now: Now) -> None:
    await fs.update_document(FETCH, pid, {**data, "updated_at": _stamp(now)})


async def _retry_or_fail(fs: Any, doc: dict[str, Any], error: str, now: Now) -> None:
    attempts = int(doc.get("attempts") or 0) + 1
    status = "failed" if attempts >= MAX_ATTEMPTS else "queued"
    await _update(fs, doc["id"], {"status": status, "attempts": attempts, "flacfetch_id": None, "error": error}, now)


def _uploaded_audio(gcs: Any, pid: str) -> str | None:
    """The song's canonical audio URI when the object exists (a download flacfetch forgot after
    uploading it), else None."""
    uri = separate.canonical_audio(pid)
    try:
        exists = gcs.bucket(separate.GEN_BUCKET).blob(uri.split("/", 3)[3]).exists()
    except GoogleAPICallError as e:
        logger.warning("voice pool: can't check %s (%s)", uri, e)
        return None
    return uri if exists else None


async def poll_downloads(fs: Any, ff: FlacfetchClient, gcs: Any, now: Now) -> tuple[int, int]:
    """Step 1; returns ``(polled, fetched)``."""
    docs = await fs.query_documents(FETCH, filters=[("status", "==", "downloading")])
    polled = fetched = 0
    for doc in docs:
        if not doc.get("flacfetch_id") and (_stamp(now) - doc["updated_at"]).total_seconds() < CLAIM_GRACE_S:
            continue  # claimed by a run that is starting it right now
        try:
            st = ff.status(doc["flacfetch_id"]) if doc.get("flacfetch_id") else None
        except FlacfetchUnavailableError as e:
            logger.warning("voice pool: flacfetch unreachable while polling downloads (%s); next run retries", e)
            break
        polled += 1
        if st is None:  # flacfetch restarted (its task list is in memory): maybe it uploaded first
            st = {"status": "complete", "gcs_path": _uploaded_audio(gcs, doc["id"])}
            st["error"] = None if st["gcs_path"] else f"flacfetch download {doc.get('flacfetch_id')} not found"
        if st.get("status") == "complete" and st.get("gcs_path"):
            await _update(
                fs, doc["id"], {"status": "fetched", "audio_gcs": st["gcs_path"], "fetched_at": _stamp(now)}, now
            )
            fetched += 1
        elif st.get("status") in ("complete", "failed", "cancelled"):
            await _retry_or_fail(fs, doc, st.get("error") or f"flacfetch {st.get('status')} without a GCS file", now)
    return polled, fetched


def _unfetchable(doc: dict[str, Any]) -> str | None:
    if not doc.get("spotify_id"):
        return "no spotify id"
    if not 1 <= round((doc.get("duration_ms") or 0) / 1000) <= MAX_EXPECTED_DURATION_S:
        return f"duration {doc.get('duration_ms')} ms outside flacfetch's 1-{MAX_EXPECTED_DURATION_S} s"
    return None


class _NotQueuedError(Exception):
    pass


async def claim(fs: Any, pid: str, now: Now) -> bool:
    """Atomically move ``pid`` from ``queued`` to ``downloading`` (no flacfetch id yet); False when
    it is no longer queued (another execution took it)."""
    stamp = _stamp(now)

    def transform(data: dict[str, Any] | None) -> dict[str, Any]:
        if data is None or data.get("status") != "queued":
            raise _NotQueuedError
        return {**data, "status": "downloading", "flacfetch_id": None, "updated_at": stamp}

    try:
        await fs.transform_document_atomically(FETCH, pid, transform)
    except _NotQueuedError:
        return False
    return True


def _start(ff: FlacfetchClient, doc: dict[str, Any]) -> str | Refused:
    budget = ff.budget()
    if not budget.get("batch_allowed_now"):
        return Refused("budget_closed", int(budget.get("retry_after_s") or 0))
    return ff.start_batch(doc["spotify_id"], round(doc["duration_ms"] / 1000), audio_dir(doc["id"]), "original")


async def submit_downloads(
    fs: Any, ff: FlacfetchClient, *, deadline: float, rng: random.Random, sleep: Sleep, now: Now
) -> tuple[int, str | None]:
    """Step 2; returns ``(submitted, why_stopped)`` (None: queue drained or deadline).

    Each song is claimed (``queued`` -> ``downloading``, transactionally) before flacfetch is
    asked, so overlapping executions never submit it twice; a refusal or outage puts it back.
    """
    submitted = 0
    for doc in await queue.next_queued(fs, QUEUE_SCAN):
        reason = _unfetchable(doc)
        if reason:
            logger.info("voice pool: %s (%s) can't be fetched: %s", doc["id"], doc.get("song_key"), reason)
            await _update(fs, doc["id"], {"status": "failed", "error": reason}, now)
            continue
        if submitted:
            await sleep(rng.uniform(*SPACING_S))
        while True:
            if now() >= deadline:
                return submitted, None
            if not await claim(fs, doc["id"], now):
                logger.info("voice pool: %s was taken by another run; skipping", doc["id"])
                break
            try:
                started = _start(ff, doc)
            except FlacfetchRejectedError as e:
                logger.warning("voice pool: %s (%s): %s", doc["id"], doc.get("song_key"), e)
                await _retry_or_fail(fs, doc, str(e), now)
                break
            except Exception as e:
                await _update(fs, doc["id"], {"status": "queued", "flacfetch_id": None}, now)
                if not isinstance(e, FlacfetchUnavailableError):
                    raise
                logger.warning("voice pool: flacfetch unavailable (%s); no more downloads this run", e)
                return submitted, e.code
            if not isinstance(started, Refused):
                await _update(fs, doc["id"], {"flacfetch_id": started, "error": None}, now)
                submitted += 1
                break
            await _update(fs, doc["id"], {"status": "queued", "flacfetch_id": None}, now)
            wait = max(float(started.retry_after_s), MIN_WAIT_S)
            if now() + wait >= deadline:
                logger.info(
                    "voice pool: flacfetch says %s, retry in %d s: no more downloads this run",
                    started.code,
                    started.retry_after_s,
                )
                return submitted, started.code
            await sleep(wait)
    return submitted, None


def _job_call(what: str, fn: Callable[[], Any], default: Any) -> Any:
    try:
        return fn()
    except _JOB_UNAVAILABLE as e:
        logger.warning("voice pool: can't %s yet (%s); infra pending?", what, e)
        return default


async def collect_separations(fs: Any, gcs: Any, runner: JobRunner, now: Now) -> tuple[int, int]:
    """Step 3; returns ``(separated, sent_back)`` over every run with songs still separating."""
    docs = await fs.query_documents(FETCH, filters=[("status", "==", "separating")])
    runs = sorted({d["manifest_run"] for d in docs if d.get("manifest_run")})
    if not runs:
        return 0, 0
    running = _job_call(
        f"check {separate.SEPARATE_JOB}",
        lambda: runner.is_running(separate.SEPARATE_JOB, separate.SEPARATE_REGION),
        True,
    )
    separated = sent_back = 0
    for run in runs:
        try:
            s, b = await separate.collect_results(fs, gcs, run, now=now, requeue_stale=not running)
        except _JOB_UNAVAILABLE as e:
            logger.warning("voice pool: can't read separation results for %s yet (%s)", run, e)
            continue
        separated += s
        sent_back += b
    return separated, sent_back


async def maybe_separate(fs: Any, gcs: Any, runner: JobRunner, run: str, now: Now) -> int:
    """Step 4; returns the number of songs sent to separation."""
    docs = await fs.query_documents(FETCH, filters=[("status", "==", "fetched")])
    if not docs:
        return 0
    docs.sort(key=lambda d: d.get("fetched_at") or d["updated_at"])
    oldest_s = (_stamp(now) - (docs[0].get("fetched_at") or docs[0]["updated_at"])).total_seconds()
    if len(docs) < SEPARATE_AT and oldest_s <= SEPARATE_AFTER_S:
        return 0
    check = f"check {separate.SEPARATE_JOB}"
    if _job_call(check, lambda: runner.is_running(separate.SEPARATE_JOB, separate.SEPARATE_REGION), True):
        logger.info("voice pool: %d fetched, separation already running; next run", len(docs))
        return 0
    try:
        await separate.submit_batch(fs, gcs, runner, docs, run, now=now)
    except _JOB_UNAVAILABLE as e:
        logger.warning("voice pool: can't run %s yet (%s); infra pending?", separate.SEPARATE_JOB, e)
        return 0
    return min(len(docs), separate.MAX_BATCH)


async def maybe_analyse(fs: Any, runner: JobRunner) -> bool:
    """Step 5; whether an analyse execution was started."""
    if not await fs.query_documents(FETCH, filters=[("status", "==", "separated")], limit=1):
        return False
    if _job_call(f"check {ANALYSE_JOB}", lambda: runner.is_running(ANALYSE_JOB, ANALYSE_REGION), True):
        return False
    return bool(
        _job_call(f"run {ANALYSE_JOB}", lambda: runner.run(ANALYSE_JOB, ANALYSE_REGION, args=["analyse"]), None)
    )


async def run_fetch(
    fs: Any,
    ff: FlacfetchClient,
    runner: JobRunner,
    gcs: Any,
    *,
    run: str,
    rng: random.Random,
    deadline_s: float = 3300,
    sleep: Sleep = asyncio.sleep,
    now: Now = time.time,
) -> dict[str, Any]:
    """One scheduler run (see module doc). Returns ``{run, polled, fetched, submitted, refused_code,
    separated, requeued, separating, analyse_started}``."""
    deadline = now() + deadline_s
    polled, fetched = await poll_downloads(fs, ff, gcs, now)
    submitted, refused_code = await submit_downloads(fs, ff, deadline=deadline, rng=rng, sleep=sleep, now=now)
    separated, requeued = await collect_separations(fs, gcs, runner, now)
    separating = await maybe_separate(fs, gcs, runner, run, now)
    analyse_started = await maybe_analyse(fs, runner)
    out = {
        "run": run,
        "polled": polled,
        "fetched": fetched,
        "submitted": submitted,
        "refused_code": refused_code,
        "separated": separated,
        "requeued": requeued,
        "separating": separating,
        "analyse_started": analyse_started,
    }
    logger.info("voice pool fetch %s: %s", run, out)
    return out


async def status_summary(fs: Any, ff: FlacfetchClient | None) -> dict[str, Any]:
    """Queue counts per status and flacfetch's Spotify budget (None when unknown)."""
    counts = {s: await fs.count_documents(FETCH, filters=[("status", "==", s)]) for s in queue.STATUSES}
    budget = None
    if ff is not None:
        try:
            budget = ff.budget()
        except FlacfetchUnavailableError as e:
            logger.warning("voice pool: flacfetch budget unavailable (%s)", e)
    return {"counts": counts, "budget": budget}
