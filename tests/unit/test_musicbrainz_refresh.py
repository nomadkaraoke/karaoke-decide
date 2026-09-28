"""Tests for the MusicBrainz refresh pipeline (karaoke_decide.etl)."""

import bz2
import hashlib
import io
import os
import re
import sys
import tarfile
import threading
from unittest.mock import MagicMock

import pytest

from karaoke_decide.etl import musicbrainz_refresh as mr
from karaoke_decide.etl import musicbrainz_sql as sql

# Portable stand-in for lbzip2 so tests don't need it installed.
PY_BUNZIP = [
    sys.executable,
    "-c",
    "import bz2,shutil,sys; shutil.copyfileobj(bz2.open(sys.stdin.buffer), sys.stdout.buffer)",
]


def make_archive(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return bz2.compress(buf.getvalue())


def chunks(data: bytes, size: int = 1000):
    for i in range(0, len(data), size):
        yield data[i : i + size]


class TestParsing:
    def test_parse_sha256sums_handles_both_formats(self):
        text = "ABC123 *mbdump.tar.bz2\ndef456  mbdump-derived.tar.bz2\n\n"
        assert mr.parse_sha256sums(text) == {
            "mbdump.tar.bz2": "abc123",
            "mbdump-derived.tar.bz2": "def456",
        }

    @pytest.mark.parametrize(
        "member,expected",
        [
            ("mbdump/artist", "artist"),
            ("mbdump/l_artist_url", "l_artist_url"),
            ("SCHEMA_SEQUENCE", None),
            ("mbdump/", None),
            ("mbdump/sub/dir", None),
            ("other/artist", None),
        ],
    )
    def test_member_table_name(self, member, expected):
        assert mr.member_table_name(member) == expected


class TestExtractMembers:
    def test_extracts_only_wanted_tables_and_metadata(self):
        archive = make_archive(
            {
                "SCHEMA_SEQUENCE": b"30\n",
                "TIMESTAMP": b"2026-09-26\n",
                "mbdump/artist": b"1\tgid\tName\n",
                "mbdump/release": b"ignored\n",
                "mbdump/tag": b"7\trock\t3\n",
            }
        )
        got: dict[str, bytes] = {}
        meta = mr.extract_members(
            chunks(archive),
            {"artist", "tag"},
            lambda table, f: got.__setitem__(table, f.read()),
            hashlib.sha256(archive).hexdigest(),
            decompress_cmd=PY_BUNZIP,
        )
        assert got == {"artist": b"1\tgid\tName\n", "tag": b"7\trock\t3\n"}
        assert meta == {"SCHEMA_SEQUENCE": "30", "TIMESTAMP": "2026-09-26"}

    def test_checksum_mismatch_raises(self):
        archive = make_archive({"mbdump/artist": b"x\n"})
        with pytest.raises(mr.RefreshError, match="SHA256 mismatch"):
            mr.extract_members(chunks(archive), {"artist"}, lambda t, f: f.read(), "0" * 64, decompress_cmd=PY_BUNZIP)

    def test_missing_table_raises(self):
        archive = make_archive({"mbdump/artist": b"x\n"})
        with pytest.raises(mr.RefreshError, match="missing tables"):
            mr.extract_members(
                chunks(archive),
                {"artist", "recording"},
                lambda t, f: f.read(),
                hashlib.sha256(archive).hexdigest(),
                decompress_cmd=PY_BUNZIP,
            )

    def test_upload_failure_mid_archive_raises_without_deadlock(self):
        # Big enough (incompressible) that the OS pipes fill up if the
        # decompressor isn't stopped when the consumer bails out.
        payload = os.urandom(4 * 1024 * 1024)
        archive = make_archive({"mbdump/artist": b"x\n", "mbdump/recording": payload})

        def fail_on_artist(table, f):
            raise OSError("GCS upload failed")

        result: list[BaseException] = []

        def target():
            try:
                mr.extract_members(
                    chunks(archive, 65536),
                    {"artist", "recording"},
                    fail_on_artist,
                    hashlib.sha256(archive).hexdigest(),
                    decompress_cmd=PY_BUNZIP,
                )
            except BaseException as e:  # noqa: BLE001
                result.append(e)

        t = threading.Thread(target=target, daemon=True)
        t.start()
        t.join(timeout=30)
        assert not t.is_alive(), "extract_members deadlocked"
        assert isinstance(result[0], mr.RefreshError)
        assert "GCS upload failed" in str(result[0])

    def test_download_error_raises(self):
        def broken():
            yield b"BZh9"
            raise ConnectionError("reset by peer")

        with pytest.raises(mr.RefreshError):
            mr.extract_members(broken(), {"artist"}, lambda t, f: f.read(), "0" * 64, decompress_cmd=PY_BUNZIP)


class TestLoadConfig:
    def test_raw_load_config_is_strict_postgres_copy_text(self):
        cfg = mr.raw_load_config(3)
        assert [f.name for f in cfg.schema] == ["c0", "c1", "c2"]
        assert all(f.field_type == "STRING" for f in cfg.schema)
        assert cfg.field_delimiter == "\t"
        assert cfg.quote_character == ""
        assert cfg.null_marker == "\\N"
        assert cfg.max_bad_records == 0
        assert cfg.allow_jagged_rows is False

    def test_gcs_uri(self):
        assert mr.gcs_uri("20260926-002121", "artist") == (
            "gs://nomadkaraoke-musicbrainz-data/staging/20260926-002121/artist.tsv"
        )


class TestRowCounts:
    def test_within_bounds_passes(self):
        staging = {name: 1000 for name in sql.ROW_COUNT_BOUNDS}
        prod = {name: 990 for name in sql.ROW_COUNT_BOUNDS}
        assert mr.check_row_counts(staging, prod) == []

    def test_drop_fails(self):
        staging = {name: 1000 for name in sql.ROW_COUNT_BOUNDS}
        prod = {name: 1000 for name in sql.ROW_COUNT_BOUNDS}
        staging["mb_recordings"] = 500
        failures = mr.check_row_counts(staging, prod)
        assert len(failures) == 1 and failures[0].startswith("mb_recordings:")

    def test_empty_staging_fails_even_without_baseline(self):
        staging = {name: 1000 for name in sql.ROW_COUNT_BOUNDS}
        staging["mb_artist_redirects"] = 0
        failures = mr.check_row_counts(staging, {})
        assert failures == ["mb_artist_redirects: staging table empty or missing"]

    def test_new_table_without_prod_baseline_passes(self):
        staging = {name: 1000 for name in sql.ROW_COUNT_BOUNDS}
        prod = {name: 1000 for name in sql.ROW_COUNT_BOUNDS}
        prod["mb_recording_redirects"] = None
        assert mr.check_row_counts(staging, prod) == []


class TestModels:
    def test_every_model_has_bounds(self):
        assert set(sql.ROW_COUNT_BOUNDS) == set(sql.MODELS)

    def test_models_only_depend_on_earlier_models(self):
        seen: set[str] = set()
        for name in sql.MODEL_ORDER:
            deps = set(re.findall(rf"`{re.escape(sql.S)}\.(\w+)`", sql.MODELS[name])) - {name}
            model_deps = {d for d in deps if not d.startswith("raw_")}
            assert model_deps <= seen, f"{name} depends on {model_deps - seen} built later"
            seen.add(name)

    def test_models_only_read_loaded_raw_tables(self):
        loaded = {f"raw_{t}" for tables in sql.RAW_TABLES.values() for t in tables}
        for name, body in sql.MODELS.items():
            raws = set(re.findall(rf"`{re.escape(sql.S)}\.(raw_\w+)`", body))
            assert raws <= loaded, f"{name} reads unloaded {raws - loaded}"

    def test_models_write_to_staging_not_prod(self):
        for name, body in sql.MODELS.items():
            assert f"CREATE OR REPLACE TABLE `{sql.S}.{name}`" in body

    def test_raw_column_references_within_table_width(self):
        widths = {f"raw_{t}": n for tables in sql.RAW_TABLES.values() for t, n in tables.items()}
        for name, body in sql.MODELS.items():
            aliases = dict(
                (alias, table) for table, alias in re.findall(rf"`{re.escape(sql.S)}\.(raw_\w+)` (\w+)", body)
            )
            for alias, col in re.findall(r"\b(\w+)\.c(\d+)\b", body):
                if alias in aliases:
                    assert int(col) < widths[aliases[alias]], f"{name}: {alias}.c{col} out of range"

    def test_model_script_includes_helpers(self):
        script = sql.model_script("mb_artists")
        assert "CREATE TEMP FUNCTION pg" in script
        assert "CREATE TEMP FUNCTION norm" in script

    def test_enriched_sql_shared_with_script(self):
        assert "CLUSTER BY name_normalized, artist_normalized" in sql.mb_recordings_enriched_sql("a.b", "a.c")


def _mock_bq(num_rows=1000, published=False, canary_ok=True):
    bq = MagicMock()
    bq.get_table.return_value = MagicMock(num_rows=num_rows, labels={})

    def query(q, job_config=None):
        job = MagicMock()
        if "status = 'success' AND dump_id" in q:
            job.result.return_value = [{"x": 1}] if published else []
        elif "ORDER BY finished_at DESC LIMIT 1" in q:
            job.result.return_value = []
        elif " AS ok" in q:
            job.result.return_value = [{"ok": canary_ok, "detail": "d"}]
        else:
            job.result.return_value = []
        job.total_bytes_billed = 0
        return job

    bq.query.side_effect = query
    return bq


class TestRun:
    def _run(self, bq, monkeypatch, **kwargs):
        extract = MagicMock()
        monkeypatch.setattr(mr, "extract_dump_to_gcs", extract)
        monkeypatch.setattr(mr, "load_raw_tables", MagicMock())
        gcs = MagicMock()
        rc = mr.run(bq, gcs, MagicMock(), dump_id="20260926-002121", **kwargs)
        return rc, extract

    def _statuses(self, bq):
        return [
            c.kwargs["job_config"].query_parameters[1].value
            for c in bq.query.call_args_list
            if "INSERT INTO" in c.args[0]
        ]

    def test_skips_already_published_dump(self, monkeypatch):
        bq = _mock_bq(published=True)
        rc, extract = self._run(bq, monkeypatch)
        assert rc == 0
        extract.assert_not_called()
        bq.copy_table.assert_not_called()

    def test_full_run_publishes_every_model_and_logs_success(self, monkeypatch):
        bq = _mock_bq()
        rc, extract = self._run(bq, monkeypatch)
        assert rc == 0
        extract.assert_called_once()
        copied = [c.args[1] for c in bq.copy_table.call_args_list]
        assert copied == [f"{sql.P}.{name}" for name in sql.MODEL_ORDER]
        assert self._statuses(bq) == ["success"]

    def test_failed_validation_never_touches_prod(self, monkeypatch):
        bq = _mock_bq(canary_ok=False)
        with pytest.raises(mr.RefreshError, match="Validation failed"):
            self._run(bq, monkeypatch)
        bq.copy_table.assert_not_called()
        assert self._statuses(bq) == ["failed"]

    def test_no_publish_leaves_staging(self, monkeypatch):
        bq = _mock_bq()
        rc, _ = self._run(bq, monkeypatch, do_publish=False)
        assert rc == 0
        bq.copy_table.assert_not_called()
        bq.delete_table.assert_not_called()

    def test_skip_extract_reuses_raw_tables(self, monkeypatch):
        bq = _mock_bq()
        _, extract = self._run(bq, monkeypatch, skip_extract=True, do_publish=False)
        extract.assert_not_called()

    def test_reuse_gcs_loads_without_downloading(self, monkeypatch):
        bq = _mock_bq()
        load = MagicMock()
        monkeypatch.setattr(mr, "extract_dump_to_gcs", extract := MagicMock())
        monkeypatch.setattr(mr, "load_raw_tables", load)
        mr.run(bq, MagicMock(), MagicMock(), dump_id="d", reuse_gcs=True, do_publish=False)
        extract.assert_not_called()
        load.assert_called_once_with(bq, "d")

    def test_main_returns_1_on_failure(self, monkeypatch):
        monkeypatch.setattr(mr.bigquery, "Client", MagicMock())
        monkeypatch.setattr(mr.storage, "Client", MagicMock())
        monkeypatch.setattr(mr, "run", MagicMock(side_effect=mr.RefreshError("boom")))
        assert mr.main(["run"]) == 1
