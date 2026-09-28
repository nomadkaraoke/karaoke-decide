"""ListenBrainz refresh: latest statistics dump -> BigQuery popularity tables.

Runs as the ``lb-refresh`` Cloud Run Job (Cloud Scheduler, twice a week; a
new full export appears on the 1st and 15th). Steps:

1. CHECK     Find the newest full export whose statistics dump + .sha256 are
             both uploaded; exit if that dump is already published.
2. EXTRACT   Stream the ~22 GB ``.tar.zst`` over HTTPS, verify its SHA256,
             decompress with zstd and copy only the ``artists_*`` and
             ``recordings_*`` JSONL members to
             ``gs://nomadkaraoke-musicbrainz-data/staging/listenbrainz/<dump_id>/``.
             Nothing is written to local disk.
3. LOAD      BigQuery JSON load jobs (free) into
             ``listenbrainz_staging.raw_<entity>_<range>`` (nested, minimal schema).
4. BUILD     SQL models (listenbrainz_sql.MODELS) -> ``listenbrainz_staging.<table>``.
5. VALIDATE  Row-count bounds vs prod + canary checks. Any failure stops the
             run before prod is touched.
6. PUBLISH   Copy jobs staging -> ``karaoke_decide``, label each table with
             ``lb_dump=<dump_id>``, log to ``karaoke_decide.lb_refresh_log``,
             delete staging.

Usage:
    python -m karaoke_decide.etl.listenbrainz_refresh run [--dump-id ID] [--force] [--no-publish]
    python -m karaoke_decide.etl.listenbrainz_refresh run --skip-extract   # rebuild from loaded raw tables
    python -m karaoke_decide.etl.listenbrainz_refresh run --reuse-gcs      # reload JSONL already in GCS staging
    python -m karaoke_decide.etl.listenbrainz_refresh publish --dump-id ID # validate + publish existing staging
    python -m karaoke_decide.etl.listenbrainz_refresh status

``--dump-id`` is the part of the export directory name between
``listenbrainz-dump-`` and ``-full``, e.g. ``2663-20260915-000002``.
"""

from __future__ import annotations

import argparse
import logging
import re
import shutil
import sys
import traceback
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import IO

import httpx
from google.cloud import (  # type: ignore[attr-defined]  # storage has no stubs
    bigquery,
    storage,
)

from karaoke_decide.etl import listenbrainz_sql as sql
from karaoke_decide.etl import refresh_common as common
from karaoke_decide.etl.refresh_common import CHUNK, GIB, RefreshError, RunState

logger = logging.getLogger("lb_refresh")

DUMP_BASE_URL = "https://data.metabrainz.org/pub/musicbrainz/listenbrainz/fullexport"
GCS_BUCKET = "nomadkaraoke-musicbrainz-data"
GCS_STAGING_PREFIX = "staging/listenbrainz"
LOG_TABLE = f"{sql.P}.lb_refresh_log"
META_FILES = ("SCHEMA_SEQUENCE", "TIMESTAMP")
# Full exports land every ~2 weeks; allow one missed export before alerting.
STALE_AFTER_DAYS = 35
MAX_BYTES_MODEL = 100 * GIB
MAX_BYTES_CHECK = 20 * GIB

DUMP_DIR_RE = re.compile(r'href="listenbrainz-dump-(\d+-\d{8}-\d{6})-full/"')
STATS_FILE_RE = re.compile(r'href="(listenbrainz-statistics-dump-\d{8}-\d{6}\.tar\.zst)"')
SHA256_RE = re.compile(r"\b([0-9a-fA-F]{64})\b")
STATS_MEMBER_DIR = "/lbdump/statistics/"


@dataclass(frozen=True)
class StatsDump:
    dump_id: str
    url: str
    sha256: str


# --------------------------------------------------------------------------
# Dump discovery + streaming extract
# --------------------------------------------------------------------------


def dump_dir_url(dump_id: str) -> str:
    return f"{DUMP_BASE_URL}/listenbrainz-dump-{dump_id}-full"


def parse_dump_ids(listing_html: str) -> list[str]:
    """Export ids from the fullexport/ index, newest (highest dump number) first."""
    ids = set(DUMP_DIR_RE.findall(listing_html))
    return sorted(ids, key=lambda d: int(d.split("-", 1)[0]), reverse=True)


def find_stats_dump(http: httpx.Client, dump_id: str) -> StatsDump | None:
    """The export's statistics archive, or None if it isn't fully uploaded yet."""
    base = dump_dir_url(dump_id)
    resp = http.get(f"{base}/")
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    match = STATS_FILE_RE.search(resp.text)
    if match is None or f'href="{match.group(1)}.sha256"' not in resp.text:
        return None
    filename = match.group(1)
    sha_resp = http.get(f"{base}/{filename}.sha256")
    sha_resp.raise_for_status()
    sha = SHA256_RE.search(sha_resp.text)
    if sha is None:
        raise RefreshError(f"Unparseable checksum file for {filename}: {sha_resp.text[:200]!r}")
    return StatsDump(dump_id=dump_id, url=f"{base}/{filename}", sha256=sha.group(1).lower())


