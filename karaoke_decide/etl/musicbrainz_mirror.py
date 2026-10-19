"""Full MusicBrainz mirror: every dump table, typed, in the ``musicbrainz`` dataset.

The app tables in ``karaoke_decide`` (musicbrainz_sql.MODELS) are a curated,
reshaped subset. This module additionally publishes *every* table from
``mbdump.tar.bz2`` and ``mbdump-derived.tar.bz2`` with MusicBrainz's own table
and column names (``musicbrainz.release``, ``musicbrainz.release_group_tag``,
...), so any metadata question is a query rather than a pipeline change. Join
on the integer ``id`` / foreign-key columns exactly as in the MusicBrainz
schema (https://musicbrainz.org/doc/MusicBrainz_Database/Schema).

Column names/types come from ``musicbrainz_schema.json``, generated from
musicbrainz-server sources by ``scripts/gen_musicbrainz_schema.py``.

The mirror is best-effort per table and never blocks the app tables: it runs
after they are published, and a table that fails to load, build or validate
keeps last week's copy instead of failing the run. Each such table gets a
WARNING with the details, then the run logs ONE ERROR summarising them all, so
the error monitor (which alerts on every new ERROR pattern) raises one alert
per bad run rather than one per table.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from google.cloud import bigquery

from karaoke_decide.etl import musicbrainz_sql as sql
from karaoke_decide.etl.refresh_common import GIB, num_rows

logger = logging.getLogger("mb_refresh")

MIRROR_DATASET = f"{sql.PROJECT_ID}.musicbrainz"
SCHEMA_FILE = Path(__file__).with_name("musicbrainz_schema.json")
STAGING_PREFIX = "mbx_"  # typed staging copy: musicbrainz_staging.mbx_<table>
BUILD_WORKERS = 8
MAX_BYTES_TABLE = 80 * GIB
# A table may shrink a little week to week (deletes/merges); a big drop means a
# broken extract/load, so keep last week's copy instead. Only tables with at
# least MIN_ROWS_FOR_RATIO rows are checked: tiny ones (l_artist_genre has ~6
# rows) routinely lose a few rows to ordinary edits, and because a kept table is
# compared against the same stale copy next week, a false positive there would
# alert every week forever.
MIN_ROW_RATIO = 0.9
MIN_ROWS_FOR_RATIO = 1000
# Bounds on the run summary so one ERROR stays readable.
SUMMARY_MAX_TABLES = 25
SUMMARY_MAX_ERROR_CHARS = 300

# PostgreSQL COPY text decoding without pg()'s ''->NULL: the mirror keeps
# MusicBrainz values verbatim (e.g. comment = '').
PG_TEXT_FN = r"""
CREATE TEMP FUNCTION pg_text(s STRING) AS (
  REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
    s, '\\\\', '\uE000'), '\\t', '\t'), '\\n', '\n'), '\\r', '\r'), '\uE000', '\\')
);
"""

Column = tuple[str, str, str]  # (name, postgres type, bigquery type)


@cache
def schema() -> dict:
    data: dict = json.loads(SCHEMA_FILE.read_text())
    return data


def mirror_tables() -> dict[str, dict[str, list[Column]]]:
    """{archive: {table: [(column, pg_type, bq_type), ...]}} in COPY column order."""
    return {
        archive: {t: [(c[0], c[1], c[2]) for c in cols] for t, cols in tables.items()}
        for archive, tables in schema()["archives"].items()
    }


def table_widths() -> dict[str, int]:
    return {t: len(cols) for tables in mirror_tables().values() for t, cols in tables.items()}


def column_expr(index: int, bq_type: str) -> str:
    """Strict cast of raw column ``c<index>``: a bad value fails the table (kept from last week)."""
    c = f"c{index}"
    if bq_type == "INT64":
        return f"CAST({c} AS INT64)"
    if bq_type == "BOOL":
        return (
            f"CASE WHEN {c} IS NULL THEN NULL WHEN {c} = 't' THEN TRUE WHEN {c} = 'f' THEN FALSE "
            f"ELSE ERROR(CONCAT('bad bool: ', {c})) END"
        )
    if bq_type in ("TIMESTAMP", "DATE", "FLOAT64"):
        return f"CAST({c} AS {bq_type})"
    return f"pg_text({c})"


def cluster_column(cols: list[Column]) -> str | None:
    """MBID lookups are the common case; otherwise the first (id / FK) column."""
    names = [c[0] for c in cols]
    if "gid" in names:
        return "gid"
    if cols and cols[0][2] in ("INT64", "STRING"):
        return cols[0][0]
    return None


def build_sql(table: str, cols: list[Column]) -> str:
    select = ",\n    ".join(f"{column_expr(i, bq)} AS `{name}`" for i, (name, _pg, bq) in enumerate(cols))
    cluster = cluster_column(cols)
    cluster_clause = f"\nCLUSTER BY `{cluster}`" if cluster else ""
    return f"""{PG_TEXT_FN}
CREATE OR REPLACE TABLE `{sql.S}.{STAGING_PREFIX}{table}`{cluster_clause}
AS
SELECT
    {select}
