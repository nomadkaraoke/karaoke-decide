"""Tests for the ListenBrainz refresh pipeline (karaoke_decide.etl)."""

import hashlib
import io
import json
import re
import sys
import tarfile
import zlib
from datetime import UTC, datetime
from unittest.mock import MagicMock

import httpx
import pytest

from karaoke_decide.etl import listenbrainz_refresh as lr
from karaoke_decide.etl import listenbrainz_sql as sql
from karaoke_decide.etl.refresh_common import PUBLISH_ATTEMPTS, PartialPublishError, RefreshError
from tests.unit.fake_gcs import FakeBucket

# Portable stand-in for `zstd -dc` so tests don't need zstd installed: the
# test archives are zlib-compressed instead.
PY_UNZLIB = [
    sys.executable,
    "-c",
    "import sys,zlib; d=zlib.decompressobj(); "
    "[sys.stdout.buffer.write(d.decompress(c)) for c in iter(lambda: sys.stdin.buffer.read(65536), b'')]; "
    "sys.stdout.buffer.write(d.flush())",
]

ROOT = "listenbrainz-statistics-dump-20260915-000002"
DUMP_ID = "2663-20260915-000002"


def make_archive(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return zlib.compress(buf.getvalue())


def stats_line(user_id: int, items: list[dict]) -> bytes:
    return (
        json.dumps({"user_id": user_id, "count": len(items), "from_ts": 0, "to_ts": 1, "data": items}) + "\n"
    ).encode()


# --------------------------------------------------------------------------
# Dump discovery
# --------------------------------------------------------------------------

FULLEXPORT_HTML = """
<a href="../">../</a>
<a href="listenbrainz-dump-2647-20260901-000002-full/">listenbrainz-dump-2647-20260901-000002-full/</a>
<a href="listenbrainz-dump-2663-20260915-000002-full/">listenbrainz-dump-2663-20260915-000002-full/</a>
"""

STATS = "listenbrainz-statistics-dump-20260915-000002.tar.zst"
COMPLETE_DIR_HTML = f'<a href="{STATS}">x</a><a href="{STATS}.md5">x</a><a href="{STATS}.sha256">x</a>'
SHA = "be24cf7773f23c0fe32420ae27429547cd87aba55b5aad76724b216d1565b4e3"


def http_client(routes: dict[str, tuple[int, str]]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        status, body = routes.get(str(request.url), (404, "not found"))
        return httpx.Response(status, text=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def dir_url(dump_id: str) -> str:
    return f"{lr.dump_dir_url(dump_id)}/"


class TestDiscovery:
    def test_parse_dump_ids_newest_first(self):
        assert lr.parse_dump_ids(FULLEXPORT_HTML) == ["2663-20260915-000002", "2647-20260901-000002"]

    def test_parse_dump_ids_orders_numerically_not_lexically(self):
        html = (
            '<a href="listenbrainz-dump-999-20250101-000002-full/">'
            '<a href="listenbrainz-dump-1000-20250115-000002-full/">'
        )
        assert lr.parse_dump_ids(html)[0] == "1000-20250115-000002"

    def test_find_stats_dump_complete(self):
        http = http_client(
            {
                dir_url(DUMP_ID): (200, COMPLETE_DIR_HTML),
                f"{lr.dump_dir_url(DUMP_ID)}/{STATS}.sha256": (200, f"{SHA.upper()}  {STATS}\n"),
            }
        )
        dump = lr.find_stats_dump(http, DUMP_ID)
        assert dump == lr.StatsDump(DUMP_ID, f"{lr.dump_dir_url(DUMP_ID)}/{STATS}", SHA)

    def test_find_stats_dump_without_checksum_is_incomplete(self):
        http = http_client({dir_url(DUMP_ID): (200, f'<a href="{STATS}">x</a>')})
        assert lr.find_stats_dump(http, DUMP_ID) is None

    def test_find_stats_dump_bad_checksum_file_raises(self):
        http = http_client(
            {
                dir_url(DUMP_ID): (200, COMPLETE_DIR_HTML),
                f"{lr.dump_dir_url(DUMP_ID)}/{STATS}.sha256": (200, "<html>oops</html>"),
            }
        )
        with pytest.raises(RefreshError, match="Unparseable checksum"):
            lr.find_stats_dump(http, DUMP_ID)

    def test_resolve_dump_falls_back_to_previous_complete_export(self):
        older = "2647-20260901-000002"
        older_stats = "listenbrainz-statistics-dump-20260901-000002.tar.zst"
        http = http_client(
            {
                f"{lr.DUMP_BASE_URL}/": (200, FULLEXPORT_HTML),
                # Newest export still uploading: no statistics file yet.
                dir_url(DUMP_ID): (200, "<a href='x.tar'>"),
                dir_url(older): (200, f'<a href="{older_stats}"></a><a href="{older_stats}.sha256"></a>'),
                f"{lr.dump_dir_url(older)}/{older_stats}.sha256": (200, SHA),
            }
        )
        assert lr.resolve_dump(http).dump_id == older

    def test_resolve_dump_none_complete_raises(self):
        http = http_client({f"{lr.DUMP_BASE_URL}/": (200, FULLEXPORT_HTML)})
        with pytest.raises(RefreshError, match="No complete statistics dump"):
            lr.resolve_dump(http)

    def test_resolve_explicit_dump_id(self):
        http = http_client(
            {
                dir_url(DUMP_ID): (200, COMPLETE_DIR_HTML),
                f"{lr.dump_dir_url(DUMP_ID)}/{STATS}.sha256": (200, SHA),
            }
        )
        assert lr.resolve_dump(http, DUMP_ID).sha256 == SHA

    def test_resolve_explicit_missing_dump_id_raises(self):
        with pytest.raises(RefreshError):
            lr.resolve_dump(http_client({}), "1-20200101-000000")

    @pytest.mark.parametrize(
        "member,expected",
        [
            (f"{ROOT}/lbdump/statistics/artists_all_time.jsonl", "artists_all_time"),
            (f"{ROOT}/lbdump/statistics/recordings_this_week.jsonl", "recordings_this_week"),
            (f"{ROOT}/SCHEMA_SEQUENCE", None),
            (f"{ROOT}/COPYING", None),
            (f"{ROOT}/lbdump/statistics", None),
            (f"{ROOT}/lbdump/other/artists_all_time.jsonl", None),
            (f"{ROOT}/lbdump/statistics/sub/artists_all_time.jsonl", None),
        ],
    )
    def test_member_table_name(self, member, expected):
        assert lr.member_table_name(member) == expected

    def test_gcs_uri(self):
        assert lr.gcs_uri(DUMP_ID, "artists_all_time") == (
            f"gs://nomadkaraoke-musicbrainz-data/staging/listenbrainz/{DUMP_ID}/artists_all_time.jsonl"
        )


# --------------------------------------------------------------------------
# Streaming extract
# --------------------------------------------------------------------------


class TestExtract:
    def _archive(self, skip: str | None = None) -> bytes:
        files = {f"{ROOT}/SCHEMA_SEQUENCE": b"8\n", f"{ROOT}/TIMESTAMP": b"2026-09-15\n"}
        for name in sql.RAW_FILES:
            if name != skip:
                files[f"{ROOT}/lbdump/statistics/{name}.jsonl"] = stats_line(1, [{"listen_count": 3}])
        files[f"{ROOT}/lbdump/statistics/releases_all_time.jsonl"] = b"not wanted\n"
        files[f"{ROOT}/lbdump/statistics/daily_activity_week.jsonl"] = b"not wanted\n"
        return make_archive(files)

    def _run(self, archive: bytes, sha: str):
        bucket = FakeBucket()
        gcs = MagicMock()
        gcs.bucket.return_value = bucket
        dump = lr.StatsDump(DUMP_ID, "https://example.test/stats.tar.zst", sha)
        http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=archive)))
        state = lr.RunState(dump_id=DUMP_ID)
        lr.extract_dump_to_gcs(http, gcs, state, dump, decompress_cmd=PY_UNZLIB)
        uploaded = {k: v for k, v in bucket.objects.items() if k != lr.archive_blob_name(dump)}
        return uploaded, state

    def test_uploads_only_artist_and_recording_files(self):
        archive = self._archive()
        uploaded, state = self._run(archive, hashlib.sha256(archive).hexdigest())
        assert set(uploaded) == {lr.gcs_blob_name(DUMP_ID, name) for name in sql.RAW_FILES}
        assert state.schema_sequence == "8"
        first = uploaded[lr.gcs_blob_name(DUMP_ID, "artists_all_time")]
        assert json.loads(first)["data"] == [{"listen_count": 3}]

    def test_checksum_mismatch_raises(self):
        with pytest.raises(RefreshError, match="SHA256 mismatch"):
            self._run(self._archive(), "0" * 64)

    def test_missing_range_file_raises(self):
        archive = self._archive(skip="recordings_this_week")
        with pytest.raises(RefreshError, match="recordings_this_week"):
            self._run(archive, hashlib.sha256(archive).hexdigest())


