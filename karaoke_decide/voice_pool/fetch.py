"""Hourly voice-pool fetch job: fetch queue -> flacfetch (Spotify, batch) -> gen separation -> analyse.

One run (``run_fetch``):

1. Poll every ``downloading`` song: flacfetch ``complete`` -> ``fetched`` (``audio_gcs``);
   ``failed`` / ``cancelled`` / unknown id -> ``attempts += 1``, back to ``queued`` (``failed``
   at 3).
2. Submit queued songs, best ``(priority, added_at)`` first, while the run has time left and
   flacfetch's Spotify budget allows a batch download; ``uniform(45, 180)`` s between
   submissions (``rng`` is seeded from the run id). When flacfetch says "not now" (budget
   closed or a 429), wait its ``retry_after_s`` if that still fits in the run, else stop: the
   gates that clear in seconds or minutes (``batch_busy``, ``batch_gap``,
   ``interactive_active``) are waited out, an exhausted 24 h budget ends the run.
3. Collect separation results (``separate.collect_results``) for every run with songs still
   ``separating``.
4. With >= ``SEPARATE_AT`` songs ``fetched``, or the oldest fetched > ``SEPARATE_AFTER_S`` ago,
   and no separation execution running: ``separate.submit_batch``.
5. Anything ``separated`` and no ``voice-pool-analyse`` execution running: start one.

flacfetch outages and missing / forbidden jobs (the infra lands separately) are WARNINGs: the
run carries on with the other steps. Misconfiguration (e.g. a rejected API key) raises.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from google.api_core.exceptions import NotFound, PermissionDenied

from karaoke_decide.voice_pool import queue, separate
from karaoke_decide.voice_pool.flacfetch_client import FlacfetchClient, FlacfetchUnavailableError, Refused
from karaoke_decide.voice_pool.separate import JobRunner

logger = logging.getLogger(__name__)

FETCH = queue.FETCH_COLLECTION
MAX_ATTEMPTS = 3
SPACING_S = (45.0, 180.0)
MIN_WAIT_S = 5.0
MAX_EXPECTED_DURATION_S = 1200  # flacfetch's expected_duration_s limit
QUEUE_SCAN = 1000
SEPARATE_AT = 25
SEPARATE_AFTER_S = 6 * 3600
ANALYSE_JOB = "voice-pool-analyse"
ANALYSE_REGION = "us-central1"
_JOB_UNAVAILABLE = (NotFound, PermissionDenied)

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


async def poll_downloads(fs: Any, ff: FlacfetchClient, now: Now) -> tuple[int, int]:
    """Step 1; returns ``(polled, fetched)``."""
    docs = await fs.query_documents(FETCH, filters=[("status", "==", "downloading")])
    polled = fetched = 0
    for doc in docs:
        try:
            st = ff.status(doc["flacfetch_id"])
        except FlacfetchUnavailableError as e:
            logger.warning("voice pool: flacfetch unreachable while polling downloads (%s); next run retries", e)
            break
        polled += 1
        if st is None:
            await _retry_or_fail(fs, doc, f"flacfetch download {doc['flacfetch_id']} not found", now)
        elif st.get("status") == "complete" and st.get("gcs_path"):
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


async def submit_downloads(
    fs: Any, ff: FlacfetchClient, *, deadline: float, rng: random.Random, sleep: Sleep, now: Now
) -> tuple[int, str | None]:
    """Step 2; returns ``(submitted, why_stopped)`` (None: queue drained or deadline)."""
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
            try:
                budget = ff.budget()
                if budget.get("batch_allowed_now"):
                    started = ff.start_batch(
                        doc["spotify_id"], round(doc["duration_ms"] / 1000), audio_dir(doc["id"]), "original"
                    )
                else:
                    started = Refused("budget_closed", int(budget.get("retry_after_s") or 0))
            except FlacfetchUnavailableError as e:
                logger.warning("voice pool: flacfetch unavailable (%s); no more downloads this run", e)
                return submitted, e.code
            if not isinstance(started, Refused):
                await _update(fs, doc["id"], {"status": "downloading", "flacfetch_id": started, "error": None}, now)
                submitted += 1
                break
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
        s, b = await separate.collect_results(fs, gcs, run, now=now, requeue_stale=not running)
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
    polled, fetched = await poll_downloads(fs, ff, now)
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
