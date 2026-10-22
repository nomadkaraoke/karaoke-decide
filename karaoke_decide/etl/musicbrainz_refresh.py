"""Weekly MusicBrainz refresh: latest full dump -> BigQuery.

Runs as the ``mb-refresh`` Cloud Run Job (Cloud Scheduler, weekly). Steps:

1. CHECK     Read fullexport/LATEST; exit if that dump is already published.
2. DOWNLOAD  Copy each dump archive once into our bucket
             (``staging/archives/<dump_id>/``), resuming after stalls and
             verifying its SHA256; reruns reuse that copy (refresh_common).
   EXTRACT   Stream each archive back from GCS, decompress with lbzip2 and
             copy the ``mbdump/<table>`` members to
             ``gs://nomadkaraoke-musicbrainz-data/staging/<dump_id>/``.
             Nothing is written to local disk (Cloud Run disk is RAM).
3. LOAD      BigQuery load jobs (free) into ``musicbrainz_staging.raw_<table>``.
4. BUILD     SQL models (musicbrainz_sql.MODELS) -> ``musicbrainz_staging.<table>``.
5. VALIDATE  Row-count bounds vs prod + canary checks. Any failure stops the
             run before prod is touched.
6. PUBLISH   Copy jobs (free, atomic per table) staging -> ``karaoke_decide``,
             label each table with ``mb_dump=<dump_id>``.
7. MIRROR    Best-effort: every dump table, typed, -> the ``musicbrainz``
             dataset (musicbrainz_mirror). Per-table failures keep last week's
             copy and log a WARNING, plus ONE ERROR summary per run; they
             never fail the run.
8. CATALOG  Best-effort: build catalog.db (recommendation facets) from the fresh
             tables, gate it against the previous build and publish it to
             ``gs://nomadkaraoke-musicbrainz-data/catalog/`` behind latest.json.
             A gate refusal logs ONE WARNING and keeps the old pointer; a crash
             logs ONE ERROR; neither fails the run.
9. LOG       ``karaoke_decide.mb_refresh_log``, delete staging.

Usage:
    python -m karaoke_decide.etl.musicbrainz_refresh run [--dump-id ID] [--force] [--no-publish] [--skip-mirror]
    python -m karaoke_decide.etl.musicbrainz_refresh run --skip-extract   # rebuild from loaded raw tables
    python -m karaoke_decide.etl.musicbrainz_refresh run --reuse-gcs      # reload TSVs already in GCS staging
    python -m karaoke_decide.etl.musicbrainz_refresh publish --dump-id ID # validate + publish existing staging
    python -m karaoke_decide.etl.musicbrainz_refresh catalog              # build + publish catalog.db only
    python -m karaoke_decide.etl.musicbrainz_refresh status
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import tempfile
import traceback
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

import httpx
from google.cloud import (  # type: ignore[attr-defined]  # storage has no stubs
    bigquery,
    storage,
)

from karaoke_decide.catalog import publish as catalog_publish
from karaoke_decide.etl import musicbrainz_mirror as mirror
from karaoke_decide.etl import musicbrainz_sql as sql
from karaoke_decide.etl import refresh_common as common
from karaoke_decide.etl.refresh_common import (  # noqa: F401 - re-exported for callers/tests
    CHUNK,
    GIB,
    PUBLISH_ATTEMPTS,
    PartialPublishError,
    RefreshError,
    RunState,
)

logger = logging.getLogger("mb_refresh")

DUMP_BASE_URL = "https://data.metabrainz.org/pub/musicbrainz/data/fullexport"
GCS_BUCKET = "nomadkaraoke-musicbrainz-data"
GCS_STAGING_PREFIX = "staging"
LOG_TABLE = f"{sql.P}.mb_refresh_log"
ARCHIVES = ("mbdump.tar.bz2", "mbdump-derived.tar.bz2")
META_FILES = ("SCHEMA_SEQUENCE", "TIMESTAMP", "REPLICATION_SEQUENCE")
STALE_AFTER_DAYS = 14
# Largest model (karaoke_recording_links) estimates ~35 GiB: spotify_tracks
# name/isrc columns (~21.6 GiB) + karaokenerds_raw + mb_recordings.
MAX_BYTES_MODEL = 80 * GIB
MAX_BYTES_CHECK = 20 * GIB

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


def extract_members(
    compressed_chunks: Iterable[bytes],
    wanted: set[str],
    on_member: Any,
    expected_sha256: str,
    decompress_cmd: list[str] | None = None,
    required: set[str] | None = None,
) -> dict[str, str]:
    """Stream a .tar.bz2, call ``on_member(table, fileobj)`` for wanted tables.

    Returns the small metadata files (SCHEMA_SEQUENCE etc.) found in the
    archive. Raises RefreshError on a checksum mismatch or missing ``required``
    members (default: all wanted).
    """
    return common.extract_members(
        compressed_chunks,
        wanted,
        on_member,
        expected_sha256,
        decompress_cmd=decompress_cmd or ["lbzip2", "-dc"],
        table_of=member_table_name,
        meta_files=META_FILES,
        required=required,
    )


def archive_tables(archive: str) -> dict[str, int]:
    """{table: column count} to extract+load from one archive: app tables + full mirror."""
    widths = {t: len(cols) for t, cols in mirror.mirror_tables().get(archive, {}).items()}
    return {**widths, **sql.RAW_TABLES.get(archive, {})}


def all_raw_tables() -> dict[str, int]:
    return {t: n for archive in ARCHIVES for t, n in archive_tables(archive).items()}


def required_tables() -> set[str]:
    """Tables the app models need; anything else is mirror-only (best-effort)."""
    return {t for tables in sql.RAW_TABLES.values() for t in tables}


def archive_blob_name(dump_id: str, archive: str) -> str:
    """Cached copy of a dump archive; outside staging/<dump_id>/ so cleanup keeps it for reruns."""
    return f"{GCS_STAGING_PREFIX}/archives/{dump_id}/{archive}"


def gcs_uri(dump_id: str, table: str) -> str:
    return f"gs://{GCS_BUCKET}/{GCS_STAGING_PREFIX}/{dump_id}/{table}.tsv"


def extract_dump_to_gcs(http: httpx.Client, gcs: storage.Client, state: RunState) -> None:
    sums = fetch_sha256sums(http, state.dump_id)
    bucket = gcs.bucket(GCS_BUCKET)

    def upload(table: str, fileobj: IO[bytes]) -> None:
        blob = bucket.blob(f"{GCS_STAGING_PREFIX}/{state.dump_id}/{table}.tsv")
        with blob.open("wb", chunk_size=64 * 1024 * 1024, content_type="text/tab-separated-values") as out:
            shutil.copyfileobj(fileobj, out, CHUNK)

    for archive in ARCHIVES:
        if archive not in sums:
            raise RefreshError(f"{archive} not listed in SHA256SUMS for {state.dump_id}")
    # Download everything first: the origin connection is only held while
    # downloading, and an extract failure never costs a second download.
    blobs = {
        archive: common.cache_archive(
            http,
            bucket,
            f"{DUMP_BASE_URL}/{state.dump_id}/{archive}",
            archive_blob_name(state.dump_id, archive),
            sums[archive],
        )
        for archive in ARCHIVES
    }
    for archive in ARCHIVES:
        tables = archive_tables(archive)
        logger.info(f"Extracting {archive} from gs://{GCS_BUCKET}/{archive_blob_name(state.dump_id, archive)}")
        meta = extract_members(
            common.gcs_chunks(blobs[archive]),
            set(tables),
            upload,
            sums[archive],
            required=set(sql.RAW_TABLES[archive]),
        )
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


def staged_tables(gcs: storage.Client, dump_id: str) -> set[str]:
    """Tables extracted to GCS staging for this dump; fails fast if an app table is missing."""
    prefix = f"{GCS_STAGING_PREFIX}/{dump_id}/"
    blobs = gcs.bucket(GCS_BUCKET).list_blobs(prefix=prefix)
    names = {blob.name[len(prefix) :].removesuffix(".tsv") for blob in blobs}
    missing = required_tables() - names
    if missing:
        raise RefreshError(f"Required tables not staged in GCS for {dump_id}: {sorted(missing)}")
    return names


def create_empty_raw_table(bq: bigquery.Client, table: str, ncols: int) -> str | None:
    """Mirror-only table absent from the dump (MusicBrainz omits empty tables): load as empty.

    Returns a failure reason, or None on success.
    """
    dest = f"{sql.S}.raw_{table}"
    try:
        bq.delete_table(dest, not_found_ok=True)
        bq.create_table(bigquery.Table(dest, schema=raw_schema(ncols)))
    except Exception as e:  # noqa: BLE001 - mirror-only, best-effort
        logger.warning(f"MusicBrainz mirror: could not create empty {dest}, skipping {table}: {e}")
        return f"could not create empty raw table: {e}"
    return None


def load_raw_tables(bq: bigquery.Client, dump_id: str, present: set[str] | None = None) -> dict[str, str]:
    """Load every extracted table. App tables must load; mirror-only failures are returned.

    Returns {table: reason} for mirror-only tables that did not load. Each is
    logged as a WARNING; the mirror step reports them all in one ERROR summary
    (one alert per bad run, not one per table).

    ``present`` = tables actually in GCS staging (None: assume all). Mirror-only
    tables missing from it are empty in MusicBrainz, which leaves them out of the
    dump, so they become empty tables rather than load errors. A table that had
    rows last week and goes missing is still caught by the mirror's row check.
    """
    required = required_tables()
    jobs = []
    absent: list[str] = []
    failures: dict[str, str] = {}
    for table, ncols in all_raw_tables().items():
        dest = f"{sql.S}.raw_{table}"
        if present is not None and table not in present and table not in required:
            if (reason := create_empty_raw_table(bq, table, ncols)) is None:
                absent.append(table)
            else:
                failures[table] = reason
            continue
        if table not in required:
            # A failed WRITE_TRUNCATE load keeps the old table; never mirror a stale one.
            try:
                bq.delete_table(dest, not_found_ok=True)
            except Exception as e:  # noqa: BLE001 - mirror-only, best-effort
                logger.warning(f"MusicBrainz mirror: could not clear {dest}, skipping {table}: {e}")
                failures[table] = f"could not clear stale raw table: {e}"
                continue
        try:
            job = bq.load_table_from_uri(gcs_uri(dump_id, table), dest, job_config=raw_load_config(ncols))
        except Exception as e:  # noqa: BLE001 - classified below
            if table in required:
                raise RefreshError(f"Load into {dest} failed to start: {e}") from e
            logger.warning(f"MusicBrainz mirror: load of {table} failed to start: {e}")
            failures[table] = f"load failed to start: {e}"
            continue
        jobs.append((table, dest, job))
    for table, dest, job in jobs:
        try:
            job.result()
        except Exception as e:  # noqa: BLE001 - classified below
            if table in required:
                raise RefreshError(f"Load into {dest} failed: {e}") from e
            logger.warning(f"MusicBrainz mirror: load of {table} failed (schema change?): {e}")
            failures[table] = f"load failed (schema change?): {e}"
            continue
        logger.info(f"Loaded {dest}: {bq.get_table(dest).num_rows:,} rows")
    if absent:
        logger.info(f"{len(absent)} tables not in the dump (empty in MusicBrainz), mirrored as empty: {absent}")
    return failures


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


def check_row_counts(staging: dict[str, int | None], prod: dict[str, int | None]) -> list[str]:
    """Compare staging row counts with prod using ROW_COUNT_BOUNDS."""
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
    """Copy every staging table over prod, in MODEL_ORDER (see common.publish_tables)."""
    common.publish_tables(bq, sql.MODEL_ORDER, sql.S, sql.P, "mb_dump", state.dump_id)


def publish_mirror(bq: bigquery.Client, state: RunState, load_failures: dict[str, str] | None = None) -> None:
    """Best-effort full mirror; runs after the app tables are live and never raises."""
    try:
        result = mirror.build_and_publish(bq, state.dump_id, state.schema_sequence, load_failures=load_failures)
    except Exception:  # noqa: BLE001 - must not fail an already-published refresh
        logger.exception("MusicBrainz mirror failed; app tables are published, musicbrainz.* left as-is")
        return
    state.row_counts.update(result.summary())


def build_catalog(
    bq: bigquery.Client, gcs: storage.Client, *, workdir: Path | None = None, publish: bool = True
) -> dict[str, Any] | None:
    """Build + publish catalog.db. Returns the new pointer, or None if the gate refused it.

    An explicit ``workdir`` is created if needed and kept afterwards (size inspection);
    otherwise a scratch dir is used and removed. Raises on unexpected failures; callers
    decide whether that is fatal.
    """
    if workdir is not None:
        workdir.mkdir(parents=True, exist_ok=True)
        return catalog_publish.build_and_publish_catalog(bq, gcs, workdir, publish=publish)
    scratch = Path(tempfile.mkdtemp(prefix="catalog-", dir="/tmp"))  # noqa: S108 - Cloud Run scratch
    try:
        return catalog_publish.build_and_publish_catalog(bq, gcs, scratch, publish=publish)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def publish_catalog_best_effort(bq: bigquery.Client, gcs: storage.Client) -> None:
    """Runs after the app tables are live; never raises."""
    try:
        build_catalog(bq, gcs)
    except Exception:  # noqa: BLE001 - must not fail an already-published refresh
        logger.exception("catalog.db build failed; previous catalog pointer left as-is")


# --------------------------------------------------------------------------
# Run log + cleanup
# --------------------------------------------------------------------------


def _log(bq: bigquery.Client) -> common.RunLog:
    return common.RunLog(bq, LOG_TABLE)


def cleanup_staging(bq: bigquery.Client, gcs: storage.Client, dump_id: str) -> None:
    for table in all_raw_tables():
        bq.delete_table(f"{sql.S}.raw_{table}", not_found_ok=True)
    for name in [*sql.MODEL_ORDER, *mirror.staging_table_names()]:
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
    do_mirror: bool = True,
    do_catalog: bool = True,
) -> int:
    log = _log(bq)
    log.ensure()
    log.warn_if_stale("MusicBrainz", STALE_AFTER_DAYS)
    state = RunState(dump_id=dump_id or fetch_latest_dump_id(http))
    logger.info(f"MusicBrainz dump {state.dump_id}")

    if do_publish and not force and log.already_published(state.dump_id):
        logger.info(f"Dump {state.dump_id} already published; nothing to do")
        return 0

    load_failures: dict[str, str] = {}
    try:
        if not skip_extract:
            if not reuse_gcs:
                extract_dump_to_gcs(http, gcs, state)
            load_failures = load_raw_tables(bq, state.dump_id, present=staged_tables(gcs, state.dump_id))
        build_models(bq)
        validate(bq, state)
        if not do_publish:
            logger.info(f"--no-publish: staging tables left in {sql.S} for inspection")
            return 0
        publish(bq, state)
    except Exception as e:
        log.write(state, common.log_status(e), f"{e}\n{traceback.format_exc()}")
        raise
    if do_mirror:
        publish_mirror(bq, state, load_failures)
    if do_catalog:
        publish_catalog_best_effort(bq, gcs)
    log.write(state, "success")
    cleanup_staging(bq, gcs, state.dump_id)
    logger.info(f"MusicBrainz refresh complete: {state.dump_id}")
    return 0


def publish_existing(
    bq: bigquery.Client, gcs: storage.Client, dump_id: str, do_mirror: bool = True, do_catalog: bool = True
) -> int:
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
    if do_mirror:
        publish_mirror(bq, state)
    if do_catalog:
        publish_catalog_best_effort(bq, gcs)
    log.write(state, "success")
    cleanup_staging(bq, gcs, dump_id)
    return 0


def status(bq: bigquery.Client) -> int:
    log = _log(bq)
    log.ensure()
    log.print_recent()
    return 0


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
    p_run.add_argument("--skip-mirror", action="store_true", help="Don't build/publish the musicbrainz.* mirror")
    p_run.add_argument("--skip-catalog", action="store_true", help="Don't build/publish catalog.db")
    p_pub = sub.add_parser("publish", help="Validate + publish existing staging tables")
    p_pub.add_argument("--dump-id", required=True)
    p_pub.add_argument("--skip-mirror", action="store_true", help="Don't build/publish the musicbrainz.* mirror")
    p_pub.add_argument("--skip-catalog", action="store_true", help="Don't build/publish catalog.db")
    p_cat = sub.add_parser("catalog", help="Build + publish catalog.db only (no refresh)")
    p_cat.add_argument("--no-publish", action="store_true", help="Build + gate only; upload nothing")
    p_cat.add_argument("--workdir", type=Path, help="Build here and keep catalog.db (default: scratch dir, removed)")
    sub.add_parser("status", help="Show recent runs")
    args = parser.parse_args(argv)

    common.configure_logging()
    bq = bigquery.Client(project=sql.PROJECT_ID)
    try:
        if args.command == "status":
            return status(bq)
        gcs = storage.Client(project=sql.PROJECT_ID)
        if args.command == "catalog":
            if args.no_publish:
                # Exit 0 when the build+gate completed; there is no pointer to return.
                build_catalog(bq, gcs, workdir=args.workdir, publish=False)
                return 0
            return 0 if build_catalog(bq, gcs, workdir=args.workdir) else 1
        if args.command == "publish":
            return publish_existing(
                bq, gcs, args.dump_id, do_mirror=not args.skip_mirror, do_catalog=not args.skip_catalog
            )
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
                do_mirror=not args.skip_mirror,
                do_catalog=not args.skip_catalog,
            )
    except Exception:
        logger.exception("MusicBrainz refresh failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
