"""Weekly MusicBrainz refresh: latest full dump -> BigQuery.

Runs as the ``mb-refresh`` Cloud Run Job (Cloud Scheduler, weekly). Steps:

1. CHECK     Read fullexport/LATEST; exit if that dump is already published.
2. EXTRACT   Stream each dump archive over HTTPS, verify its SHA256, decompress
             with lbzip2 and copy only the needed ``mbdump/<table>`` members to
             ``gs://nomadkaraoke-musicbrainz-data/staging/<dump_id>/``.
             Nothing is written to local disk (Cloud Run disk is RAM).
3. LOAD      BigQuery load jobs (free) into ``musicbrainz_staging.raw_<table>``.
4. BUILD     SQL models (musicbrainz_sql.MODELS) -> ``musicbrainz_staging.<table>``.
5. VALIDATE  Row-count bounds vs prod + canary checks. Any failure stops the
             run before prod is touched.
6. PUBLISH   Copy jobs (free, atomic per table) staging -> ``karaoke_decide``,
             label each table with ``mb_dump=<dump_id>``, log to
             ``karaoke_decide.mb_refresh_log``, delete staging.

Usage:
    python -m karaoke_decide.etl.musicbrainz_refresh run [--dump-id ID] [--force] [--no-publish]
    python -m karaoke_decide.etl.musicbrainz_refresh run --skip-extract   # rebuild from loaded raw tables
    python -m karaoke_decide.etl.musicbrainz_refresh run --reuse-gcs      # reload TSVs already in GCS staging
    python -m karaoke_decide.etl.musicbrainz_refresh publish --dump-id ID # validate + publish existing staging
    python -m karaoke_decide.etl.musicbrainz_refresh status
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import threading
import traceback
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import IO, Any

import httpx
from google.cloud import (  # type: ignore[attr-defined]  # storage has no stubs
    bigquery,
    storage,
)

from karaoke_decide.etl import musicbrainz_sql as sql

logger = logging.getLogger("mb_refresh")

DUMP_BASE_URL = "https://data.metabrainz.org/pub/musicbrainz/data/fullexport"
GCS_BUCKET = "nomadkaraoke-musicbrainz-data"
GCS_STAGING_PREFIX = "staging"
LOG_TABLE = f"{sql.P}.mb_refresh_log"
META_FILES = ("SCHEMA_SEQUENCE", "TIMESTAMP", "REPLICATION_SEQUENCE")
STALE_AFTER_DAYS = 14
CHUNK = 8 * 1024 * 1024
GIB = 1024**3
# Largest model (karaoke_recording_links) estimates ~35 GiB: spotify_tracks
# name/isrc columns (~21.6 GiB) + karaokenerds_raw + mb_recordings.
MAX_BYTES_MODEL = 80 * GIB
MAX_BYTES_CHECK = 20 * GIB


class RefreshError(RuntimeError):
    """A refresh step failed; prod tables were not modified by this step."""


@dataclass
class RunState:
    dump_id: str
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    schema_sequence: str | None = None
    row_counts: dict[str, int] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Dump discovery + streaming extract
# --------------------------------------------------------------------------


def fetch_latest_dump_id(http: httpx.Client) -> str:
    resp = http.get(f"{DUMP_BASE_URL}/LATEST")
    resp.raise_for_status()
    dump_id = resp.text.strip()
    if not dump_id:
        raise RefreshError("fullexport/LATEST was empty")
    return dump_id


def parse_sha256sums(text: str) -> dict[str, str]:
    """Parse a ``sha256sum`` listing ("<hash> *<file>" or "<hash>  <file>")."""
    sums: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2:
            sums[parts[1].lstrip("*").strip()] = parts[0].lower()
    return sums


def fetch_sha256sums(http: httpx.Client, dump_id: str) -> dict[str, str]:
    resp = http.get(f"{DUMP_BASE_URL}/{dump_id}/SHA256SUMS")
    resp.raise_for_status()
    return parse_sha256sums(resp.text)


def member_table_name(member_name: str) -> str | None:
    """``mbdump/artist`` -> ``artist``; anything outside mbdump/ -> None."""
    prefix = "mbdump/"
    if not member_name.startswith(prefix):
        return None
    name = member_name[len(prefix) :]
    return name if name and "/" not in name else None


def _feed(chunks: Iterable[bytes], hasher: Any, dst: IO[bytes], errors: list[BaseException]) -> None:
    """Hash the compressed stream while piping it into the decompressor."""
    try:
        for chunk in chunks:
            hasher.update(chunk)
            dst.write(chunk)
    except BaseException as e:  # noqa: BLE001 - re-raised by the main thread
        errors.append(e)
    finally:
        try:
            dst.close()
        except OSError:
            pass


def extract_members(
    compressed_chunks: Iterable[bytes],
    wanted: set[str],
    on_member: Any,
    expected_sha256: str,
    decompress_cmd: list[str] | None = None,
) -> dict[str, str]:
    """Stream a .tar.bz2, call ``on_member(table, fileobj)`` for wanted tables.

    Returns the small metadata files (SCHEMA_SEQUENCE etc.) found in the
    archive. Raises RefreshError on a checksum mismatch or missing members.
    """
    cmd = decompress_cmd or ["lbzip2", "-dc"]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert proc.stdin is not None and proc.stdout is not None
    hasher = hashlib.sha256()
    errors: list[BaseException] = []
    feeder = threading.Thread(target=_feed, args=(compressed_chunks, hasher, proc.stdin, errors), daemon=True)
    feeder.start()

    meta: dict[str, str] = {}
    found: set[str] = set()
    read_error: Exception | None = None
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
            for member in tar:
                if not member.isfile():
                    continue
                if member.name in META_FILES:
                    f = tar.extractfile(member)
                    if f is not None:
                        meta[member.name] = f.read().decode().strip()
                    continue
                table = member_table_name(member.name)
                if table in wanted:
                    f = tar.extractfile(member)
                    if f is None:
                        raise RefreshError(f"Could not read {member.name}")
                    logger.info(f"Extracting {member.name} ({member.size / GIB:.2f} GiB)")
                    on_member(table, f)
                    found.add(table)
        # Drain trailing padding so the decompressor and feeder can finish.
        while proc.stdout.read(CHUNK):
            pass
    except Exception as e:  # noqa: BLE001 - classified below
        read_error = e
        # Stop the decompressor so the feeder can't block writing into a full pipe.
        proc.kill()
    finally:
        feeder.join()
        rc = proc.wait()

    # A truncated download surfaces as a tar read error; report the root cause.
    # A BrokenPipe in the feeder is just a consequence of killing lbzip2.
    download_error = errors[0] if errors and not isinstance(errors[0], BrokenPipeError) else None
    if download_error is not None:
        raise RefreshError(f"Download failed: {download_error!r}") from download_error
    if read_error is not None:
        if isinstance(read_error, RefreshError):
            raise read_error
        raise RefreshError(f"Reading archive failed: {read_error!r}") from read_error
    if rc != 0:
        raise RefreshError(f"{cmd[0]} exited with {rc}")
    actual = hasher.hexdigest()
    if actual != expected_sha256.lower():
        raise RefreshError(f"SHA256 mismatch: expected {expected_sha256}, got {actual}")
    missing = wanted - found
    if missing:
        raise RefreshError(f"Archive is missing tables: {sorted(missing)}")
    return meta


def gcs_uri(dump_id: str, table: str) -> str:
    return f"gs://{GCS_BUCKET}/{GCS_STAGING_PREFIX}/{dump_id}/{table}.tsv"


def extract_dump_to_gcs(http: httpx.Client, gcs: storage.Client, state: RunState) -> None:
    sums = fetch_sha256sums(http, state.dump_id)
    bucket = gcs.bucket(GCS_BUCKET)

    def upload(table: str, fileobj: IO[bytes]) -> None:
        blob = bucket.blob(f"{GCS_STAGING_PREFIX}/{state.dump_id}/{table}.tsv")
        with blob.open("wb", chunk_size=64 * 1024 * 1024, content_type="text/tab-separated-values") as out:
            shutil.copyfileobj(fileobj, out, CHUNK)

    for archive, tables in sql.RAW_TABLES.items():
        if archive not in sums:
            raise RefreshError(f"{archive} not listed in SHA256SUMS for {state.dump_id}")
        url = f"{DUMP_BASE_URL}/{state.dump_id}/{archive}"
        logger.info(f"Streaming {url}")
        timeout = httpx.Timeout(60.0, read=600.0)
        with http.stream("GET", url, timeout=timeout, follow_redirects=True) as resp:
            resp.raise_for_status()
            meta = extract_members(resp.iter_bytes(CHUNK), set(tables), upload, sums[archive])
        if "SCHEMA_SEQUENCE" in meta:
            state.schema_sequence = meta["SCHEMA_SEQUENCE"]
        logger.info(f"{archive}: extracted {len(tables)} tables (meta={meta})")


# --------------------------------------------------------------------------
# BigQuery load / build / validate / publish
# --------------------------------------------------------------------------


def raw_schema(column_count: int) -> list[bigquery.SchemaField]:
    return [bigquery.SchemaField(f"c{i}", "STRING") for i in range(column_count)]


def raw_load_config(column_count: int) -> bigquery.LoadJobConfig:
    """PostgreSQL COPY text: tab-separated, no quoting, \\N = NULL."""
    return bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.CSV,
        schema=raw_schema(column_count),
        field_delimiter="\t",
        quote_character="",
        null_marker="\\N",
        encoding="UTF-8",
        allow_jagged_rows=False,
        allow_quoted_newlines=False,
        max_bad_records=0,
        preserve_ascii_control_characters=True,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )


def load_raw_tables(bq: bigquery.Client, dump_id: str) -> None:
    jobs = []
    for tables in sql.RAW_TABLES.values():
        for table, ncols in tables.items():
            dest = f"{sql.S}.raw_{table}"
            jobs.append(
                (dest, bq.load_table_from_uri(gcs_uri(dump_id, table), dest, job_config=raw_load_config(ncols)))
            )
    for dest, job in jobs:
        try:
            job.result()
        except Exception as e:
            raise RefreshError(f"Load into {dest} failed: {e}") from e
        logger.info(f"Loaded {dest}: {bq.get_table(dest).num_rows:,} rows")


def build_models(bq: bigquery.Client) -> None:
    for name in sql.MODEL_ORDER:
        start = datetime.now(UTC)
        job = bq.query(sql.model_script(name), job_config=bigquery.QueryJobConfig(maximum_bytes_billed=MAX_BYTES_MODEL))
        try:
            job.result()
        except Exception as e:
            raise RefreshError(f"Model {name} failed: {e}") from e
        billed = (job.total_bytes_billed or 0) / GIB
        secs = (datetime.now(UTC) - start).total_seconds()
        logger.info(
            f"Built {sql.S}.{name}: {bq.get_table(f'{sql.S}.{name}').num_rows:,} rows, {billed:.2f} GiB billed, {secs:.0f}s"
        )


def _num_rows(bq: bigquery.Client, table_id: str) -> int | None:
    try:
        rows = bq.get_table(table_id).num_rows
    except Exception:  # noqa: BLE001 - NotFound or transient; treated as "no baseline"
        return None
    return int(rows) if rows is not None else None


def check_row_counts(staging: dict[str, int | None], prod: dict[str, int | None]) -> list[str]:
    """Compare staging row counts with prod using ROW_COUNT_BOUNDS."""
    failures = []
    for name, bounds in sql.ROW_COUNT_BOUNDS.items():
        new = staging.get(name)
        if not new:
            failures.append(f"{name}: staging table empty or missing")
            continue
        old = prod.get(name)
        if bounds is None or not old:
            continue
        lo, hi = bounds
        ratio = new / old
        if not lo <= ratio <= hi:
            failures.append(f"{name}: {new:,} rows vs prod {old:,} (ratio {ratio:.3f} outside [{lo}, {hi}])")
    return failures


def validate(bq: bigquery.Client, state: RunState) -> None:
    staging = {name: _num_rows(bq, f"{sql.S}.{name}") for name in sql.MODEL_ORDER}
    prod = {name: _num_rows(bq, f"{sql.P}.{name}") for name in sql.MODEL_ORDER}
    state.row_counts = {k: v for k, v in staging.items() if v is not None}
    for name in sql.MODEL_ORDER:
        logger.info(f"Rows {name}: staging={staging[name]} prod={prod[name]}")

    failures = check_row_counts(staging, prod)
    cfg = bigquery.QueryJobConfig(maximum_bytes_billed=MAX_BYTES_CHECK)
    for name, query in sql.CANARY_CHECKS.items():
        row = next(iter(bq.query(query, job_config=cfg).result()))
        detail = row.get("detail")
        if row["ok"]:
            logger.info(f"Check {name}: ok ({detail})")
        else:
            failures.append(f"{name}: {detail}")

    if failures:
        raise RefreshError("Validation failed, prod untouched: " + "; ".join(failures))


def publish(bq: bigquery.Client, state: RunState) -> None:
    cfg = bigquery.CopyJobConfig(write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE)
    for name in sql.MODEL_ORDER:
        dest = f"{sql.P}.{name}"
        bq.copy_table(f"{sql.S}.{name}", dest, job_config=cfg).result()
        table = bq.get_table(dest)
        table.labels = {**(table.labels or {}), "mb_dump": state.dump_id.lower()}
        bq.update_table(table, ["labels"])
        logger.info(f"Published {dest} ({table.num_rows:,} rows)")


# --------------------------------------------------------------------------
# Run log + cleanup
# --------------------------------------------------------------------------


def ensure_log_table(bq: bigquery.Client) -> None:
    bq.query(
        f"""
        CREATE TABLE IF NOT EXISTS `{LOG_TABLE}` (
            dump_id STRING NOT NULL,
            status STRING NOT NULL,
            schema_sequence STRING,
            started_at TIMESTAMP NOT NULL,
            finished_at TIMESTAMP NOT NULL,
            row_counts JSON,
            error STRING
        )
        """
    ).result()


def write_log(bq: bigquery.Client, state: RunState, status: str, error: str | None = None) -> None:
    params = [
        bigquery.ScalarQueryParameter("dump_id", "STRING", state.dump_id),
        bigquery.ScalarQueryParameter("status", "STRING", status),
        bigquery.ScalarQueryParameter("schema_sequence", "STRING", state.schema_sequence),
        bigquery.ScalarQueryParameter("started_at", "TIMESTAMP", state.started_at),
        bigquery.ScalarQueryParameter("row_counts", "STRING", json.dumps(state.row_counts)),
        bigquery.ScalarQueryParameter("error", "STRING", (error or "")[:10000] or None),
    ]
    bq.query(
        f"""
        INSERT INTO `{LOG_TABLE}` (dump_id, status, schema_sequence, started_at, finished_at, row_counts, error)
        VALUES (@dump_id, @status, @schema_sequence, @started_at, CURRENT_TIMESTAMP(),
                PARSE_JSON(@row_counts), @error)
        """,
        job_config=bigquery.QueryJobConfig(query_parameters=params),
    ).result()


def last_success(bq: bigquery.Client) -> tuple[str, datetime] | None:
    rows = list(
        bq.query(
            f"SELECT dump_id, finished_at FROM `{LOG_TABLE}` WHERE status = 'success' ORDER BY finished_at DESC LIMIT 1"
        ).result()
    )
    return (rows[0]["dump_id"], rows[0]["finished_at"]) if rows else None


def already_published(bq: bigquery.Client, dump_id: str) -> bool:
    cfg = bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("d", "STRING", dump_id)])
    rows = list(
        bq.query(
            f"SELECT 1 FROM `{LOG_TABLE}` WHERE status = 'success' AND dump_id = @d LIMIT 1", job_config=cfg
        ).result()
    )
    return bool(rows)


def warn_if_stale(bq: bigquery.Client) -> None:
    """Log an ERROR (picked up by the error monitor) if data is getting old."""
    last = last_success(bq)
    if last is None:
        return
    dump_id, finished_at = last
    age_days = (datetime.now(UTC) - finished_at).days
    if age_days > STALE_AFTER_DAYS:
        logger.error(f"MusicBrainz data is stale: last successful refresh {dump_id} was {age_days} days ago")


def cleanup_staging(bq: bigquery.Client, gcs: storage.Client, dump_id: str) -> None:
    for tables in sql.RAW_TABLES.values():
        for table in tables:
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
    ensure_log_table(bq)
    warn_if_stale(bq)
    state = RunState(dump_id=dump_id or fetch_latest_dump_id(http))
    logger.info(f"MusicBrainz dump {state.dump_id}")

    if do_publish and not force and already_published(bq, state.dump_id):
        logger.info(f"Dump {state.dump_id} already published; nothing to do")
        return 0

    try:
        if not skip_extract:
            if not reuse_gcs:
                extract_dump_to_gcs(http, gcs, state)
            load_raw_tables(bq, state.dump_id)
        build_models(bq)
        validate(bq, state)
        if not do_publish:
            logger.info(f"--no-publish: staging tables left in {sql.S} for inspection")
            return 0
        publish(bq, state)
    except Exception as e:
        write_log(bq, state, "failed", f"{e}\n{traceback.format_exc()}")
        raise
    write_log(bq, state, "success")
    cleanup_staging(bq, gcs, state.dump_id)
    logger.info(f"MusicBrainz refresh complete: {state.dump_id}")
    return 0


def publish_existing(bq: bigquery.Client, gcs: storage.Client, dump_id: str) -> int:
    """Validate and publish staging tables built by an earlier --no-publish run."""
    ensure_log_table(bq)
    state = RunState(dump_id=dump_id)
    try:
        validate(bq, state)
        publish(bq, state)
    except Exception as e:
        write_log(bq, state, "failed", f"{e}\n{traceback.format_exc()}")
        raise
    write_log(bq, state, "success")
    cleanup_staging(bq, gcs, dump_id)
    return 0


def status(bq: bigquery.Client) -> int:
    ensure_log_table(bq)
    for row in bq.query(f"SELECT * FROM `{LOG_TABLE}` ORDER BY finished_at DESC LIMIT 10").result():
        print(f"{row['finished_at']:%Y-%m-%d %H:%M} {row['status']:8} {row['dump_id']} {(row['error'] or '')[:120]}")
    return 0


class _CloudLoggingFormatter(logging.Formatter):
    """One JSON object per line so Cloud Logging picks up severity."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {"severity": record.levelname, "message": record.getMessage(), "logger": record.name}
        if record.exc_info:
            entry["message"] += "\n" + self.formatException(record.exc_info)
        return json.dumps(entry)


