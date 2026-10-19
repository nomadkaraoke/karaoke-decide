"""Tests for the song-index-build job (karaoke_decide.etl.song_index_build).

GCS and the Cloud Run Admin API are replaced by in-memory fakes; the indexes are
really built from the shared song_search fixtures."""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from google.api_core.exceptions import NotFound
from google.cloud import run_v2

from backend.search import index_loader
from karaoke_decide.etl import song_index_build as sib
from karaoke_decide.song_search.text_normalize import NORMALIZER_VERSION

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "song_search"
SRC = sib.SOURCE_BUCKET
IDX = sib.INDEX_BUCKET
PUB = sib.PUBLIC_BUCKET
SOURCE_RUN = "20261018-133000"


class FakeStore:
    """The job's storage interface over a dict; records the write order."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.attrs: dict[tuple[str, str], dict[str, Any]] = {}
        self.writes: list[tuple[str, str]] = []
        self.deletes: list[tuple[str, str]] = []

    def read_bytes(self, bucket: str, name: str) -> bytes | None:
        return self.objects.get((bucket, name))

    def download(self, bucket: str, name: str, dest: Path) -> None:
        dest.write_bytes(self.objects[(bucket, name)])

    def upload_file(
        self, bucket: str, name: str, src: Path, *, content_type: str, cache_control: str | None = None
    ) -> None:
        self.put(bucket, name, Path(src).read_bytes(), content_type=content_type, cache_control=cache_control)

    def upload_bytes(self, bucket: str, name: str, data: bytes, *, content_type: str) -> None:
        self.put(bucket, name, data, content_type=content_type)

    def list_names(self, bucket: str, prefix: str) -> list[str]:
        return sorted(n for b, n in self.objects if b == bucket and n.startswith(prefix))

    def delete(self, bucket: str, name: str) -> None:
        self.objects.pop((bucket, name), None)
        self.deletes.append((bucket, name))

    def put(self, bucket: str, name: str, data: bytes, **attrs: Any) -> None:
        self.objects[(bucket, name)] = data
        self.attrs[(bucket, name)] = attrs
        self.writes.append((bucket, name))


class FakeAdmin:
    def __init__(self, fail: bool = False, pinned: list[str] | None = None, live_runs: set[str] | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail = fail
        self.pinned = pinned
        self.live_runs = live_runs or set()

    def set_env(self, name: str, value: str) -> sib.Rollout:
        self.calls.append((name, value))
        if self.fail:
            raise RuntimeError("revision failed its startup probe")
        if self.pinned is not None:
            return sib.Rollout(serving=False, pinned=self.pinned)
        self.live_runs = {value}
        return sib.Rollout(serving=True)

    def serving_env_values(self, name: str) -> set[str]:
        assert name == "SONG_INDEX_RUN"
        return set(self.live_runs)


def _gz(data: bytes) -> bytes:
    return gzip.compress(data, mtime=0)


def seed_sources(store: FakeStore, run: str = SOURCE_RUN) -> None:
    shard = f"song-id/{run}/songs-000000000000.tsv.gz"
    store.put(SRC, shard, _gz((FIXTURES / "songs_fixture.tsv").read_bytes()))
    store.put(
        SRC,
        "song-id/latest.json",
        json.dumps({"run": run, "shards": [f"gs://{SRC}/{shard}"], "columns": []}).encode(),
    )
    store.put(SRC, "full/full-data-latest.json.gz", _gz((FIXTURES / "kn_full_fixture.json").read_bytes()))
    store.put(
        SRC, "community/community-data-latest.json.gz", _gz((FIXTURES / "kn_community_fixture.json").read_bytes())
    )
    store.writes.clear()


def manifest_of(store: FakeStore, index_run: str) -> dict[str, Any]:
    return json.loads(store.objects[(IDX, f"song-index/{index_run}/manifest.json")])


@pytest.fixture
def store() -> FakeStore:
    s = FakeStore()
    seed_sources(s)
    return s


@pytest.fixture(scope="module")
def published(tmp_path_factory: pytest.TempPathFactory) -> tuple[FakeStore, FakeAdmin, dict[str, Any]]:
    """One real build + publish over the fixtures (shared: a build takes seconds)."""
    s = FakeStore()
    seed_sources(s)
    admin = FakeAdmin()
    out = sib.build_and_publish(storage=s, run_admin=admin, workdir=tmp_path_factory.mktemp("work"))
    return s, admin, out


# ---- a new source run is built, published and deployed -------------------------------


def test_new_run_publishes_all_files(published: tuple[FakeStore, FakeAdmin, dict[str, Any]]) -> None:
    store, _admin, out = published
    assert out["published"] is True
    assert out["run"] == SOURCE_RUN
    assert re.fullmatch(r"[0-9a-f]{16}", out["token"])
    index_run = out["index_run"]
    assert index_run == f"{SOURCE_RUN}-{out['token']}"
    for name in ("song_id.db", "availability.db", "manifest.json"):
        assert (IDX, f"song-index/{index_run}/{name}") in store.objects
    # The phone's file lives in the dedicated public bucket (uniform access on kn-data).
    assert (PUB, f"{index_run}/offline.db.gz") in store.objects
    assert (IDX, f"song-index/{index_run}/offline.db.gz") not in store.objects


def test_manifest_hashes_match_uploaded_bytes(published: tuple[FakeStore, FakeAdmin, dict[str, Any]]) -> None:
    store, _admin, out = published
    m = manifest_of(store, out["index_run"])
    assert m["run"] == SOURCE_RUN and m["token"] == out["token"]
    assert m["schema"] == sib.SCHEMA_VERSION and m["normalizer"] == NORMALIZER_VERSION
    assert m["offline_schema"] == sib.OFFLINE_SCHEMA_VERSION
    locations = {
        "song_id.db": (IDX, f"song-index/{out['index_run']}/song_id.db"),
        "availability.db": (IDX, f"song-index/{out['index_run']}/availability.db"),
        "offline.db.gz": (PUB, f"{out['index_run']}/offline.db.gz"),
    }
    assert set(m["files"]) == set(locations)
    for name, key in locations.items():
        data = store.objects[key]
        assert m["files"][name]["bytes"] == len(data)
        assert m["files"][name]["sha256"] == hashlib.sha256(data).hexdigest()
    assert m["files"]["offline.db.gz"]["bucket"] == PUB
    assert m["files"]["offline.db.gz"]["path"] == f"{out['index_run']}/offline.db.gz"
    # The phone downloads raw gzip bytes (no transcoding) and caches the immutable path.
    attrs = store.attrs[locations["offline.db.gz"]]
    assert attrs["content_type"] == "application/gzip"
    assert "immutable" in (attrs.get("cache_control") or "")


def test_offline_file_is_a_gzipped_offline_db(
    published: tuple[FakeStore, FakeAdmin, dict[str, Any]], tmp_path: Path
) -> None:
    import sqlite3

    store, _admin, out = published
    db = tmp_path / "offline.db"
    db.write_bytes(gzip.decompress(store.objects[(PUB, f"{out['index_run']}/offline.db.gz")]))
    conn = sqlite3.connect(db)
    try:
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    finally:
        conn.close()
    assert meta["offline_schema_version"] == str(sib.OFFLINE_SCHEMA_VERSION)
    assert meta["run"] == SOURCE_RUN


def test_manifest_records_eval_dual_eval_and_build_stats(
    published: tuple[FakeStore, FakeAdmin, dict[str, Any]],
) -> None:
    store, _admin, out = published
    m = manifest_of(store, out["index_run"])
    assert {"total", "auto", "wrong_auto", "candidates", "none"} <= set(m["eval"])
    assert m["eval"]["total"] == len(sib.load_eval_cases())
    d = m["dual_eval"]
    assert {
        "total",
        "confident_disagree",
        "offline_downgraded",
        "downgraded_cloud_best_karaoke",
        "downgraded_cloud_best_non_karaoke",
        "disagree_rate",
        "max_disagree",
    } <= set(d)
    assert d["total"] == len(sib.load_eval_cases()) + len(sib.load_dual_eval_cases())
    assert d["max_disagree"] == sib.DUAL_EVAL_MAX_DISAGREE
    # A10 sizes the job from these.
    assert set(m["build"]["seconds"]) >= {"download", "song_id", "availability", "offline", "eval", "dual_eval"}
    assert m["build"]["peak_rss_mib"] > 0


def test_latest_json_written_after_every_file(published: tuple[FakeStore, FakeAdmin, dict[str, Any]]) -> None:
    store, _admin, out = published
    latest_at = store.writes.index((IDX, "song-index/latest.json"))
    run_writes = [i for i, (_b, n) in enumerate(store.writes) if out["index_run"] in n]
    assert len(run_writes) == 4
    assert max(run_writes) < latest_at
    # manifest.json is the last file of the run directory written
    assert store.writes[max(run_writes)] == (IDX, f"song-index/{out['index_run']}/manifest.json")
    assert json.loads(store.objects[(IDX, "song-index/latest.json")]) == {"run": SOURCE_RUN, "token": out["token"]}


def test_deploy_called_once_with_new_env(published: tuple[FakeStore, FakeAdmin, dict[str, Any]]) -> None:
    _store, admin, out = published
    assert admin.calls == [("SONG_INDEX_RUN", out["index_run"])]


def test_published_index_loads_through_the_search_service_loader(
    published: tuple[FakeStore, FakeAdmin, dict[str, Any]], tmp_path: Path
) -> None:
    """The manifest format is exactly what backend.search.index_loader reads."""
    store, _admin, out = published

    class _Blob:
        def __init__(self, bucket: str, name: str) -> None:
            self.bucket, self.name = bucket, name

        def download_as_bytes(self) -> bytes:
            return store.objects[(self.bucket, self.name)]

        def download_to_filename(self, path: str) -> None:
            Path(path).write_bytes(store.objects[(self.bucket, self.name)])

    class _Client:
        def bucket(self, name: str) -> Any:
            return type("B", (), {"blob": lambda _self, n: _Blob(name, n)})()

    for run in (out["index_run"], ""):  # explicit SONG_INDEX_RUN, and latest.json
        paths = index_loader.load(run, IDX, tmp_path / (run or "latest"), client=_Client())
        assert paths.run == out["index_run"]
        loaded = index_loader.open_index(paths)
        assert loaded.info.run == out["index_run"]
        loaded.identifier.close()
        loaded.availability.close()
        offline = index_loader.offline_manifest(paths)
        data = store.objects[(PUB, f"{out['index_run']}/offline.db.gz")]
        assert offline is not None
        assert offline["bytes"] == len(data) and offline["sha256"] == hashlib.sha256(data).hexdigest()
        assert offline["schema"] == sib.OFFLINE_SCHEMA_VERSION


def test_job_layout_matches_the_loader() -> None:
    assert sib.INDEX_PREFIX == index_loader.PREFIX
    assert sib.OFFLINE_FILE == index_loader.OFFLINE_FILE
    assert set(index_loader.SERVER_FILES) == set(sib.SERVER_FILES)


# ---- skip / force -----------------------------------------------------------------------


def test_unchanged_source_is_skipped_without_deploy(
    published: tuple[FakeStore, FakeAdmin, dict[str, Any]], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store, _admin, _out = published
    admin = FakeAdmin()
    n_writes = len(store.writes)
    with caplog.at_level(logging.INFO):
        out = sib.build_and_publish(storage=store, run_admin=admin, workdir=tmp_path)
    assert out["skipped"] is True
    assert admin.calls == []
    assert store.writes[n_writes:] == []
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_force_rebuilds_an_unchanged_source(store: FakeStore, tmp_path: Path) -> None:
    first = sib.build_and_publish(storage=store, run_admin=FakeAdmin(), workdir=tmp_path / "a")
    admin = FakeAdmin()
    second = sib.build_and_publish(storage=store, run_admin=admin, workdir=tmp_path / "b", force=True)
    assert second["published"] is True
    assert second["token"] != first["token"]
    assert admin.calls == [("SONG_INDEX_RUN", second["index_run"])]


def test_new_source_run_is_rebuilt(store: FakeStore, tmp_path: Path) -> None:
    sib.build_and_publish(storage=store, run_admin=FakeAdmin(), workdir=tmp_path / "a")
    seed_sources(store, run="20261019-133000")
    out = sib.build_and_publish(storage=store, run_admin=FakeAdmin(), workdir=tmp_path / "b")
    assert out["published"] is True and out["run"] == "20261019-133000"


def test_version_change_is_rebuilt(store: FakeStore, tmp_path: Path) -> None:
    first = sib.build_and_publish(storage=store, run_admin=FakeAdmin(), workdir=tmp_path / "a")
    key = (IDX, f"song-index/{first['index_run']}/manifest.json")
    m = json.loads(store.objects[key])
    m["normalizer"] = NORMALIZER_VERSION - 1
    store.objects[key] = json.dumps(m).encode()
    out = sib.build_and_publish(storage=store, run_admin=FakeAdmin(), workdir=tmp_path / "b")
    assert out.get("published") is True


# ---- gates ------------------------------------------------------------------------------


def _eval(total: int, auto: int, wrong_auto: int) -> dict[str, Any]:
    return {"total": total, "auto": auto, "wrong_auto": wrong_auto, "candidates": 0, "none": 0, "wrong": []}


def _dual(total: int, disagree: int) -> dict[str, Any]:
    return {
        "total": total,
        "confident_disagree": disagree,
        "offline_downgraded": 0,
        "downgraded_cloud_best_karaoke": 0,
        "downgraded_cloud_best_non_karaoke": 0,
        "disagreements": [],
    }


def test_gates_pass_at_the_thresholds() -> None:
    prev = _eval(100, 90, 0)
    assert sib.gate_failures(_eval(100, 88, 1), _dual(100, 1), prev) == []  # -2 points, +1 wrong, 1%
    assert sib.gate_failures(_eval(100, 50, 0), _dual(0, 0), None) == []  # no baseline: dual gate only


def test_gate_wrong_auto_rise() -> None:
    failures = sib.gate_failures(_eval(100, 90, 2), _dual(100, 0), _eval(100, 90, 0))
    assert len(failures) == 1 and "wrong_auto" in failures[0]


def test_gate_auto_rate_drop() -> None:
    failures = sib.gate_failures(_eval(100, 87, 0), _dual(100, 0), _eval(100, 90, 0))
    assert len(failures) == 1 and "auto" in failures[0]
    # rate, not count: more cases with the same rate is fine
    assert sib.gate_failures(_eval(200, 180, 0), _dual(100, 0), _eval(100, 90, 0)) == []


def test_gate_dual_eval_disagreement() -> None:
    failures = sib.gate_failures(_eval(100, 90, 0), _dual(100, 2), _eval(100, 90, 0))
    assert len(failures) == 1 and "confident_disagree" in failures[0]


def _assert_not_published(store: FakeStore, admin: FakeAdmin, before: dict[tuple[str, str], bytes]) -> None:
    assert admin.calls == []
    after = {k: v for k, v in store.objects.items() if k != (IDX, sib.REPORTED_FAILURES)}
    assert after == before  # nothing uploaded, latest.json untouched


def _errors(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_eval_regression_blocks_publish(
    store: FakeStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    first = sib.build_and_publish(storage=store, run_admin=FakeAdmin(), workdir=tmp_path / "a")
    prev_eval = manifest_of(store, first["index_run"])["eval"]
    worse = {**prev_eval, "wrong_auto": prev_eval["wrong_auto"] + 2}
    monkeypatch.setattr(sib.song_eval, "run_eval", lambda _ident, _cases: worse)
    before, admin = dict(store.objects), FakeAdmin()
    with caplog.at_level(logging.INFO):
        out = sib.build_and_publish(storage=store, run_admin=admin, workdir=tmp_path / "b", force=True)
    assert out["published"] is False
    _assert_not_published(store, admin, before)
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    msg = errors[0].getMessage()
    assert "wrong_auto" in msg and SOURCE_RUN in msg and "not published" in msg


def test_auto_rate_regression_blocks_publish(
    store: FakeStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    first = sib.build_and_publish(storage=store, run_admin=FakeAdmin(), workdir=tmp_path / "a")
    prev_eval = manifest_of(store, first["index_run"])["eval"]
    worse = {**prev_eval, "auto": prev_eval["auto"] - 4}  # 4/123 = 3.3 points
    monkeypatch.setattr(sib.song_eval, "run_eval", lambda _ident, _cases: worse)
    before, admin = dict(store.objects), FakeAdmin()
    with caplog.at_level(logging.INFO):
        out = sib.build_and_publish(storage=store, run_admin=admin, workdir=tmp_path / "b", force=True)
    assert out["published"] is False
    _assert_not_published(store, admin, before)
    assert len([r for r in caplog.records if r.levelno >= logging.ERROR]) == 1


def test_dual_eval_disagreement_blocks_publish(
    store: FakeStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(sib.song_eval, "dual_eval", lambda _c, _o, cases: _dual(len(cases), 50))
    before, admin = dict(store.objects), FakeAdmin()
    with caplog.at_level(logging.INFO):
        out = sib.build_and_publish(storage=store, run_admin=admin, workdir=tmp_path)
    assert out["published"] is False
    _assert_not_published(store, admin, before)
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and "confident_disagree" in errors[0].getMessage()


def test_dual_eval_disagreements_logged_capped_and_in_manifest(
    store: FakeStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    side = {"status": "confident", "artist": "A", "title": "T"}
    items = [{"q": f"d{i}", "type": "offline_downgraded", "cloud": side, "offline": side} for i in range(150)]
    items += [{"q": f"c{i}", "type": "confident_disagree", "cloud": side, "offline": side} for i in range(30)]

    def fake(_c: Any, _o: Any, cases: list[Any]) -> dict[str, Any]:
        return {**_dual(len(cases), 30), "offline_downgraded": 150, "disagreements": items}

    monkeypatch.setattr(sib.song_eval, "dual_eval", fake)
    with caplog.at_level(logging.INFO):
        out = sib.build_and_publish(storage=store, run_admin=FakeAdmin(), workdir=tmp_path)
    assert out["published"] is False  # 30 confident disagreements trips the gate
    rec = next(r for r in caplog.records if "Dual eval disagreements" in r.getMessage())
    assert rec.levelno == logging.INFO
    logged = rec.json_fields["dual_eval_disagreements"]  # type: ignore[attr-defined]
    assert len(logged) == sib.DUAL_EVAL_DISAGREEMENTS_CAP == 100
    assert [d["type"] for d in logged[:30]] == ["confident_disagree"] * 30
    assert logged[30]["type"] == "offline_downgraded"
    assert '"q": "c0"' in rec.getMessage() and "first 100 of 180" in rec.getMessage()
    assert len(_errors(caplog)) == 1


def test_cap_disagreements_caps_and_orders_confident_first() -> None:
    many = [{"q": f"q{i}", "type": "offline_downgraded"} for i in range(250)]
    assert len(sib.cap_disagreements(many)) == 100
    assert sib.cap_disagreements([many[0], {"q": "x", "type": "confident_disagree"}])[0]["q"] == "x"


# ---- deploy failure + pruning -------------------------------------------------------------


def test_deploy_failure_leaves_latest_untouched(store: FakeStore, tmp_path: Path) -> None:
    admin = FakeAdmin(fail=True)
    with pytest.raises(RuntimeError, match="startup probe"):
        sib.build_and_publish(storage=store, run_admin=admin, workdir=tmp_path)
    assert len(admin.calls) == 1
    assert (IDX, "song-index/latest.json") not in store.objects  # next run retries (not skipped)


def test_keeps_the_previous_three_runs(store: FakeStore, tmp_path: Path) -> None:
    old = [f"20261010-13300{i}-{i:016x}" for i in range(5)]
    for run in old:
        store.put(IDX, f"song-index/{run}/manifest.json", b"{}")
        store.put(IDX, f"song-index/{run}/song_id.db", b"x")
        store.put(PUB, f"{run}/offline.db.gz", b"x")
    out = sib.build_and_publish(storage=store, run_admin=FakeAdmin(), workdir=tmp_path)
    kept = {n.split("/")[1] for n in store.list_names(IDX, "song-index/") if n.count("/") == 2}
    assert kept == {out["index_run"], *old[-3:]}
    kept_public = {n.split("/")[0] for n in store.list_names(PUB, "")}
    assert kept_public == {out["index_run"], *old[-3:]}


def test_pinned_traffic_skips_latest_and_pruning(
    store: FakeStore, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    for i in range(5):
        store.put(IDX, f"song-index/20261010-13300{i}-{i:016x}/manifest.json", b"{}")
    store.deletes.clear()
    admin = FakeAdmin(pinned=["karaoke-decide-search-00041-old"])
    with caplog.at_level(logging.INFO):
        out = sib.build_and_publish(storage=store, run_admin=admin, workdir=tmp_path)
    assert out["published"] is False and out["pinned"] == ["karaoke-decide-search-00041-old"]
    assert len(admin.calls) == 1
    assert (IDX, "song-index/latest.json") not in store.objects
    assert store.deletes == []
    errors = _errors(caplog)
    assert len(errors) == 1
    msg = errors[0].getMessage()
    assert "pinned to karaoke-decide-search-00041-old" in msg and out["index_run"] in msg
    assert "latest.json and pruning skipped" in msg
    # the next day: same pin → INFO, not another page
    caplog.clear()
    with caplog.at_level(logging.INFO):
        sib.build_and_publish(storage=store, run_admin=admin, workdir=tmp_path / "again")
    assert _errors(caplog) == []


def test_prune_keeps_runs_live_revisions_serve(store: FakeStore, tmp_path: Path) -> None:
    old = [f"20261010-13300{i}-{i:016x}" for i in range(5)]
    for run in old:
        store.put(IDX, f"song-index/{run}/manifest.json", b"{}")
        store.put(PUB, f"{run}/offline.db.gz", b"x")

    class SplitTraffic(FakeAdmin):
        def serving_env_values(self, name: str) -> set[str]:
            return {*self.live_runs, old[0]}  # e.g. a tagged/canary revision on the oldest run

    out = sib.build_and_publish(storage=store, run_admin=SplitTraffic(), workdir=tmp_path)
    kept = {n.split("/")[1] for n in store.list_names(IDX, "song-index/") if n.count("/") == 2}
    assert kept == {out["index_run"], old[0], *old[-3:]}
    assert {n.split("/")[0] for n in store.list_names(PUB, "")} == {out["index_run"], old[0], *old[-3:]}


def test_prune_skipped_when_live_runs_unknown(
    store: FakeStore, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    for i in range(5):
        store.put(IDX, f"song-index/20261010-13300{i}-{i:016x}/manifest.json", b"{}")
    store.deletes.clear()

    class Blind(FakeAdmin):
        def serving_env_values(self, name: str) -> set[str]:
            raise RuntimeError("permission denied")

    with caplog.at_level(logging.INFO):
        out = sib.build_and_publish(storage=store, run_admin=Blind(), workdir=tmp_path)
    assert out["published"] is True
    assert store.deletes == []
    assert _errors(caplog) == [] and any("Pruning skipped" in r.getMessage() for r in caplog.records)


def test_repeated_gate_failure_alerts_once(
    store: FakeStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(sib.song_eval, "dual_eval", lambda _c, _o, cases: _dual(len(cases), 50))
    with caplog.at_level(logging.INFO):
        sib.build_and_publish(storage=store, run_admin=FakeAdmin(), workdir=tmp_path / "a")
    assert len(_errors(caplog)) == 1
    caplog.clear()
    with caplog.at_level(logging.INFO):  # tomorrow, same source run + versions
        out = sib.build_and_publish(storage=store, run_admin=FakeAdmin(), workdir=tmp_path / "b")
    assert out["published"] is False
    assert _errors(caplog) == []
    assert any("Already reported" in r.getMessage() for r in caplog.records)
    caplog.clear()
    seed_sources(store, run="20261019-133000")  # a new source run that still fails pages again
    with caplog.at_level(logging.INFO):
        sib.build_and_publish(storage=store, run_admin=FakeAdmin(), workdir=tmp_path / "c")
    assert len(_errors(caplog)) == 1


def test_main_exit_code_first_failure_1_repeat_0(
    store: FakeStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a first-time failure fails the Cloud Run task; a repeat is already alerted."""
    monkeypatch.setattr(sib.song_eval, "dual_eval", lambda _c, _o, cases: _dual(len(cases), 50))
    monkeypatch.setattr(sib, "GcsStore", lambda *_a, **_k: store)
    monkeypatch.setattr(sib, "CloudRunAdmin", lambda *_a, **_k: FakeAdmin())
    monkeypatch.setattr(sib, "_storage_client", lambda: None)
    monkeypatch.setattr(sib.common, "configure_logging", lambda: None)
    assert sib.main(["run", "--workdir", str(tmp_path / "a")]) == 1
    assert sib.main(["run", "--workdir", str(tmp_path / "b")]) == 0