# --------------------------------------------------------------------------
# SQL models
# --------------------------------------------------------------------------


class TestModels:
    def test_raw_files_cover_every_entity_and_range(self):
        assert len(sql.RAW_FILES) == len(sql.ENTITIES) * len(sql.STATS_RANGES) == 18

    def test_every_model_has_bounds(self):
        assert set(sql.ROW_COUNT_BOUNDS) == set(sql.MODELS)

    def test_models_write_to_staging_not_prod(self):
        for name, body in sql.MODELS.items():
            assert f"CREATE OR REPLACE TABLE `{sql.S}.{name}`" in body
            assert f"`{sql.P}.{name}`" not in body

    def test_models_only_read_loaded_raw_tables(self):
        loaded = {f"raw_{name}" for name in sql.RAW_FILES}
        for name, body in sql.MODELS.items():
            raws = set(re.findall(rf"`{re.escape(sql.S)}\.(raw_\w+)`", body))
            assert raws <= loaded, f"{name} reads unloaded {raws - loaded}"

    def test_popularity_models_read_every_range(self):
        for table, entity in [("lb_artist_popularity", "artists"), ("lb_recording_popularity", "recordings")]:
            raws = set(re.findall(rf"`{re.escape(sql.S)}\.raw_(\w+)`", sql.MODELS[table]))
            assert raws == {f"{entity}_{rng}" for rng in sql.STATS_RANGES}

    def test_popularity_models_resolve_redirects(self):
        assert f"`{sql.P}.mb_artist_redirects`" in sql.MODELS["lb_artist_popularity"]
        assert f"`{sql.P}.mb_recording_redirects`" in sql.MODELS["lb_recording_popularity"]
        assert f"`{sql.P}.mb_artist_redirects`" in sql.MODELS["lb_user_artist_listens"]

    def test_raw_schema_is_nested_and_minimal(self):
        schema = sql.raw_schema("recordings")
        data = next(f for f in schema if f.name == "data")
        assert data.mode == "REPEATED"
        assert [f.name for f in data.fields] == ["listen_count", "recording_mbid"]
        assert [f.name for f in sql.raw_schema("artists")][-1] == "data"

    def test_raw_load_config_ignores_unknown_fields(self):
        cfg = lr.raw_load_config("artists")
        assert cfg.source_format == "NEWLINE_DELIMITED_JSON"
        assert cfg.ignore_unknown_values is True
        assert cfg.max_bad_records == 0


