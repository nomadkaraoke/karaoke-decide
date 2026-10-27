"""Hourly voice-pool fetch job: fetch queue -> flacfetch (Spotify, batch) -> gen separation -> analyse.

One run (``run_fetch``):

1. Poll every ``downloading`` song: flacfetch ``complete`` -> ``fetched`` (``audio_gcs``);
   ``failed`` / ``cancelled`` -> ``attempts += 1``, back to ``queued`` (``failed`` at 3). An id
   flacfetch forgot (restart) counts as fetched when the audio is already in GCS, else failed.
2. Resolve queued songs enqueued without a Spotify track (Andrew's sheet, personal pools): ONE
   batched lookup per run (``resolve``: catalog.db KaraokeNerds ids -> BigQuery, the selection
   job's own rule, ``spotify.resolve_song_keys``) writes ``spotify_id`` / ``duration_ms`` onto the
   doc. Songs still without one (or with a duration flacfetch can't take) are marked ``failed``
   with ONE aggregated WARNING per run. When the lookup itself is unavailable (catalog not loaded,
   BigQuery transient) they stay ``queued`` for the next run.
   Submit queued songs, best ``(priority, added_at)`` first (personal-pool songs only once no
   shared / sheet song is still queued), while the run has time left and
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
Transient Cloud Run API errors (503, 500, 429, deadline, connection) are WARNINGs too. Any other
Cloud Run job failure (an API error or a bug in the runner) is a WARNING when it happens; the run
carries on, then raises ``JobRunnerFailedError`` once at the end, which the CLI logs as the run's
one ERROR.

Each failed download is a WARNING. A ``downloading`` song still unfinished ``DOWNLOAD_TIMEOUT_S``
(2 h) after submission counts as a failed attempt (a hung capture). When one run sees
``SYSTEMATIC_DOWNLOAD_FAILURES`` (5) or more failures and no success, it logs ONE ERROR naming the
most common error (in ``json_fields``) and submits nothing new: each attempt costs Spotify budget.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import Counter
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
from karaoke_decide.voice_pool.transient import TRANSIENT_ERRORS

logger = logging.getLogger(__name__)

FETCH = queue.FETCH_COLLECTION
MAX_ATTEMPTS = 3
SPACING_S = (45.0, 180.0)
MIN_WAIT_S = 30.0  # flacfetch's minimum gap between batch starts
MAX_EXPECTED_DURATION_S = 1200  # flacfetch's expected_duration_s limit
QUEUE_SCAN = 1000
CLAIM_GRACE_S = 600  # a claimed song without a flacfetch id is orphaned (crash) after this
# A download still not finished this long after it was submitted is treated as hung (a stuck
# capture): it counts a failed attempt, so the song can't hold its place forever.
DOWNLOAD_TIMEOUT_S = 2 * 3600
# This many download failures in one run with no success = something systematic (account,
# region, flacfetch bug): ONE ERROR, and no new downloads this run (each one costs budget).
SYSTEMATIC_DOWNLOAD_FAILURES = 5
SEPARATE_AT = 25
SEPARATE_AFTER_S = 6 * 3600
ANALYSE_JOB = "voice-pool-analyse"
ANALYSE_REGION = "us-central1"
_JOB_UNAVAILABLE = (NotFound, Forbidden)  # PermissionDenied (gRPC) subclasses Forbidden (GCS 403)

Sleep = Callable[[float], Awaitable[None]]
Now = Callable[[], float]
# ``resolve(song_keys)`` -> ``{song_key: (spotify_id, duration_ms)}`` for the keys it can resolve.
Resolver = Callable[[list[str]], dict[str, tuple[str | None, int | None]]]
NO_SPOTIFY_ID = "no spotify id"
PERSONAL = "personal"  # queue source of personal-pool songs: they wait for the shared pool (submit_downloads)


class SpotifyResolveUnavailableError(RuntimeError):
    """The Spotify id lookup can't run this run (e.g. catalog.db not loaded); songs stay queued."""


_RESOLVE_UNAVAILABLE: tuple[type[BaseException], ...] = (
    GoogleAPICallError,
    SpotifyResolveUnavailableError,
    *TRANSIENT_ERRORS,
)


def audio_dir(pid: str) -> str:
    """flacfetch ``gcs_path`` for a pool song; with ``output_filename="original"`` the file lands
    at ``voice-pool/audio/<pool_id>/original.flac``."""
    return f"voice-pool/audio/{pid}/"


def _stamp(now: Now) -> datetime:
    return datetime.fromtimestamp(now(), UTC)


async def _update(fs: Any, pid: str, data: dict[str, Any], now: Now) -> None:
    await fs.update_document(FETCH, pid, {**data, "updated_at": _stamp(now)})