def test_download_stage_reaches_manifest_build_stats(
    published: tuple[FakeStore, FakeAdmin, dict[str, Any]],
) -> None:
    store, _admin, out = published
    build = manifest_of(store, out["index_run"])["build"]
    assert list(build["seconds"])[0] == "download"
    assert build["peak_workdir_mib"] > 0


# ---- production adapters -----------------------------------------------------------------


LATEST = run_v2.TrafficTargetAllocationType.TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST
PINNED = run_v2.TrafficTargetAllocationType.TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION
SVC = "projects/p/locations/r/services/s"
SECRET = run_v2.EnvVar(
    name="DECIDE_GEN_PARTNER_SECRET",
    value_source=run_v2.EnvVarSource(
        secret_key_ref=run_v2.SecretKeySelector(secret="decide-gen-partner-secret", version="latest")
    ),
)


def _env(env: dict[str, str]) -> list[run_v2.EnvVar]:
    return [run_v2.EnvVar(name=k, value=v) for k, v in env.items()]


def _service(env: dict[str, str], traffic: list[run_v2.TrafficTarget] | None = None) -> run_v2.Service:
    return run_v2.Service(
        name=SVC,
        template=run_v2.RevisionTemplate(
            revision="s-00042-abc",
            containers=[run_v2.Container(name="app", env=[*_env(env), SECRET])],
        ),
        traffic=traffic if traffic is not None else [run_v2.TrafficTarget(type_=LATEST, percent=100)],
    )


