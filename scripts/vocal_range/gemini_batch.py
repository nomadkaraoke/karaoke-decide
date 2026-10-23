#!/usr/bin/env python3
"""One-off Gemini Flash Batch run: estimated vocal ranges for top karaoke artists + songs.

    python scripts/vocal_range/gemini_batch.py prepare --artists 20000 --songs 50000
    python scripts/vocal_range/gemini_batch.py estimate        # count_tokens on 200 samples -> cost
    python scripts/vocal_range/gemini_batch.py submit          # SPENDS MONEY (only after explicit OK)
    python scripts/vocal_range/gemini_batch.py poll
    python scripts/vocal_range/gemini_batch.py collect

Work files go in --work-dir or $VR_GEMINI_DIR (required; refused if inside the repo).
Uses GOOGLE_AI_STUDIO_KEY (Gemini Developer API; never Vertex, never GEMINI_API_KEY).
BigQuery is read-only via ADC and every query has maximum_bytes_billed.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import random
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from karaoke_decide.catalog.bq_source import _NORM_SQL  # noqa: E402
from karaoke_decide.catalog.keys import song_key  # noqa: E402
from karaoke_decide.vocal_range.gemini import (  # noqa: E402
    BUDGET_USD,
    DEFAULT_MODEL,
    PRICES_PER_M,
    SCHEMA_TOKENS,
    build_request,
    check_estimate,
    classify_result,
    estimate_cost,
    requests_fingerprint,
    resolve_work_dir,
)

P = "nomadkaraoke.karaoke_decide"
BQ_CAP = 10 * 2**30  # these queries are far below this; fail loudly if not
SAMPLE = 200
OUT_COLS = "kind,key,artist,title,pop_rank,low_midi,high_midi,p10,p50,p90,voice_type,confidence".split(",")

ARTISTS_SQL = f"""
WITH kn AS (SELECT DISTINCT {_NORM_SQL.format(col="Artist")} AS n FROM `{P}.karaokenerds_raw` WHERE Artist IS NOT NULL)
SELECT a.artist_mbid AS mbid, a.artist_name AS name, a.name_normalized, lp.listeners
FROM `{P}.lb_artist_popularity` lp
JOIN `{P}.mb_artists_normalized` a ON a.artist_mbid = lp.artist_mbid
WHERE lp.stats_range = 'all_time' AND a.artist_name IS NOT NULL
  AND a.name_normalized IN (SELECT n FROM kn)
