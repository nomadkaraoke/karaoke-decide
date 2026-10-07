"""Tests for the full MusicBrainz mirror (musicbrainz.* dataset)."""

from __future__ import annotations

import hashlib
import importlib.util
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from karaoke_decide.etl import musicbrainz_mirror as mirror
from karaoke_decide.etl import musicbrainz_refresh as mr
from karaoke_decide.etl import musicbrainz_sql as sql
from tests.unit.test_musicbrainz_refresh import PY_BUNZIP, chunks, make_archive

GEN_PATH = Path(__file__).resolve().parents[2] / "scripts" / "gen_musicbrainz_schema.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("gen_musicbrainz_schema", GEN_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestSchemaFile:
    def test_covers_both_archives(self):
        tables = mirror.mirror_tables()
        assert set(tables) == {"mbdump.tar.bz2", "mbdump-derived.tar.bz2"}
        assert len(tables["mbdump.tar.bz2"]) > 200
        # Genres are attached to entities via tags, which only ship in the derived archive.
        assert {"tag", "artist_tag", "release_group_tag", "recording_tag"} <= set(tables["mbdump-derived.tar.bz2"])
        assert {"genre", "release", "release_group", "track", "work", "label"} <= set(tables["mbdump.tar.bz2"])

    def test_app_raw_tables_match_schema_widths(self):
        """The hand-written app column counts must agree with the generated schema."""
        mirror_tables = mirror.mirror_tables()
        for archive, tables in sql.RAW_TABLES.items():
            for table, width in tables.items():
                assert len(mirror_tables[archive][table]) == width, f"{archive}:{table}"

    def test_known_columns(self):
        core = mirror.mirror_tables()["mbdump.tar.bz2"]
        assert [c[0] for c in core["release_group"]] == [
            "id",
            "gid",
            "name",
            "artist_credit",
            "type",
            "comment",
            "edits_pending",
            "last_updated",
        ]
        assert dict((c[0], c[2]) for c in core["label"])["ended"] == "BOOL"
        assert dict((c[0], c[2]) for c in core["release"])["last_updated"] == "TIMESTAMP"

    def test_every_table_has_columns(self):
        for tables in mirror.mirror_tables().values():
            for table, cols in tables.items():
                assert cols, table


class TestGenerator:
    SQL = """
CREATE TABLE thing ( -- replicate (verbose)
    id                  SERIAL,
    gid                 UUID NOT NULL,
    name                VARCHAR(255) NOT NULL DEFAULT '',
    ids                 INTEGER[],
    price               NUMERIC(10, 2),
    ended               BOOLEAN NOT NULL DEFAULT FALSE
      CONSTRAINT thing_ended_check CHECK (
        (end_year IS NOT NULL AND ended = TRUE) OR (end_year IS NULL)
      ),
    last_updated        TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CHECK (id > 0)
);

CREATE TABLE pair (
  a INTEGER NOT NULL,  -- PK, references thing.id
  b INTEGER NOT NULL
);
"""

    def test_parses_columns_types_and_skips_constraints(self):
        gen = _load_generator()
        tables = gen.parse_tables(self.SQL)
        assert [(c[0], c[2]) for c in tables["thing"]] == [
            ("id", "INT64"),
            ("gid", "STRING"),
            ("name", "STRING"),
            ("ids", "STRING"),
            ("price", "FLOAT64"),
            ("ended", "BOOL"),
            ("last_updated", "TIMESTAMP"),
        ]
        assert [c[0] for c in tables["pair"]] == ["a", "b"]

    def test_parse_list(self):
        gen = _load_generator()
        pm = "Readonly our @CORE_TABLE_LIST => qw(\n    artist\n    release\n);"
        assert gen.parse_list(pm, "CORE_TABLE_LIST") == ["artist", "release"]