def resolve_dump(http: httpx.Client, dump_id: str | None = None) -> StatsDump:
    """The requested export, or the newest one whose statistics dump is complete."""
    if dump_id:
        found = find_stats_dump(http, dump_id)
        if found is None:
            raise RefreshError(f"No complete statistics dump in export {dump_id}")
        return found
    resp = http.get(f"{DUMP_BASE_URL}/")
    resp.raise_for_status()
    candidates = parse_dump_ids(resp.text)
    for candidate in candidates:
        found = find_stats_dump(http, candidate)
        if found is not None:
            return found
        logger.info(f"Export {candidate} has no complete statistics dump yet; trying the previous one")
    raise RefreshError(f"No complete statistics dump among exports {candidates}")


def member_table_name(member_name: str) -> str | None:
    """``<root>/lbdump/statistics/artists_all_time.jsonl`` -> ``artists_all_time``."""
    if STATS_MEMBER_DIR not in member_name or not member_name.endswith(".jsonl"):
        return None
    name = member_name.rsplit("/", 1)[-1][: -len(".jsonl")]
    return name or None


def gcs_blob_name(dump_id: str, table: str) -> str:
    return f"{GCS_STAGING_PREFIX}/{dump_id}/{table}.jsonl"


def gcs_uri(dump_id: str, table: str) -> str:
    return f"gs://{GCS_BUCKET}/{gcs_blob_name(dump_id, table)}"


def extract_dump_to_gcs(
    http: httpx.Client,
    gcs: storage.Client,
    state: RunState,
    dump: StatsDump,
    decompress_cmd: list[str] | None = None,
) -> None:
    bucket = gcs.bucket(GCS_BUCKET)

    def upload(table: str, fileobj: IO[bytes]) -> None:
        blob = bucket.blob(gcs_blob_name(state.dump_id, table))
        with blob.open("wb", chunk_size=64 * 1024 * 1024, content_type="application/x-ndjson") as out:
            shutil.copyfileobj(fileobj, out, CHUNK)

    logger.info(f"Streaming {dump.url}")
    timeout = httpx.Timeout(60.0, read=600.0)
    with http.stream("GET", dump.url, timeout=timeout, follow_redirects=True) as resp:
        resp.raise_for_status()
        meta = common.extract_members(
            resp.iter_bytes(CHUNK),
            set(sql.RAW_FILES),
            upload,
            dump.sha256,
            decompress_cmd=decompress_cmd or ["zstd", "-dc"],
            table_of=member_table_name,
            meta_files=META_FILES,
        )
    state.schema_sequence = meta.get("SCHEMA_SEQUENCE")
    logger.info(f"Extracted {len(sql.RAW_FILES)} statistics files (meta={meta})")


# --------------------------------------------------------------------------
# BigQuery load / build / validate / publish
# --------------------------------------------------------------------------


def raw_load_config(entity: str) -> bigquery.LoadJobConfig:
    return bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        schema=sql.raw_schema(entity),
        ignore_unknown_values=True,
        max_bad_records=0,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )


def load_raw_tables(bq: bigquery.Client, dump_id: str) -> None:
    jobs = []
    for entity in sql.ENTITIES:
        for rng in sql.STATS_RANGES:
            table = f"{entity}_{rng}"
            dest = f"{sql.S}.raw_{table}"
            job = bq.load_table_from_uri(gcs_uri(dump_id, table), dest, job_config=raw_load_config(entity))
            jobs.append((dest, job))
    for dest, job in jobs:
        try:
            job.result()
        except Exception as e:
            raise RefreshError(f"Load into {dest} failed: {e}") from e
        logger.info(f"Loaded {dest}: {bq.get_table(dest).num_rows:,} rows")


def build_models(bq: bigquery.Client) -> None:
    for name in sql.MODEL_ORDER:
        start = datetime.now(UTC)
        job = bq.query(sql.MODELS[name], job_config=bigquery.QueryJobConfig(maximum_bytes_billed=MAX_BYTES_MODEL))
        try:
            job.result()
        except Exception as e:
            raise RefreshError(f"Model {name} failed: {e}") from e
        billed = (job.total_bytes_billed or 0) / GIB
        secs = (datetime.now(UTC) - start).total_seconds()
        logger.info(
            f"Built {sql.S}.{name}: {bq.get_table(f'{sql.S}.{name}').num_rows:,} rows, {billed:.2f} GiB billed, {secs:.0f}s"
        )


def check_row_counts(staging: dict[str, int | None], prod: dict[str, int | None]) -> list[str]:
    return common.check_row_counts(sql.ROW_COUNT_BOUNDS, staging, prod)


def validate(bq: bigquery.Client, state: RunState) -> None:
    staging = {name: common.num_rows(bq, f"{sql.S}.{name}") for name in sql.MODEL_ORDER}
    prod = {name: common.num_rows(bq, f"{sql.P}.{name}") for name in sql.MODEL_ORDER}
    state.row_counts = {k: v for k, v in staging.items() if v is not None}
    for name in sql.MODEL_ORDER:
        logger.info(f"Rows {name}: staging={staging[name]} prod={prod[name]}")

    failures = check_row_counts(staging, prod)
    failures += common.run_canaries(bq, sql.CANARY_CHECKS, MAX_BYTES_CHECK)
    if failures:
        raise RefreshError("Validation failed, prod untouched: " + "; ".join(failures))


