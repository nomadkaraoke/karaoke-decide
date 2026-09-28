"""Building blocks shared by the dump -> BigQuery refresh jobs.

Used by ``musicbrainz_refresh`` (``mb-refresh``) and ``listenbrainz_refresh``
(``lb-refresh``). Both follow the same shape: stream a public dump archive,
copy the members they need to GCS staging, load + build in a staging dataset,
validate, then copy the results over prod and record the run in a log table.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
import tarfile
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import IO, Any

from google.cloud import bigquery

logger = logging.getLogger("etl_refresh")

CHUNK = 8 * 1024 * 1024
GIB = 1024**3
PUBLISH_ATTEMPTS = 3


class RefreshError(RuntimeError):
    """A refresh step failed before publishing; prod tables were not modified."""


class PartialPublishError(RefreshError):
    """Publishing stopped partway: some prod tables already hold the new dump.

    Staging is kept, so ``publish --dump-id <id>`` can finish the job.
    """

    def __init__(self, published: list[str], failed: str, cause: Exception):
        self.published = published
        super().__init__(
            f"Publish failed on {failed} after replacing {len(published)} prod table(s) "
            f"{published}: {cause}. Staging kept; re-run `publish --dump-id` to finish."
        )


def log_status(error: Exception) -> str:
    return "partial_publish" if isinstance(error, PartialPublishError) else "failed"


@dataclass
class RunState:
    dump_id: str
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    schema_sequence: str | None = None
    row_counts: dict[str, int] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Streaming extract
# --------------------------------------------------------------------------


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
    decompress_cmd: list[str],
    table_of: Callable[[str], str | None],
    meta_files: Iterable[str] = (),
) -> dict[str, str]:
    """Stream a compressed tar and call ``on_member(table, fileobj)`` for wanted members.

    ``table_of`` maps a member path to a table name (or None to skip it).
    Small metadata members whose basename is in ``meta_files`` are returned as
    ``{basename: text}``. Raises RefreshError on a checksum mismatch or missing
    members.
    """
    meta_names = set(meta_files)
    proc = subprocess.Popen(decompress_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
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
                table = table_of(member.name)
                if table is None:
                    basename = member.name.rsplit("/", 1)[-1]
                    if basename in meta_names:
                        f = tar.extractfile(member)
                        if f is not None:
                            meta[basename] = f.read().decode().strip()
                    continue
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
    # A BrokenPipe in the feeder is just a consequence of killing the decompressor.
    download_error = errors[0] if errors and not isinstance(errors[0], BrokenPipeError) else None
    if download_error is not None:
        raise RefreshError(f"Download failed: {download_error!r}") from download_error
    if read_error is not None:
        if isinstance(read_error, RefreshError):
            raise read_error
        raise RefreshError(f"Reading archive failed: {read_error!r}") from read_error
    if rc != 0:
        raise RefreshError(f"{decompress_cmd[0]} exited with {rc}")
    actual = hasher.hexdigest()
    if actual != expected_sha256.lower():
        raise RefreshError(f"SHA256 mismatch: expected {expected_sha256}, got {actual}")
    missing = wanted - found
    if missing:
        raise RefreshError(f"Archive is missing tables: {sorted(missing)}")
    return meta


# --------------------------------------------------------------------------
# Validate + publish
# --------------------------------------------------------------------------


def num_rows(bq: bigquery.Client, table_id: str) -> int | None:
    try:
        rows = bq.get_table(table_id).num_rows
    except Exception:  # noqa: BLE001 - NotFound or transient; treated as "no baseline"
        return None
    return int(rows) if rows is not None else None


def check_row_counts(
    bounds_by_table: dict[str, tuple[float, float] | None],
    staging: dict[str, int | None],
    prod: dict[str, int | None],
) -> list[str]:
    """Compare staging row counts with prod. ``None`` bounds = no ratio check."""
    failures = []
    for name, bounds in bounds_by_table.items():
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


def run_canaries(bq: bigquery.Client, checks: dict[str, str], max_bytes: int) -> list[str]:
    """Run each check (one row: BOOL ``ok`` + optional ``detail``); return failures."""
    failures = []
    cfg = bigquery.QueryJobConfig(maximum_bytes_billed=max_bytes)
    for name, query in checks.items():
        row = next(iter(bq.query(query, job_config=cfg).result()))
        detail = row.get("detail")
        if row["ok"]:
            logger.info(f"Check {name}: ok ({detail})")
        else:
            failures.append(f"{name}: {detail}")
    return failures


def publish_tables(
    bq: bigquery.Client,
    names: list[str],
    staging_dataset: str,
    prod_dataset: str,
    label_key: str,
    label_value: str,
    attempts: int = PUBLISH_ATTEMPTS,
) -> None:
    """Copy every staging table over prod, in order.

    Each copy is atomic per table and idempotent, so it's retried; if one still
    fails, the tables already replaced are reported via PartialPublishError.
    """
    cfg = bigquery.CopyJobConfig(write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE)
    published: list[str] = []
    for name in names:
        dest = f"{prod_dataset}.{name}"
        for attempt in range(1, attempts + 1):
            try:
                bq.copy_table(f"{staging_dataset}.{name}", dest, job_config=cfg).result()
                break
            except Exception as e:  # noqa: BLE001 - retried, then reported
                if attempt == attempts:
                    raise PartialPublishError(published, name, e) from e
                logger.warning(f"Copy to {dest} failed (attempt {attempt}/{attempts}): {e}")
        published.append(name)
        # Labels are informational only; never fail a publish over them.
        try:
            table = bq.get_table(dest)
            table.labels = {**(table.labels or {}), label_key: label_value.lower()}
            bq.update_table(table, ["labels"])
            logger.info(f"Published {dest} ({table.num_rows:,} rows)")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Published {dest} but failed to set {label_key} label: {e}")


# --------------------------------------------------------------------------
# Run log
# --------------------------------------------------------------------------


class RunLog:
    """One row per refresh run in ``table`` (dump, status, row counts, error)."""

    def __init__(self, bq: bigquery.Client, table: str):
        self.bq = bq
        self.table = table

    def ensure(self) -> None:
        self.bq.query(
            f"""
            CREATE TABLE IF NOT EXISTS `{self.table}` (
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

    def write(self, state: RunState, status: str, error: str | None = None) -> None:
        params = [
            bigquery.ScalarQueryParameter("dump_id", "STRING", state.dump_id),
            bigquery.ScalarQueryParameter("status", "STRING", status),
            bigquery.ScalarQueryParameter("schema_sequence", "STRING", state.schema_sequence),
            bigquery.ScalarQueryParameter("started_at", "TIMESTAMP", state.started_at),
            bigquery.ScalarQueryParameter("row_counts", "STRING", json.dumps(state.row_counts)),
            bigquery.ScalarQueryParameter("error", "STRING", (error or "")[:10000] or None),
        ]
        self.bq.query(
            f"""
            INSERT INTO `{self.table}` (dump_id, status, schema_sequence, started_at, finished_at, row_counts, error)
            VALUES (@dump_id, @status, @schema_sequence, @started_at, CURRENT_TIMESTAMP(),
                    PARSE_JSON(@row_counts), @error)
            """,
            job_config=bigquery.QueryJobConfig(query_parameters=params),
        ).result()

    def last_success(self) -> tuple[str, datetime] | None:
        rows = list(
            self.bq.query(
                f"SELECT dump_id, finished_at FROM `{self.table}` WHERE status = 'success' "
                "ORDER BY finished_at DESC LIMIT 1"
            ).result()
        )
        return (rows[0]["dump_id"], rows[0]["finished_at"]) if rows else None

    def already_published(self, dump_id: str) -> bool:
        cfg = bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("d", "STRING", dump_id)])
        rows = list(
            self.bq.query(
                f"SELECT 1 FROM `{self.table}` WHERE status = 'success' AND dump_id = @d LIMIT 1", job_config=cfg
            ).result()
        )
        return bool(rows)

    def warn_if_stale(self, what: str, stale_after_days: int) -> None:
        """Log an ERROR (picked up by the error monitor) if data is getting old."""
        last = self.last_success()
        if last is None:
            return
        dump_id, finished_at = last
        age_days = (datetime.now(UTC) - finished_at).days
        if age_days > stale_after_days:
            logger.error(f"{what} data is stale: last successful refresh {dump_id} was {age_days} days ago")

    def print_recent(self, limit: int = 10) -> None:
        for row in self.bq.query(f"SELECT * FROM `{self.table}` ORDER BY finished_at DESC LIMIT {limit}").result():
            print(
                f"{row['finished_at']:%Y-%m-%d %H:%M} {row['status']:8} {row['dump_id']} {(row['error'] or '')[:120]}"
            )


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------


class CloudLoggingFormatter(logging.Formatter):
    """One JSON object per line so Cloud Logging picks up severity."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {"severity": record.levelname, "message": record.getMessage(), "logger": record.name}
        if record.exc_info:
            entry["message"] += "\n" + self.formatException(record.exc_info)
        return json.dumps(entry)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    if os.environ.get("CLOUD_RUN_JOB"):
        handler.setFormatter(CloudLoggingFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