class TestBuildSql:
    COLS = [("id", "SERIAL", "INT64"), ("gid", "UUID", "STRING"), ("ended", "BOOLEAN", "BOOL")]

    def test_named_typed_columns_from_raw(self):
        q = mirror.build_sql("label", self.COLS)
        assert f"CREATE OR REPLACE TABLE `{sql.S}.mbx_label`" in q
        assert f"FROM `{sql.S}.raw_label`" in q
        assert "CAST(c0 AS INT64) AS `id`" in q
        assert "pg_text(c1) AS `gid`" in q
        assert "AS `ended`" in q
        assert "CREATE TEMP FUNCTION pg_text" in q

    def test_clusters_on_gid_else_first_column(self):
        assert "CLUSTER BY `gid`" in mirror.build_sql("label", self.COLS)
        assert "CLUSTER BY `release`" in mirror.build_sql(
            "release_country", [("release", "INTEGER", "INT64"), ("country", "INTEGER", "INT64")]
        )

    def test_bool_cast_is_strict_and_null_safe(self):
        expr = mirror.column_expr(3, "BOOL")
        assert "c3 IS NULL THEN NULL" in expr
        assert "ERROR(" in expr

    def test_pg_text_keeps_empty_strings(self):
        assert "NULLIF" not in mirror.PG_TEXT_FN

        # Same escape handling as the app's pg() decoder.
        def squash(text: str) -> str:
            return "".join(text.split())

        decode = squash(mirror.PG_TEXT_FN).split("AS(", 1)[1].rsplit(");", 1)[0]
        assert decode in squash(sql.PG_DECODE_FN)


class TestCheckRows:
    @pytest.mark.parametrize(
        "new,old,ok",
        [(100, None, True), (0, None, True), (95, 100, True), (89, 100, False), (None, 100, False), (0, 0, True)],
    )
    def test_bounds(self, new, old, ok):
        assert (mirror.check_rows(new, old) is None) is ok


def _mirror_bq(build_fail: set[str] = frozenset(), rows: dict[str, tuple[int, int]] | None = None):
    """Mock client: raw/staging/prod row counts per table; builds for ``build_fail`` raise."""
    rows = rows or {}
    bq = MagicMock()

    def get_table(table_id):
        name = table_id.rsplit(".", 1)[-1]
        base = name.removeprefix("raw_").removeprefix("mbx_")
        new, old = rows.get(base, (1000, 1000))
        if table_id.startswith(mirror.MIRROR_DATASET + "."):  # not musicbrainz_staging
            return MagicMock(num_rows=old, labels={})
        return MagicMock(num_rows=new, labels={})

    def query(q, job_config=None):
        job = MagicMock()
        for t in build_fail:
            if f".mbx_{t}`" in q:
                job.result.side_effect = RuntimeError("Bad int64 value")
        return job

    bq.get_table.side_effect = get_table
    bq.query.side_effect = query
    return bq


class TestBuildAndPublish:
    def test_publishes_all_tables_and_labels(self):
        bq = _mirror_bq()
        result = mirror.build_and_publish(bq, "20261007-002147", "31")
        assert set(result.published) == set(mirror.table_widths())
        assert result.skipped == {}
        dests = {c.args[1] for c in bq.copy_table.call_args_list}
        assert f"{mirror.MIRROR_DATASET}.release_group" in dests
        assert result.summary()["mirror_tables_published"] == len(mirror.table_widths())

    def test_failed_build_and_row_drop_keep_previous_copy(self, caplog):
        bq = _mirror_bq(build_fail={"label"}, rows={"work": (500, 1000)})
        with caplog.at_level(logging.ERROR, logger="mb_refresh"):
            result = mirror.build_and_publish(bq, "20261007-002147", "31")
        assert set(result.skipped) == {"label", "work"}
        assert "build failed" in result.skipped["label"]
        dests = {c.args[1] for c in bq.copy_table.call_args_list}
        assert f"{mirror.MIRROR_DATASET}.label" not in dests
        assert f"{mirror.MIRROR_DATASET}.work" not in dests
        assert f"{mirror.MIRROR_DATASET}.release" in dests
        assert "kept previous musicbrainz.label" in caplog.text

    def test_schema_sequence_mismatch_logs_error(self, caplog):
        with caplog.at_level(logging.ERROR, logger="mb_refresh"):
            mirror.build_and_publish(_mirror_bq(), "d", "99")
        assert "gen_musicbrainz_schema.py" in caplog.text


