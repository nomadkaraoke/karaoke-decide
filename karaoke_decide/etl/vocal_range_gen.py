"""vocal-range-gen: pyin vocal-range analysis of karaoke-gen jobs' separated vocals stems.

On-demand Cloud Run job (10 tasks, no scheduler). For every gen job that still has
``jobs/{id}/stems/vocals_clean.flac`` it runs ``analyze_vocals`` restricted to the
word-timed windows from ``lyrics/corrections_updated.json`` (else ``corrections.json``),
one pass per singer for duets. Output is the highest-trust "measured" tier:

    gs://nomadkaraoke-musicbrainz-data/vocal-range/gen_measured/<run>/part-<task>.csv
    source_id,artist,title,lead_part,low_midi,high_midi,p10,p50,p90,voiced_seconds

There is NO re-separation: gen purges stems 30 days after a job completes and its
audio-separator service is disabled, so jobs without a stem are skipped (counted in one
INFO summary line, never logged per job). Resumable: job ids already present in any
earlier ``gen_measured/*/part-*.csv`` are skipped, and each task checkpoints its own
part file as it goes. Jobs that yield no stats (<20 s voiced) write no row and are
retried by later runs.

    python -m karaoke_decide.etl.vocal_range_gen run [--limit N] [--run ID]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import logging
import os
import re
import sys
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from karaoke_decide.vocal_range.pitch import analyze_vocals

logger = logging.getLogger("vocal_range_gen")

PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "nomadkaraoke")
GEN_BUCKET = os.environ.get("VOCAL_RANGE_GEN_BUCKET", "karaoke-gen-storage-nomadkaraoke")
OUT_BUCKET = os.environ.get("VOCAL_RANGE_OUT_BUCKET", "nomadkaraoke-musicbrainz-data")
OUT_PREFIX = "vocal-range/gen_measured/"
STEM_GLOB = "jobs/*/stems/vocals_clean.flac"
CORRECTIONS_GLOBS = ("jobs/*/lyrics/corrections.json", "jobs/*/lyrics/corrections_updated.json")
COLUMNS = ["source_id", "artist", "title", "lead_part", "low_midi", "high_midi", "p10", "p50", "p90", "voiced_seconds"]
MERGE_GAP_S = 0.25
CHECKPOINT_EVERY = 25

_JOB_RE = re.compile(r"^jobs/([^/]+)/")


def shard(job_ids: list[str], index: int, count: int) -> list[str]:
    """The ids belonging to task ``index`` of ``count`` (stable ``sha1(id) % count``)."""
    return [j for j in job_ids if int(hashlib.sha1(j.encode()).hexdigest(), 16) % count == index]


def _merge(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for s, e in sorted(spans):
        if out and s - out[-1][1] <= MERGE_GAP_S:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def select_word_windows(corrections: dict[str, Any]) -> dict[str, list[tuple[float, float]]]:
    """Word-timed windows per singer id (``"1"``, ``"2"``...), or ``"all"`` when no word
    carries a singer. Accepts ``{corrections:{corrected_segments}}`` and
    ``{corrected_segments}``."""
    body = corrections.get("corrections") if isinstance(corrections.get("corrections"), dict) else corrections
    segments = (body or {}).get("corrected_segments") or []
    tagged: list[tuple[Any, float, float]] = []
    for seg in segments:
        for w in seg.get("words") or []:
            s, e = w.get("start_time"), w.get("end_time")
            if s is None or e is None or e <= s:
                continue
            tagged.append((w.get("singer") or seg.get("singer"), float(s), float(e)))
    by: dict[str, list[tuple[float, float]]] = {}
    if not any(t[0] for t in tagged):
        by["all"] = [(s, e) for _, s, e in tagged]
    else:
        for singer, s, e in tagged:
            if singer:
                by.setdefault(str(singer), []).append((s, e))
    return {k: _merge(v) for k, v in by.items() if v}


def load_audio(blob: Any) -> tuple[Any, int]:
    """Decode a GCS audio blob to (mono float32 samples, sample rate)."""
    import soundfile as sf

    with blob.open("rb") as fh:
        y, sr = sf.read(fh, dtype="float32", always_2d=False)
    return y, int(sr)


def _fmt(v: float) -> str:
    return repr(round(float(v), 3))


def _csv_bytes(rows: list[dict[str, str]]) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode()


def _read_rows(blob: Any) -> list[dict[str, str]]:
    with blob.open("rb") as fh:
        return list(csv.DictReader(io.StringIO(fh.read().decode())))


def _job_ids(bucket: Any, glob: str) -> dict[str, str]:
    """job id -> object name for every object matching ``glob``."""
    out = {}
    for blob in bucket.list_blobs(prefix="jobs/", match_glob=glob):
        m = _JOB_RE.match(blob.name)
        if m:
            out[m.group(1)] = blob.name
    return out


def run(
    gen: Any,
    out: Any,
    get_job: Callable[[str], dict[str, Any] | None],
    *,
    run_id: str,
    task_index: int,
    task_count: int,
    limit: int | None = None,
) -> dict[str, int]:
    """Analyse this task's share of gen jobs; returns the summary counters."""
    own_name = f"{OUT_PREFIX}{run_id}/part-{task_index}.csv"
    done: set[str] = set()
    own_rows: list[dict[str, str]] = []
    for blob in out.list_blobs(prefix=OUT_PREFIX):
        if not re.search(r"/part-\d+\.csv$", blob.name):
            continue
        rows = _read_rows(blob)
        done.update(r["source_id"] for r in rows)
        if blob.name == own_name:
            own_rows = rows

    corr_names: dict[str, str] = {}
    for g in CORRECTIONS_GLOBS:  # corrections_updated.json (listed last) wins
        corr_names.update(_job_ids(gen, g))
    stems = _job_ids(gen, STEM_GLOB)
    mine = shard(sorted(corr_names), task_index, task_count)

    counts: Counter[str] = Counter(jobs_listed=len(mine))
    todo: list[str] = []
    for jid in mine:
        if jid not in stems:
            counts["skipped_no_stem"] += 1
        elif jid in done:
            counts["skipped_done"] += 1
        else:
            todo.append(jid)
    counts["with_stem"] = len(mine) - counts["skipped_no_stem"]
    if limit is not None:
        todo = todo[:limit]

    rows_out = list(own_rows)
    processed = 0
    for jid in todo:
        job = get_job(jid)
        if not job:
            counts["skipped_no_job"] += 1
            continue
        if job.get("tenant_id"):
            counts["skipped_tenant"] += 1
            continue
        if job.get("outputs_deleted_at"):
            counts["skipped_deleted"] += 1
            continue
        counts["attempted"] += 1
        try:
            with gen.blob(corr_names[jid]).open("rb") as fh:
                windows = select_word_windows(json.loads(fh.read()))
            if not windows:
                counts["skipped_no_windows"] += 1
                continue
            y, sr = load_audio(gen.blob(stems[jid]))
            wrote = False
            for part, spans in windows.items():
                stats = analyze_vocals(y, sr, spans)
                if stats is None:
                    continue
                wrote = True
                rows_out.append(
                    {
                        "source_id": jid,
                        "artist": job.get("artist") or "",
                        "title": job.get("title") or "",
                        "lead_part": part,
                        "low_midi": _fmt(stats.low),
                        "high_midi": _fmt(stats.high),
                        "p10": _fmt(stats.p10),
                        "p50": _fmt(stats.p50),
                        "p90": _fmt(stats.p90),
                        "voiced_seconds": _fmt(stats.voiced_s),
                    }
                )
            counts["analysed" if wrote else "skipped_too_little_voice"] += 1
        except Exception as exc:  # noqa: BLE001 - one bad job must not stop the shard
            counts["failed"] += 1
            logger.warning("job %s failed: %s: %s", jid, type(exc).__name__, exc)
        processed += 1
        if processed % CHECKPOINT_EVERY == 0:
            _write(out, own_name, rows_out)
    _write(out, own_name, rows_out)

    summary = {k: counts[k] for k in sorted(counts)}
    for k in ("analysed", "failed", "skipped_no_stem", "skipped_done"):
        summary.setdefault(k, 0)
    line = (
        f"task {task_index}/{task_count}: "
        + " ".join(f"{k}={v}" for k, v in summary.items())
        + f" rows={len(rows_out)}"
    )
    if counts["attempted"] and counts["failed"] == counts["attempted"]:
        logger.error("vocal-range-gen: every job failed. %s", line)
    else:
        logger.info("vocal-range-gen done. %s", line)
    return summary