def publish(bq: bigquery.Client, state: RunState) -> None:
    common.publish_tables(bq, sql.MODEL_ORDER, sql.S, sql.P, "lb_dump", state.dump_id)


def cleanup_staging(bq: bigquery.Client, gcs: storage.Client, dump_id: str) -> None:
    for table in sql.RAW_FILES:
        bq.delete_table(f"{sql.S}.raw_{table}", not_found_ok=True)
    for name in sql.MODEL_ORDER:
        bq.delete_table(f"{sql.S}.{name}", not_found_ok=True)
    blobs = list(gcs.bucket(GCS_BUCKET).list_blobs(prefix=f"{GCS_STAGING_PREFIX}/{dump_id}/"))
    for blob in blobs:
        blob.delete()
    logger.info(f"Cleaned up staging ({len(blobs)} GCS objects)")


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------


def _log(bq: bigquery.Client) -> common.RunLog:
    return common.RunLog(bq, LOG_TABLE)


def run(
    bq: bigquery.Client,
    gcs: storage.Client,
    http: httpx.Client,
    dump_id: str | None = None,
    force: bool = False,
    do_publish: bool = True,
    skip_extract: bool = False,
    reuse_gcs: bool = False,
) -> int:
    log = _log(bq)
    log.ensure()
    log.warn_if_stale("ListenBrainz", STALE_AFTER_DAYS)
    dump: StatsDump | None = None
    if skip_extract or reuse_gcs:
        if not dump_id:
            raise RefreshError("--skip-extract / --reuse-gcs need --dump-id")
        state = RunState(dump_id=dump_id)
    else:
        dump = resolve_dump(http, dump_id)
        state = RunState(dump_id=dump.dump_id)
    logger.info(f"ListenBrainz export {state.dump_id}")

    if do_publish and not force and log.already_published(state.dump_id):
        logger.info(f"Export {state.dump_id} already published; nothing to do")
        return 0

    try:
        if dump is not None:
            extract_dump_to_gcs(http, gcs, state, dump)
        if not skip_extract:
            load_raw_tables(bq, state.dump_id)
        build_models(bq)
        validate(bq, state)
        if not do_publish:
            logger.info(f"--no-publish: staging tables left in {sql.S} for inspection")
            return 0
        publish(bq, state)
    except Exception as e:
        log.write(state, common.log_status(e), f"{e}\n{traceback.format_exc()}")
        raise
    log.write(state, "success")
    cleanup_staging(bq, gcs, state.dump_id)
    logger.info(f"ListenBrainz refresh complete: {state.dump_id}")
    return 0


def publish_existing(bq: bigquery.Client, gcs: storage.Client, dump_id: str) -> int:
    """Validate and publish staging tables built by an earlier --no-publish run."""
    log = _log(bq)
    log.ensure()
    state = RunState(dump_id=dump_id)
    try:
        validate(bq, state)
        publish(bq, state)
    except Exception as e:
        log.write(state, common.log_status(e), f"{e}\n{traceback.format_exc()}")
        raise
    log.write(state, "success")
    cleanup_staging(bq, gcs, dump_id)
    return 0


def status(bq: bigquery.Client) -> int:
    log = _log(bq)
    log.ensure()
    log.print_recent()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh ListenBrainz popularity tables in BigQuery")
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run", help="Full refresh")
    p_run.add_argument("--dump-id", help="Export to load, e.g. 2663-20260915-000002 (default: newest complete)")
    p_run.add_argument("--force", action="store_true", help="Reload even if this export was already published")
    p_run.add_argument("--no-publish", action="store_true", help="Build + validate staging only")
    p_run.add_argument("--skip-extract", action="store_true", help="Reuse raw tables already loaded in staging")
    p_run.add_argument(
        "--reuse-gcs", action="store_true", help="Skip the download; load JSONL already extracted to GCS staging"
    )
    p_pub = sub.add_parser("publish", help="Validate + publish existing staging tables")
    p_pub.add_argument("--dump-id", required=True)
    sub.add_parser("status", help="Show recent runs")
    args = parser.parse_args(argv)

    common.configure_logging()
    bq = bigquery.Client(project=sql.PROJECT_ID)
    try:
        if args.command == "status":
            return status(bq)
        gcs = storage.Client(project=sql.PROJECT_ID)
        if args.command == "publish":
            return publish_existing(bq, gcs, args.dump_id)
        with httpx.Client(headers={"User-Agent": "nomadkaraoke-lb-refresh/1.0 (https://nomadkaraoke.com)"}) as http:
            return run(
                bq,
                gcs,
                http,
                dump_id=args.dump_id,
                force=args.force,
                do_publish=not args.no_publish,
                skip_extract=args.skip_extract,
                reuse_gcs=args.reuse_gcs,
            )
    except Exception:
        logger.exception("ListenBrainz refresh failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