def _admin_client(env: dict[str, str], traffic: list[run_v2.TrafficTarget] | None = None) -> MagicMock:
    client = MagicMock()
    client.get_service.return_value = _service(env)
    client.update_service.return_value.result.return_value = _service(env, traffic)
    return client


@pytest.mark.parametrize("env", [{"GEN_API_URL": "x", "SONG_INDEX_RUN": "old"}, {"GEN_API_URL": "x"}])
def test_cloud_run_admin_updates_the_env(env: dict[str, str]) -> None:
    client = _admin_client(env)
    rollout = sib.CloudRunAdmin(SVC, client=client).set_env("SONG_INDEX_RUN", "new-run")
    assert rollout == sib.Rollout(serving=True)
    client.get_service.assert_called_once_with(name=SVC)
    svc = client.update_service.call_args.kwargs["service"]
    got = {e.name: e.value for e in svc.template.containers[0].env}
    assert got == {"GEN_API_URL": "x", "SONG_INDEX_RUN": "new-run", "DECIDE_GEN_PARTNER_SECRET": ""}
    secret = [e for e in svc.template.containers[0].env if e.name == "DECIDE_GEN_PARTNER_SECRET"][0]
    assert secret.value_source.secret_key_ref.secret == "decide-gen-partner-secret"  # secret ref kept as a ref
    assert secret.value_source.secret_key_ref.version == "latest"
    assert svc.template.revision == ""  # a fresh revision name, never a clash with the current one
    client.update_service.return_value.result.assert_called_once()