def _write(out: Any, name: str, rows: list[dict[str, str]]) -> None:
    with out.blob(name).open("wb") as fh:
        fh.write(_csv_bytes(rows))


def resolve_run_id(explicit: str | None = None) -> str:
    """--run, else VOCAL_RANGE_RUN, else CLOUD_RUN_EXECUTION (shared by all tasks of one
    execution), else a UTC timestamp."""
    return (
        explicit
        or os.environ.get("VOCAL_RANGE_RUN")
        or os.environ.get("CLOUD_RUN_EXECUTION")
        or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    )


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="vocal_range_gen")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--limit", type=int, default=None)
    r.add_argument("--run", dest="run_id", default=None)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from google.cloud import firestore, storage  # type: ignore[attr-defined]

    client = storage.Client(project=PROJECT_ID)
    db = firestore.Client(project=PROJECT_ID)

    def get_job(jid: str) -> dict[str, Any] | None:
        snap: Any = db.collection("jobs").document(jid).get()
        return snap.to_dict() if snap.exists else None

    run_id = resolve_run_id(args.run_id)
    run(
        client.bucket(GEN_BUCKET),
        client.bucket(OUT_BUCKET),
        get_job,
        run_id=run_id,
        task_index=int(os.environ.get("CLOUD_RUN_TASK_INDEX", "0")),
        task_count=int(os.environ.get("CLOUD_RUN_TASK_COUNT", "1")),
        limit=args.limit,
    )
    return 0


if __name__ == "__main__":
    sys.exit(_main())