def _configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    if os.environ.get("CLOUD_RUN_JOB"):
        handler.setFormatter(_CloudLoggingFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh MusicBrainz tables in BigQuery from the latest dump")
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run", help="Full refresh")
    p_run.add_argument("--dump-id", help="Dump to load (default: fullexport/LATEST)")
    p_run.add_argument("--force", action="store_true", help="Reload even if this dump was already published")
    p_run.add_argument("--no-publish", action="store_true", help="Build + validate staging only")
    p_run.add_argument("--skip-extract", action="store_true", help="Reuse raw tables already loaded in staging")
    p_run.add_argument(
        "--reuse-gcs", action="store_true", help="Skip the download; load TSVs already extracted to GCS staging"
    )
    p_pub = sub.add_parser("publish", help="Validate + publish existing staging tables")
    p_pub.add_argument("--dump-id", required=True)
    sub.add_parser("status", help="Show recent runs")
    args = parser.parse_args(argv)

    _configure_logging()
    bq = bigquery.Client(project=sql.PROJECT_ID)
    try:
        if args.command == "status":
            return status(bq)
        gcs = storage.Client(project=sql.PROJECT_ID)
        if args.command == "publish":
            return publish_existing(bq, gcs, args.dump_id)
        with httpx.Client(headers={"User-Agent": "nomadkaraoke-mb-refresh/1.0 (https://nomadkaraoke.com)"}) as http:
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
        logger.exception("MusicBrainz refresh failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