def test_cloud_run_admin_reports_pinned_traffic() -> None:
    client = _admin_client({}, [run_v2.TrafficTarget(type_=PINNED, revision="s-00041-old", percent=100)])
    rollout = sib.CloudRunAdmin(SVC, client=client).set_env("SONG_INDEX_RUN", "new-run")
    assert rollout == sib.Rollout(serving=False, pinned=["s-00041-old"])


def test_cloud_run_admin_picks_the_serving_container() -> None:
    sidecar = run_v2.Container(name="sidecar")
    main = run_v2.Container(name="app", ports=[run_v2.ContainerPort(container_port=8000)])
    assert sib._main_container([sidecar, main]) is not None
    assert sib._main_container([sidecar, main]).name == "app"
    with pytest.raises(RuntimeError, match="serving container"):
        sib._main_container([sidecar, run_v2.Container(name="other")])


def test_cloud_run_admin_serving_env_values() -> None:
    client, revisions = MagicMock(), MagicMock()
    client.get_service.return_value = run_v2.Service(
        name=SVC,
        latest_ready_revision=f"{SVC}/revisions/s-00043-new",
        traffic_statuses=[
            run_v2.TrafficTargetStatus(type_=PINNED, revision="s-00041-old", percent=90),
            run_v2.TrafficTargetStatus(type_=LATEST, percent=10),  # revision resolved via latest_ready
            run_v2.TrafficTargetStatus(type_=PINNED, revision="s-00040-older", percent=0),
        ],
    )
    runs = {f"{SVC}/revisions/s-00041-old": "run-a", f"{SVC}/revisions/s-00043-new": "run-b"}
    revisions.get_revision.side_effect = lambda name: run_v2.Revision(
        containers=[run_v2.Container(env=[*_env({"SONG_INDEX_RUN": runs[name]}), SECRET])]
    )
    admin = sib.CloudRunAdmin(SVC, client=client, revisions_client=revisions)
    assert admin.serving_env_values("SONG_INDEX_RUN") == {"run-a", "run-b"}
    assert revisions.get_revision.call_count == 2  # 0% revisions aren't read