FROM `{sql.S}.raw_{table}`
"""


@dataclass
class MirrorResult:
    published: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)  # table -> reason

    def summary(self) -> dict[str, int]:
        return {"mirror_tables_published": len(self.published), "mirror_tables_skipped": len(self.skipped)}


def _build_one(bq: bigquery.Client, table: str, cols: list[Column]) -> str | None:
    """Build one staging table; return a failure reason or None."""
    if num_rows(bq, f"{sql.S}.raw_{table}") is None:
        return "raw table not loaded"
    try:
        bq.query(
            build_sql(table, cols), job_config=bigquery.QueryJobConfig(maximum_bytes_billed=MAX_BYTES_TABLE)
        ).result()
    except Exception as e:  # noqa: BLE001 - reported per table
        return f"build failed: {e}"
    return None


def check_rows(new: int | None, old: int | None) -> str | None:
    """Row-count sanity vs the published copy; None = OK."""
    if new is None:
        return "staging table missing"
    if old and old >= MIN_ROWS_FOR_RATIO and new < old * MIN_ROW_RATIO:
        return f"{new:,} rows vs published {old:,} (< {MIN_ROW_RATIO:.0%})"
    return None


def summary_error(skipped: dict[str, str], total: int, schema_hint: str | None = None) -> str:
    """One ERROR line for every table this run kept from last week."""
    names = sorted(skipped)
    shown = ", ".join(names[:SUMMARY_MAX_TABLES])
    if len(names) > SUMMARY_MAX_TABLES:
        shown += f", ... and {len(names) - SUMMARY_MAX_TABLES} more"
    first = names[0]
    reason = skipped[first]
    if len(reason) > SUMMARY_MAX_ERROR_CHARS:
        reason = reason[:SUMMARY_MAX_ERROR_CHARS] + "..."
    message = (
        f"MusicBrainz mirror: {len(names)} of {total} tables failed and kept the previous copy in "
        f"{MIRROR_DATASET}: {shown} (first error, {first}: {reason}). "
        "Per-table details are in this run's WARNING logs."
    )
    return f"{message} {schema_hint}" if schema_hint else message


def build_and_publish(
    bq: bigquery.Client,
    dump_id: str,
    schema_sequence: str | None = None,
    load_failures: dict[str, str] | None = None,
) -> MirrorResult:
    """Build every mirror table from the loaded raw tables and publish the good ones.

    ``load_failures`` = {table: reason} from the raw load step, so a table that
    never loaded is reported with its real cause, not "raw table not loaded".
    """
    result = MirrorResult()
    load_failures = load_failures or {}
    tables = {t: cols for archive in mirror_tables().values() for t, cols in archive.items()}
    schema_hint = None
    if schema_sequence and str(schema()["schema_sequence"]) != str(schema_sequence).strip():
        schema_hint = (
            f"MusicBrainz SCHEMA_SEQUENCE is {schema_sequence} but musicbrainz_schema.json is for "
            f"{schema()['schema_sequence']}; tables whose shape changed will be skipped. "
            "Re-run scripts/gen_musicbrainz_schema.py."
        )
        logger.warning(schema_hint)

    with ThreadPoolExecutor(max_workers=BUILD_WORKERS) as pool:
        failures = dict(zip(tables, pool.map(lambda t: _build_one(bq, t, tables[t]), tables), strict=True))

    copy_cfg = bigquery.CopyJobConfig(write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE)
    for table in tables:
        reason = failures[table]
        if reason is not None and table in load_failures:
            reason = load_failures[table]
        reason = reason or check_rows(
            num_rows(bq, f"{sql.S}.{STAGING_PREFIX}{table}"), num_rows(bq, f"{MIRROR_DATASET}.{table}")
        )
        if reason is None:
            dest = f"{MIRROR_DATASET}.{table}"
            try:
                bq.copy_table(f"{sql.S}.{STAGING_PREFIX}{table}", dest, job_config=copy_cfg).result()
            except Exception as e:  # noqa: BLE001 - reported per table
                reason = f"publish failed: {e}"
            else:
                result.published.append(table)
                _label(bq, dest, dump_id)
                continue
        result.skipped[table] = reason
        logger.warning(f"MusicBrainz mirror: kept previous musicbrainz.{table} ({reason})")

    logger.info(f"MusicBrainz mirror: published {len(result.published)}/{len(tables)} tables to {MIRROR_DATASET}")
    if result.skipped:
        logger.error(summary_error(result.skipped, len(tables), schema_hint))
    elif schema_hint:
        logger.error(schema_hint)
    return result


def _label(bq: bigquery.Client, table_id: str, dump_id: str) -> None:
    try:
        table = bq.get_table(table_id)
        table.labels = {**(table.labels or {}), "mb_dump": dump_id.lower()}
        bq.update_table(table, ["labels"])
    except Exception as e:  # noqa: BLE001 - labels are informational
        logger.warning(f"Could not label {table_id}: {e}")


def staging_table_names() -> list[str]:
    return [f"{STAGING_PREFIX}{t}" for t in table_widths()]