# --------------------------------------------------------------------------
# Run orchestration
# --------------------------------------------------------------------------


def _mock_bq(num_rows=1000, published=False, canary_ok=True, last_success=None):
    bq = MagicMock()
    bq.get_table.return_value = MagicMock(num_rows=num_rows, labels={})

    def query(q, job_config=None):
        job = MagicMock()
        if "status = 'success' AND dump_id" in q:
            job.result.return_value = [{"x": 1}] if published else []
        elif "ORDER BY finished_at DESC LIMIT 1" in q:
            job.result.return_value = (
                [{"dump_id": last_success, "finished_at": datetime.now(UTC)}] if last_success else []
            )
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
        load = MagicMock()
        monkeypatch.setattr(lr, "resolve_dump", MagicMock(return_value=lr.StatsDump(DUMP_ID, "u", SHA)))
        monkeypatch.setattr(lr, "extract_dump_to_gcs", extract)
        monkeypatch.setattr(lr, "load_raw_tables", load)
        rc = lr.run(bq, MagicMock(), MagicMock(), **kwargs)
        return rc, extract, load

    def _statuses(self, bq):
        return [
            c.kwargs["job_config"].query_parameters[1].value
            for c in bq.query.call_args_list
            if "INSERT INTO" in c.args[0]
        ]

    def test_skips_already_published_dump(self, monkeypatch):
        bq = _mock_bq(published=True)
        rc, extract, _ = self._run(bq, monkeypatch)
        assert rc == 0
        extract.assert_not_called()
        bq.copy_table.assert_not_called()

    def test_skips_export_older_than_last_published(self, monkeypatch):
        bq = _mock_bq(last_success="2700-20261015-000002")
        rc, extract, _ = self._run(bq, monkeypatch)
        assert rc == 0
        extract.assert_not_called()
        bq.copy_table.assert_not_called()

    def test_force_publishes_older_export(self, monkeypatch):
        bq = _mock_bq(last_success="2700-20261015-000002")
        rc, extract, _ = self._run(bq, monkeypatch, force=True)
        assert rc == 0
        extract.assert_called_once()

    def test_newer_export_than_last_published_runs(self, monkeypatch):
        bq = _mock_bq(last_success="2647-20260901-000002")
        _, extract, _ = self._run(bq, monkeypatch)
        extract.assert_called_once()

    def test_full_run_publishes_every_model_with_label(self, monkeypatch):
        bq = _mock_bq()
        rc, extract, load = self._run(bq, monkeypatch)
        assert rc == 0
        extract.assert_called_once()
        load.assert_called_once_with(bq, DUMP_ID)
        assert [c.args[1] for c in bq.copy_table.call_args_list] == [f"{sql.P}.{n}" for n in sql.MODEL_ORDER]
        assert bq.update_table.call_args_list[0].args[0].labels == {"lb_dump": DUMP_ID}
        assert self._statuses(bq) == ["success"]
        deleted = {c.args[0] for c in bq.delete_table.call_args_list}
        assert f"{sql.S}.raw_recordings_all_time" in deleted and f"{sql.S}.lb_stats_ranges" in deleted

    def test_models_use_log_table_in_prod(self, monkeypatch):
        bq = _mock_bq()
        self._run(bq, monkeypatch)
        assert any(lr.LOG_TABLE in c.args[0] for c in bq.query.call_args_list if "CREATE TABLE" in c.args[0])

    def test_failed_validation_never_touches_prod(self, monkeypatch):
        bq = _mock_bq(canary_ok=False)
        with pytest.raises(RefreshError, match="Validation failed"):
            self._run(bq, monkeypatch)
        bq.copy_table.assert_not_called()
        assert self._statuses(bq) == ["failed"]

    def test_row_count_drop_fails_validation(self, monkeypatch):
        bq = _mock_bq()
        bq.get_table.side_effect = lambda t: MagicMock(num_rows=100 if t.startswith(sql.S) else 1000, labels={})
        with pytest.raises(RefreshError, match="ratio"):
            self._run(bq, monkeypatch)
        bq.copy_table.assert_not_called()

    def test_partial_publish_is_recorded_and_staging_kept(self, monkeypatch):
        bq = _mock_bq()

        def copy_table(src, dest, job_config=None):
            job = MagicMock()
            if dest.endswith(sql.MODEL_ORDER[1]):
                job.result.side_effect = RuntimeError("quota")
            return job

        bq.copy_table.side_effect = copy_table
        with pytest.raises(PartialPublishError) as exc:
            self._run(bq, monkeypatch)
        assert exc.value.published == sql.MODEL_ORDER[:1]
        assert bq.copy_table.call_count == 1 + PUBLISH_ATTEMPTS
        assert self._statuses(bq) == ["partial_publish"]
        bq.delete_table.assert_not_called()

    def test_no_publish_leaves_staging(self, monkeypatch):
        bq = _mock_bq()
        rc, _, _ = self._run(bq, monkeypatch, do_publish=False)
        assert rc == 0
        bq.copy_table.assert_not_called()
        bq.delete_table.assert_not_called()

    def test_skip_extract_needs_dump_id(self, monkeypatch):
        with pytest.raises(RefreshError, match="need --dump-id"):
            self._run(_mock_bq(), monkeypatch, skip_extract=True)

    def test_skip_extract_rebuilds_without_loading(self, monkeypatch):
        bq = _mock_bq()
        _, extract, load = self._run(bq, monkeypatch, dump_id=DUMP_ID, skip_extract=True, do_publish=False)
        extract.assert_not_called()
        load.assert_not_called()
        lr.resolve_dump.assert_not_called()  # type: ignore[attr-defined]

    def test_reuse_gcs_loads_without_downloading(self, monkeypatch):
        bq = _mock_bq()
        _, extract, load = self._run(bq, monkeypatch, dump_id=DUMP_ID, reuse_gcs=True, do_publish=False)
        extract.assert_not_called()
        load.assert_called_once_with(bq, DUMP_ID)

    def test_publish_existing(self, monkeypatch):
        bq = _mock_bq()
        assert lr.publish_existing(bq, MagicMock(), DUMP_ID) == 0
        assert len(bq.copy_table.call_args_list) == len(sql.MODEL_ORDER)
        assert self._statuses(bq) == ["success"]

    def test_load_raw_tables_loads_every_file(self):
        bq = _mock_bq()
        lr.load_raw_tables(bq, DUMP_ID)
        dests = [c.args[1] for c in bq.load_table_from_uri.call_args_list]
        assert dests == [f"{sql.S}.raw_{name}" for name in sql.RAW_FILES]

    def test_load_failure_raises_refresh_error(self):
        bq = _mock_bq()
        bq.load_table_from_uri.return_value.result.side_effect = RuntimeError("bad json")
        with pytest.raises(RefreshError, match="Load into"):
            lr.load_raw_tables(bq, DUMP_ID)

    def test_build_models_in_order(self):
        bq = _mock_bq()
        lr.build_models(bq)
        scripts = [c.args[0] for c in bq.query.call_args_list]
        assert scripts == [sql.MODELS[n] for n in sql.MODEL_ORDER]

    def test_main_returns_1_on_failure(self, monkeypatch):
        monkeypatch.setattr(lr.bigquery, "Client", MagicMock())
        monkeypatch.setattr(lr.storage, "Client", MagicMock())
        monkeypatch.setattr(lr, "run", MagicMock(side_effect=RefreshError("boom")))
        assert lr.main(["run"]) == 1

    def test_main_status(self, monkeypatch):
        monkeypatch.setattr(lr.bigquery, "Client", MagicMock(return_value=_mock_bq()))
        assert lr.main(["status"]) == 0