async def _retry_or_fail(fs: Any, doc: dict[str, Any], error: str, now: Now) -> str:
    """Count a failed download attempt; returns the new status (``queued`` again, or ``failed`` at the limit)."""
    attempts = int(doc.get("attempts") or 0) + 1
    status = "failed" if attempts >= MAX_ATTEMPTS else "queued"
    logger.warning(
        "voice pool: download of %s (%s) failed (attempt %d/%d, now %s): %s",
        doc["id"], doc.get("song_key"), attempts, MAX_ATTEMPTS, status, error,
    )  # fmt: skip
    await _update(
        fs,
        doc["id"],
        {"status": status, "attempts": attempts, "flacfetch_id": None, "download_started_at": None, "error": error},
        now,
    )
    return status


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


async def poll_downloads(fs: Any, ff: FlacfetchClient, gcs: Any, now: Now) -> tuple[int, int, Counter[str]]:
    """Step 1; returns ``(polled, fetched, failures)`` (``failures``: count per error string)."""
    docs = await fs.query_documents(FETCH, filters=[("status", "==", "downloading")])
    polled = fetched = 0
    failures: Counter[str] = Counter()
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
            error = st.get("error") or f"flacfetch {st.get('status')} without a GCS file"
            await _retry_or_fail(fs, doc, error, now)
            failures[error] += 1
        else:
            started = doc.get("download_started_at") or doc["updated_at"]
            if (_stamp(now) - started).total_seconds() > DOWNLOAD_TIMEOUT_S:
                error = f"flacfetch download still {st.get('status')} after {DOWNLOAD_TIMEOUT_S // 3600} h"
                await _retry_or_fail(fs, doc, error, now)
                failures[error] += 1
    return polled, fetched, failures


def _unfetchable(doc: dict[str, Any]) -> str | None:
    if not doc.get("spotify_id"):
        return NO_SPOTIFY_ID
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


def _needs_track(doc: dict[str, Any]) -> bool:
    return not doc.get("spotify_id") or not doc.get("duration_ms")


async def resolve_missing(
    fs: Any, docs: list[dict[str, Any]], resolve: Resolver | None, now: Now
) -> tuple[int, set[str]]:
    """Step 2a: fill ``spotify_id`` / ``duration_ms`` on queued ``docs`` that lack them, with one
    ``resolve`` call (none when nothing is missing). A doc that already has a ``spotify_id`` only
    takes the duration of that same track. Returns ``(resolved, held)``: ``held`` are the ids left
    queued because the lookup is unavailable this run (a WARNING). ``docs`` are updated in place."""
    missing = [d for d in docs if _needs_track(d)]
    if not missing or resolve is None:
        return 0, set()
    try:
        tracks = resolve(sorted({d["song_key"] for d in missing}))
    except _RESOLVE_UNAVAILABLE as e:
        logger.warning(
            "voice pool: can't resolve Spotify ids for %d queued songs (%s: %s); they stay queued for the next run",
            len(missing), type(e).__name__, e,
        )  # fmt: skip
        return 0, {d["id"] for d in missing}
    resolved = 0
    for d in missing:
        sid, dur = tracks.get(d["song_key"], (None, None))
        if not sid or (d.get("spotify_id") and d["spotify_id"] != sid):
            continue
        data = {"spotify_id": sid, "duration_ms": dur}
        await _update(fs, d["id"], data, now)
        d.update(data)
        resolved += 1
    return resolved, set()