def test_gcs_store_reads_missing_as_none() -> None:
    client = MagicMock()
    client.bucket.return_value.blob.return_value.download_as_bytes.side_effect = NotFound("nope")
    assert sib.GcsStore(client).read_bytes("b", "n") is None
    client.bucket.return_value.blob.return_value.download_as_bytes.side_effect = None
    client.bucket.return_value.blob.return_value.download_as_bytes.return_value = b"{}"
    assert sib.GcsStore(client).read_bytes("b", "n") == b"{}"


def test_gcs_store_upload_sets_metadata(tmp_path: Path) -> None:
    client = MagicMock()
    f = tmp_path / "x"
    f.write_bytes(b"x")
    sib.GcsStore(client).upload_file("b", "n", f, content_type="application/gzip", cache_control="public")
    blob = client.bucket.return_value.blob.return_value
    assert blob.cache_control == "public"
    blob.upload_from_filename.assert_called_once()
    assert blob.upload_from_filename.call_args.kwargs["content_type"] == "application/gzip"


# ---- CLI + eval cases -----------------------------------------------------------------------


def test_dry_run_builds_locally_without_gcp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _no_gcp(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("dry run must not touch GCP")

    monkeypatch.setattr(sib, "GcsStore", _no_gcp)
    monkeypatch.setattr(sib, "CloudRunAdmin", _no_gcp)
    assert sib.main(["run", "--dry-run", "--workdir", str(tmp_path)]) == 0
    m = json.loads((tmp_path / "manifest.json").read_text())
    for name in ("song_id.db", "availability.db", "offline.db.gz"):
        f = tmp_path / name
        assert m["files"][name]["sha256"] == hashlib.sha256(f.read_bytes()).hexdigest()


def test_main_returns_nonzero_on_gate_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sib.song_eval, "dual_eval", lambda _c, _o, cases: _dual(len(cases), 50))
    assert sib.main(["run", "--dry-run", "--workdir", str(tmp_path)]) == 1