class TestRawLoading:
    def test_archive_tables_include_app_and_mirror(self):
        core = mr.archive_tables("mbdump.tar.bz2")
        assert core["artist"] == sql.RAW_TABLES["mbdump.tar.bz2"]["artist"]
        assert "work" in core and "l_artist_work" in core
        assert "release_group_tag" in mr.archive_tables("mbdump-derived.tar.bz2")

    def test_required_load_failure_raises(self):
        bq = MagicMock()
        job = MagicMock()
        job.result.side_effect = RuntimeError("bad width")
        bq.load_table_from_uri.return_value = job
        with pytest.raises(mr.RefreshError, match="raw_"):
            mr.load_raw_tables(bq, "d")

    def test_mirror_only_load_failure_is_logged_and_stale_table_dropped(self, caplog):
        bq = MagicMock()

        def load(uri, dest, job_config=None):
            job = MagicMock()
            if dest.endswith(".raw_work"):
                job.result.side_effect = RuntimeError("bad width")
            return job

        bq.load_table_from_uri.side_effect = load
        bq.get_table.return_value = MagicMock(num_rows=1)
        with caplog.at_level(logging.ERROR, logger="mb_refresh"):
            mr.load_raw_tables(bq, "d")
        assert "load of work failed" in caplog.text
        deleted = {c.args[0] for c in bq.delete_table.call_args_list}
        assert f"{sql.S}.raw_work" in deleted
        assert f"{sql.S}.raw_artist" not in deleted  # app tables are never pre-deleted

    def test_mirror_only_delete_failure_skips_table_without_failing(self, caplog):
        bq = MagicMock()
        bq.get_table.return_value = MagicMock(num_rows=1)

        def delete(table_id, not_found_ok=False):
            if table_id.endswith(".raw_work"):
                raise RuntimeError("transient")

        bq.delete_table.side_effect = delete
        with caplog.at_level(logging.ERROR, logger="mb_refresh"):
            mr.load_raw_tables(bq, "d")
        loaded = {c.args[1] for c in bq.load_table_from_uri.call_args_list}
        assert f"{sql.S}.raw_work" not in loaded
        assert f"{sql.S}.raw_artist" in loaded
        assert "could not clear" in caplog.text

    def test_tables_absent_from_dump_become_empty_tables_without_errors(self, caplog):
        bq = MagicMock()
        bq.get_table.return_value = MagicMock(num_rows=1)
        present = set(mr.all_raw_tables()) - {"l_area_artist", "alternative_medium"}
        with caplog.at_level(logging.INFO, logger="mb_refresh"):
            mr.load_raw_tables(bq, "d", present=present)
        loaded = {c.args[1] for c in bq.load_table_from_uri.call_args_list}
        created = {c.args[0].table_id for c in bq.create_table.call_args_list}
        assert f"{sql.S}.raw_l_area_artist" not in loaded
        assert created == {"raw_l_area_artist", "raw_alternative_medium"}
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert "mirrored as empty" in caplog.text

    def test_required_table_absent_from_dump_still_fails(self):
        bq = MagicMock()
        bq.get_table.return_value = MagicMock(num_rows=1)

        def load(uri, dest, job_config=None):
            job = MagicMock()
            if dest.endswith(".raw_artist"):
                job.result.side_effect = RuntimeError("404 Not found")
            return job

        bq.load_table_from_uri.side_effect = load
        present = set(mr.all_raw_tables()) - {"artist"}
        with pytest.raises(mr.RefreshError, match="raw_artist"):
            mr.load_raw_tables(bq, "d", present=present)

    def test_staged_tables_from_gcs(self):
        gcs = MagicMock()
        gcs.bucket.return_value.list_blobs.return_value = [
            MagicMock(name="a"),
            MagicMock(name="b"),
        ]
        gcs.bucket.return_value.list_blobs.return_value[0].name = "staging/d/artist.tsv"
        gcs.bucket.return_value.list_blobs.return_value[1].name = "staging/d/l_artist_work.tsv"
        assert mr.staged_tables(gcs, "d") == {"artist", "l_artist_work"}


class TestOptionalExtract:
    def test_missing_optional_member_only_warns(self, caplog):
        archive = make_archive({"mbdump/artist": b"x\n"})
        with caplog.at_level(logging.INFO):
            mr.extract_members(
                chunks(archive),
                {"artist", "work"},
                lambda t, f: f.read(),
                hashlib.sha256(archive).hexdigest(),
                decompress_cmd=PY_BUNZIP,
                required={"artist"},
            )
        assert "no member for optional tables: ['work']" in caplog.text

    def test_missing_required_member_raises(self):
        archive = make_archive({"mbdump/work": b"x\n"})
        with pytest.raises(mr.RefreshError, match="missing tables"):
            mr.extract_members(
                chunks(archive),
                {"artist", "work"},
                lambda t, f: f.read(),
                hashlib.sha256(archive).hexdigest(),
                decompress_cmd=PY_BUNZIP,
                required={"artist"},
            )