async def fail_unfetchable(fs: Any, docs: list[dict[str, Any]], run: str, now: Now) -> tuple[list[dict[str, Any]], int]:
    """Step 2b: mark ``failed`` every doc flacfetch can't take (no Spotify id, bad duration), with
    ONE WARNING for the run. Returns ``(fetchable docs, songs failed for no spotify id)``."""
    ok: list[dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    failed_keys: list[str] = []
    for doc in docs:
        reason = _unfetchable(doc)
        if not reason:
            ok.append(doc)
            continue
        await _update(fs, doc["id"], {"status": "failed", "error": reason}, now)
        reasons["no spotify id" if reason == NO_SPOTIFY_ID else "duration out of range"] += 1
        failed_keys.append(str(doc.get("song_key")))
    if reasons:
        logger.warning(
            "voice pool fetch: %d queued songs can't be fetched (%s); marked failed",
            sum(reasons.values()), ", ".join(f"{n} {r}" for r, n in reasons.most_common()),
            extra={"json_fields": {"run": run, "reasons": dict(reasons), "song_keys": failed_keys[:50]}},
        )  # fmt: skip
    return ok, reasons["no spotify id"]


async def submit_downloads(
    fs: Any,
    ff: FlacfetchClient,
    docs: list[dict[str, Any]],
    *,
    deadline: float,
    rng: random.Random,
    sleep: Sleep,
    now: Now,
    waiting: set[str] | None = None,
) -> tuple[int, str | None]:
    """Step 2c: submit the fetchable queued ``docs`` in order; returns ``(submitted, why_stopped)``
    (None: queue drained or deadline).

    Each song is claimed (``queued`` -> ``downloading``, transactionally) before flacfetch is
    asked, so overlapping executions never submit it twice; a refusal or outage puts it back.
    Personal-pool songs wait for the shared pool: one is submitted only when no ``shared`` /
    ``andrew_sheet`` song is still queued, neither in ``docs`` (one sent back after a rejected
    attempt) nor in ``waiting`` (ids of such songs held out of ``docs``, e.g. unresolved this run).
    """
    submitted = 0
    shared_queued = set(waiting or ()) | {d["id"] for d in docs if d.get("source") != PERSONAL}
    for doc in docs:
        personal = doc.get("source") == PERSONAL
        if personal and shared_queued:
            logger.info("voice pool: %d shared songs still queued; personal songs wait", len(shared_queued))
            break  # docs are in priority order: the rest are personal too
        if submitted:
            await sleep(rng.uniform(*SPACING_S))
        while True:
            if now() >= deadline:
                return submitted, None
            if not await claim(fs, doc["id"], now):
                logger.info("voice pool: %s was taken by another run; skipping", doc["id"])
                shared_queued.discard(doc["id"])
                break
            try:
                started = _start(ff, doc)
            except FlacfetchRejectedError as e:
                logger.warning("voice pool: %s (%s): %s", doc["id"], doc.get("song_key"), e)
                if await _retry_or_fail(fs, doc, str(e), now) == "failed":
                    shared_queued.discard(doc["id"])
                break
            except Exception as e:
                await _update(fs, doc["id"], {"status": "queued", "flacfetch_id": None}, now)
                if not isinstance(e, FlacfetchUnavailableError):
                    raise
                logger.warning("voice pool: flacfetch unavailable (%s); no more downloads this run", e)
                return submitted, e.code
            if not isinstance(started, Refused):
                await _update(
                    fs, doc["id"], {"flacfetch_id": started, "download_started_at": _stamp(now), "error": None}, now
                )
                submitted += 1
                shared_queued.discard(doc["id"])
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


class JobRunnerFailedError(RuntimeError):
    """Cloud Run job calls failed unexpectedly (not missing / forbidden / transient) during a run.

    The message is stable (one error-monitor pattern); the run id, count and first error are in
    ``log_extra`` (Cloud Logging ``json_fields``), which the CLI attaches to its ERROR."""

    MESSAGE = "voice pool fetch: Cloud Run job calls failed unexpectedly this run"

    def __init__(self, run: str, errors: list[Exception]) -> None:
        super().__init__(self.MESSAGE)
        self.log_extra = {
            "json_fields": {"run": run, "failed_calls": len(errors), "first_error": repr(errors[0])[:500]}
        }


def _job_call(what: str, fn: Callable[[], Any], default: Any, errors: list[Exception] | None = None) -> Any:
    """``fn()``; a missing / forbidden job or a transient API error is a WARNING. Any other failure
    (an API error, or a bug such as an AttributeError) is appended to ``errors`` (``run_fetch``
    raises once, after the other steps) and ``default`` is returned; without ``errors`` it propagates."""
    try:
        return fn()
    except _JOB_UNAVAILABLE as e:
        logger.warning("voice pool: can't %s yet (%s); infra pending?", what, e)
        return default
    except TRANSIENT_ERRORS as e:
        logger.warning("voice pool: %s hit a transient error (%s: %s); next run retries", what, type(e).__name__, e)
        return default
    except Exception as e:
        if errors is None:
            raise
        logger.warning("voice pool: %s failed unexpectedly (%r); carrying on with the other steps", what, e)
        errors.append(e)
        return default


async def collect_separations(
    fs: Any, gcs: Any, runner: JobRunner, now: Now, errors: list[Exception] | None = None
) -> tuple[int, int]:
    """Step 3; returns ``(separated, sent_back)`` over every run with songs still separating."""
    docs = await fs.query_documents(FETCH, filters=[("status", "==", "separating")])
    runs = sorted({d["manifest_run"] for d in docs if d.get("manifest_run")})
    if not runs:
        return 0, 0
    running = _job_call(
        f"check {separate.SEPARATE_JOB}",
        lambda: runner.is_running(separate.SEPARATE_JOB, separate.SEPARATE_REGION),
        True,
        errors,
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


async def maybe_separate(
    fs: Any, gcs: Any, runner: JobRunner, run: str, now: Now, errors: list[Exception] | None = None
) -> int:
    """Step 4; returns the number of songs sent to separation."""
    docs = await fs.query_documents(FETCH, filters=[("status", "==", "fetched")])
    if not docs:
        return 0
    docs.sort(key=lambda d: d.get("fetched_at") or d["updated_at"])
    oldest_s = (_stamp(now) - (docs[0].get("fetched_at") or docs[0]["updated_at"])).total_seconds()
    if len(docs) < SEPARATE_AT and oldest_s <= SEPARATE_AFTER_S:
        return 0
    check = f"check {separate.SEPARATE_JOB}"
    if _job_call(check, lambda: runner.is_running(separate.SEPARATE_JOB, separate.SEPARATE_REGION), True, errors):
        logger.info("voice pool: %d fetched, separation already running; next run", len(docs))
        return 0
    # submit_batch reverts its claims (back to ``fetched``) before any of these reach us.
    try:
        _, songs = await separate.submit_batch(fs, gcs, runner, docs, run, now=now)
    except _JOB_UNAVAILABLE as e:
        logger.warning("voice pool: can't run %s yet (%s); infra pending?", separate.SEPARATE_JOB, e)
        return 0
    except TRANSIENT_ERRORS as e:
        logger.warning("voice pool: separation hand-off hit a transient error (%s: %s); next run retries",
                       type(e).__name__, e)  # fmt: skip
        return 0
    except Exception as e:
        if errors is None:
            raise
        logger.warning("voice pool: submitting separation failed unexpectedly (%r); carrying on", e)
        errors.append(e)
        return 0
    return songs


async def maybe_analyse(fs: Any, runner: JobRunner, errors: list[Exception] | None = None) -> bool:
    """Step 5; whether an analyse execution was started."""
    if not await fs.query_documents(FETCH, filters=[("status", "==", "separated")], limit=1):
        return False
    if _job_call(f"check {ANALYSE_JOB}", lambda: runner.is_running(ANALYSE_JOB, ANALYSE_REGION), True, errors):
        return False
    return bool(
        _job_call(f"run {ANALYSE_JOB}", lambda: runner.run(ANALYSE_JOB, ANALYSE_REGION, args=["analyse"]), None, errors)
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
    resolve: Resolver | None = None,
) -> dict[str, Any]:
    """One scheduler run (see module doc). Returns ``{run, polled, fetched, resolved, unresolved,
    submitted, refused_code, separated, requeued, separating, analyse_started}``; ``resolve`` is the
    Spotify id lookup (None: songs without one fail)."""
    deadline = now() + deadline_s
    polled, fetched, failures = await poll_downloads(fs, ff, gcs, now)
    downloads_failing = sum(failures.values()) >= SYSTEMATIC_DOWNLOAD_FAILURES and fetched == 0
    submitted: int
    refused_code: str | None
    if downloads_failing:
        top_error, top_count = failures.most_common(1)[0]
        logger.error(
            "voice pool fetch: downloads are failing systematically; no new downloads this run",
            extra={"json_fields": {"run": run, "failures": sum(failures.values()), "fetched": fetched,
                                   "top_error": top_error[:500], "top_error_count": top_count}},
        )  # fmt: skip
        submitted, refused_code = 0, "downloads_failing"
        resolved = unresolved = 0
    else:
        queued = await queue.next_queued(fs, QUEUE_SCAN)
        resolved, held = await resolve_missing(fs, queued, resolve, now)
        fetchable, unresolved = await fail_unfetchable(fs, [d for d in queued if d["id"] not in held], run, now)
        waiting = {d["id"] for d in queued if d["id"] in held and d.get("source") != PERSONAL}
        submitted, refused_code = await submit_downloads(
            fs, ff, fetchable, deadline=deadline, rng=rng, sleep=sleep, now=now, waiting=waiting
        )
    errors: list[Exception] = []
    separated, requeued = await collect_separations(fs, gcs, runner, now, errors)
    separating = await maybe_separate(fs, gcs, runner, run, now, errors)
    analyse_started = await maybe_analyse(fs, runner, errors)
    out = {
        "run": run,
        "polled": polled,
        "fetched": fetched,
        "resolved": resolved,
        "unresolved": unresolved,
        "submitted": submitted,
        "refused_code": refused_code,
        "separated": separated,
        "requeued": requeued,
        "separating": separating,
        "analyse_started": analyse_started,
    }
    logger.info("voice pool fetch %s: %s", run, out)
    if errors and downloads_failing:  # this run already logged its one ERROR
        logger.warning(
            "voice pool fetch %s: %d Cloud Run job call(s) also failed; first: %r", run, len(errors), errors[0]
        )
    elif errors:
        # One failure for the run (the CLI logs it as this run's one ERROR and exits 1), raised
        # only after the queue, downloads and the other hand-offs have had their turn.
        raise JobRunnerFailedError(run, errors) from errors[0]
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