def test_main_logs_one_error_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    store = FakeStore()  # no song-id/latest.json
    monkeypatch.setattr(sib, "GcsStore", lambda *_a, **_k: store)
    monkeypatch.setattr(sib, "CloudRunAdmin", lambda *_a, **_k: FakeAdmin())
    monkeypatch.setattr(sib, "_storage_client", lambda: None)
    monkeypatch.setattr(sib.common, "configure_logging", lambda: None)
    with caplog.at_level(logging.INFO):
        assert sib.main(["run", "--workdir", str(tmp_path)]) == 1
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and "song-id/latest.json" in errors[0].getMessage()


def test_packaged_eval_cases_match_the_test_fixtures() -> None:
    """The job reads its eval sets from the package (the image has no tests/)."""
    assert sib.EVAL_CASES.read_bytes() == (FIXTURES / "song_id_eval.jsonl").read_bytes()
    assert sib.DUAL_EVAL_EXTRA_CASES.read_bytes() == (FIXTURES / "song_id_synthetic.jsonl").read_bytes()


# ---- bootstrap (--no-deploy): the first index, before the search service exists ------------


class NoService(FakeAdmin):
    def set_env(self, name: str, value: str) -> sib.Rollout:
        raise AssertionError("--no-deploy must not touch the service")

    def serving_env_values(self, name: str) -> set[str]:
        raise AssertionError("--no-deploy must not read the service")