QUALIFY ROW_NUMBER() OVER (PARTITION BY a.name_normalized ORDER BY lp.listeners DESC, a.artist_mbid) = 1
ORDER BY lp.listeners DESC
LIMIT @n
"""

SONGS_SQL = f"""
SELECT k.Id AS karaoke_id, k.Artist AS artist, k.Title AS title, MAX(p.listeners) AS listeners
FROM `{P}.karaoke_recording_links` l
JOIN `{P}.karaokenerds_raw` k ON k.Id = l.karaoke_id
JOIN `{P}.lb_recording_popularity` p ON p.recording_mbid = l.recording_mbid AND p.stats_range = 'all_time'
WHERE k.Artist IS NOT NULL AND k.Title IS NOT NULL
GROUP BY k.Id, k.Artist, k.Title
ORDER BY listeners DESC, karaoke_id
"""


def work_dir(args) -> Path:
    try:
        d = resolve_work_dir(args.work_dir, dict(os.environ), REPO)
    except ValueError as e:
        sys.exit(str(e))
    d.mkdir(parents=True, exist_ok=True)
    return d


def genai_client():
    key = os.environ.get("GOOGLE_AI_STUDIO_KEY")
    if not key:
        sys.exit("GOOGLE_AI_STUDIO_KEY is not set")
    from google import genai

    return genai.Client(api_key=key)


def bq_rows(sql: str, params=None):
    from google.cloud import bigquery

    bq = bigquery.Client(project="nomadkaraoke")
    dry = bq.query(
        sql,
        job_config=bigquery.QueryJobConfig(dry_run=True, query_parameters=params or [], maximum_bytes_billed=BQ_CAP),
    )
    print(f"  dry run: {dry.total_bytes_processed / 2**30:.2f} GiB", file=sys.stderr)
    job = bq.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params or [], maximum_bytes_billed=BQ_CAP))
    rows = [dict(r) for r in job.result()]
    print(f"  billed: {(job.total_bytes_billed or 0) / 2**30:.2f} GiB, {len(rows)} rows", file=sys.stderr)
    return rows


def cmd_prepare(args):
    from google.cloud import bigquery

    d = work_dir(args)
    artists = bq_rows(ARTISTS_SQL, [bigquery.ScalarQueryParameter("n", "INT64", args.artists)])
    raw_songs = bq_rows(SONGS_SQL)
    seen: set[str] = set()
    songs = []
    for r in raw_songs:  # already ordered by listeners desc -> first per song_key is the max
        sk = song_key(r["artist"], r["title"])
        if sk in seen:
            continue
        seen.add(sk)
        songs.append(r)
        if len(songs) >= args.songs:
            break
    meta = []
    with open(d / "requests.jsonl", "w", encoding="utf-8") as f:
        for i, a in enumerate(artists, 1):
            key = f"artist:{a['mbid']}"
            f.write(json.dumps(build_request("artist", key, a["name"], None), ensure_ascii=False) + "\n")
            meta.append({"kind": "artist", "key": key, "artist": a["name"], "title": "", "pop_rank": i})
        for i, s in enumerate(songs, 1):
            key = f"song:{s['karaoke_id']}"
            f.write(json.dumps(build_request("song", key, s["artist"], s["title"]), ensure_ascii=False) + "\n")
            meta.append({"kind": "song", "key": key, "artist": s["artist"], "title": s["title"], "pop_rank": i})
    with open(d / "meta.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["kind", "key", "artist", "title", "pop_rank"])
        w.writeheader()
        w.writerows(meta)
    print(f"prepared {len(artists)} artists + {len(songs)} songs = {len(meta)} requests in {d}")


def _estimate_one(client, model, line):
    # count_tokens rejects system_instruction on the Developer API: count it as leading content instead
    # (same token total). The response schema is not covered by count_tokens on this API, so SCHEMA_TOKENS is added.
    req = line["request"]
    text = req["system_instruction"]["parts"][0]["text"] + "\n" + req["contents"][0]["parts"][0]["text"]
    return client.models.count_tokens(model=model, contents=text).total_tokens


def cmd_estimate(args):
    d = work_dir(args)
    model = args.model
    if model not in PRICES_PER_M:
        sys.exit(f"model {model!r} has no verified price in PRICES_PER_M")
    lines = [json.loads(x) for x in open(d / "requests.jsonl", encoding="utf-8")]
    random.Random(0).shuffle(lines)
    sample = lines[:SAMPLE]
    client = genai_client()
    toks = [_estimate_one(client, model, ln) for ln in sample]
    avg_in = sum(toks) / len(toks) + SCHEMA_TOKENS
    # Output: JSON with 4 notes, voice_type, confidence, unknown -> measured from the schema's typical answer.
    typical = json.dumps(
        {
            "low_note": "A#2",
            "high_note": "C#5",
            "typical_low": "D3",
            "typical_high": "A4",
            "voice_type": "baritone",
            "confidence": 0.65,
            "unknown": False,
        },
        separators=(",", ":"),
    )
    avg_out = float(_count_text(client, model, typical))
    cost = estimate_cost(len(lines), avg_in, avg_out, model)
    est = {
        "model": model,
        "n": len(lines),
        "requests": requests_fingerprint(d / "requests.jsonl"),
        "schema_tokens": SCHEMA_TOKENS,
        "avg_in": avg_in,
        "avg_out": avg_out,
        "prices_per_m": PRICES_PER_M[model],
        "cost": cost,
        "cost_x1_5": cost * 1.5,
    }
    (d / "estimate.json").write_text(json.dumps(est, indent=2))
    print(json.dumps(est, indent=2))
    if cost * 1.5 > BUDGET_USD:
        print(f"cost x 1.5 = ${cost * 1.5:.2f} exceeds budget ${BUDGET_USD:.2f}", file=sys.stderr)
        sys.exit(2)


def _count_text(client, model, text):
    return client.models.count_tokens(model=model, contents=text).total_tokens


def cmd_submit(args):
    d = work_dir(args)
    try:
        est = check_estimate(d / "estimate.json", d / "requests.jsonl", args.model)
    except ValueError as e:
        sys.exit(str(e))
    if est["cost"] * 1.5 > BUDGET_USD:
        sys.exit(f"estimate x 1.5 = ${est['cost'] * 1.5:.2f} exceeds budget ${BUDGET_USD:.2f}")
    from google.genai import types

    client = genai_client()
    f = client.files.upload(
        file=str(d / "requests.jsonl"),
        config=types.UploadFileConfig(display_name="vocal-range-requests", mime_type="jsonl"),
    )
    job = client.batches.create(model=args.model, src=f.name, config={"display_name": "vocal-range"})
    (d / "job.json").write_text(json.dumps({"name": job.name, "model": args.model, "src": f.name}))
    print("submitted", job.name)


def cmd_poll(args):
    d = work_dir(args)
    info = json.loads((d / "job.json").read_text())
    client = genai_client()
    while True:
        job = client.batches.get(name=info["name"])
        state = job.state.name if hasattr(job.state, "name") else str(job.state)
        print(time.strftime("%H:%M:%S"), state, flush=True)
        if (
            state in ("JOB_STATE_SUCCEEDED", "JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED")
            or args.once
        ):
            return
        time.sleep(300)


def cmd_collect(args):
    d = work_dir(args)
    info = json.loads((d / "job.json").read_text())
    client = genai_client()
    job = client.batches.get(name=info["name"])
    if not job.dest or not job.dest.file_name:
        sys.exit(f"job not finished: {job.state}")
    raw = client.files.download(file=job.dest.file_name)
    (d / "results.jsonl").write_bytes(raw)
    meta = {r["key"]: r for r in csv.DictReader(open(d / "meta.csv", encoding="utf-8"))}
    out = {"song": [], "artist": []}
    counts = {"ok": 0, "unknown": 0, "api_error": 0, "unparseable": 0}
    for line in raw.decode("utf-8").splitlines():
        r = json.loads(line)
        m = meta.get(r.get("key"))
        if not m:
            continue
        status, p = classify_result(r)
        counts[status] += 1
        if p is not None:
            out[m["kind"]].append({**{k: m[k] for k in ("kind", "key", "artist", "title", "pop_rank")}, **p})
    for kind in ("song", "artist"):
        path = REPO / "data" / "vocal_range" / f"gemini_{kind}s.csv.gz"
        with gzip.open(path, "wt", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=OUT_COLS)
            w.writeheader()
            w.writerows(out[kind])
        print(path, len(out[kind]), f"{path.stat().st_size / 1e6:.2f} MB")
    total = max(sum(counts.values()), 1)
    print(
        f"results: {counts} (unknown rate {counts['unknown'] / total:.1%}, errors+unparseable {(counts['api_error'] + counts['unparseable']) / total:.1%})"
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work-dir")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--artists", type=int, default=20000)
    p.add_argument("--songs", type=int, default=50000)
    sub.add_parser("estimate")
    sub.add_parser("submit")
    q = sub.add_parser("poll")
    q.add_argument("--once", action="store_true")
    sub.add_parser("collect")
    args = ap.parse_args()
    {"prepare": cmd_prepare, "estimate": cmd_estimate, "submit": cmd_submit, "poll": cmd_poll, "collect": cmd_collect}[
        args.cmd
    ](args)


if __name__ == "__main__":
    main()