def test_no_deploy_publishes_and_points_latest_without_a_rollout(store: FakeStore, tmp_path: Path) -> None:
    out = sib.build_and_publish(storage=store, run_admin=NoService(), workdir=tmp_path, deploy=False)
    assert out["published"] is True and out["deployed"] is False
    latest = json.loads(store.objects[(IDX, "song-index/latest.json")])
    assert f"{latest['run']}-{latest['token']}" == out["index_run"]
    assert (IDX, f"song-index/{out['index_run']}/manifest.json") in store.objects
    assert store.deletes == []  # no pruning without knowing what's live


def test_no_deploy_then_normal_run_is_skipped(store: FakeStore, tmp_path: Path) -> None:
    sib.build_and_publish(storage=store, run_admin=NoService(), workdir=tmp_path / "a", deploy=False)
    admin = FakeAdmin()
    assert sib.build_and_publish(storage=store, run_admin=admin, workdir=tmp_path / "b")["skipped"] is True
    assert admin.calls == []


def test_main_no_deploy_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_build(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"published": True}

    monkeypatch.setattr(sib, "build_and_publish", fake_build)
    monkeypatch.setattr(sib, "GcsStore", lambda *_a, **_k: FakeStore())
    monkeypatch.setattr(sib, "_storage_client", lambda: None)
    monkeypatch.setattr(sib.common, "configure_logging", lambda: None)
    assert sib.main(["run", "--no-deploy", "--workdir", str(tmp_path)]) == 0
    assert seen["deploy"] is False
    assert sib.main(["run", "--workdir", str(tmp_path)]) == 0
    assert seen["deploy"] is True
