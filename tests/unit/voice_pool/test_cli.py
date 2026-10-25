"""``python -m karaoke_decide.voice_pool``: argument handling and the dry-run report (no GCP, no network)."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from backend.config import get_backend_settings
from karaoke_decide.voice_pool import cli


@pytest.fixture
def no_bq(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("BIGQUERY_DISABLED", "1")
    # configure_logging(force=True) would replace pytest's capture handler.
    monkeypatch.setattr("karaoke_decide.etl.refresh_common.configure_logging", lambda: None)
    get_backend_settings.cache_clear()
    yield
    get_backend_settings.cache_clear()


def test_analyse_and_publish_are_wired():
    parser = cli.build_parser()
    args = parser.parse_args(["analyse", "--run", "r1", "--limit", "3"])
    assert (args.cmd, args.run, args.limit) == ("analyse", "r1", 3)
    assert parser.parse_args(["publish"]).cmd == "publish"
    assert not getattr(cli, "PENDING", {})


def test_no_probe_needs_dry_run(no_bq, capsys):
    assert cli.main(["select", "--n", "5", "--no-probe"]) == 2
    assert "--no-probe" in capsys.readouterr().err


def test_select_dry_run_prints_report(no_bq, catalog_db: Path, availability_db: Path, capsys):
    argv = ["select", "--n", "20", "--dry-run", "--no-probe", "--run", "t1"]
    assert cli.main([*argv, "--catalog", str(catalog_db), "--availability", str(availability_db)]) == 0
    out = capsys.readouterr().out
    assert "voice pool selection t1 (dry run)" in out
    assert "picked 20/20" in out and "enqueued 0" in out
    assert "gender" in out and "decade" in out


def test_select_needs_bigquery_to_write(no_bq, capsys):
    assert cli.main(["select", "--n", "5"]) == 2
    assert "only --dry-run" in capsys.readouterr().err


def test_select_failure_logs_one_error(no_bq, tmp_path: Path, caplog):
    missing = tmp_path / "nope.db"
    rc = cli.main(["select", "--dry-run", "--catalog", str(missing), "--availability", str(missing)])
    assert rc == 1
    assert [r.levelno for r in caplog.records if r.levelno >= logging.ERROR] == [logging.ERROR]


def test_catalog_and_availability_go_together(no_bq, catalog_db: Path):
    with pytest.raises(SystemExit):
        cli.main(["select", "--dry-run", "--catalog", str(catalog_db)])


def test_format_report_marks_below_minimum():
    summary = {
        "run": "r", "dry_run": False, "picked": 2, "target": 1000, "candidates": 5, "below_minimum": True,
        "probed": 3, "non_embeddable": 1, "enqueued": 2, "per_dimension": {"duet": {"solo": [900, 2], "duet": [100, 0]}},
        "empty_values": ["duet=duet"], "coverage_uri": "gs://b/voice/selection/r/coverage.json",
    }  # fmt: skip
    text = cli.format_report(summary)
    assert "BELOW MINIMUM" in text and "solo 2/900" in text and "empty: duet=duet" in text
    assert "gs://b/voice/selection/r/coverage.json" in text


def test_fetch_without_flacfetch_config_is_a_warning(no_bq, monkeypatch, caplog):
    monkeypatch.setenv("FLACFETCH_API_URL", "")
    monkeypatch.setenv("FLACFETCH_API_KEY", "")
    assert cli.main(["fetch"]) == 0
    assert any(r.levelno == logging.WARNING and "FLACFETCH_API_URL" in r.getMessage() for r in caplog.records)
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


def test_format_fetch_and_status():
    text = cli.format_fetch({
        "run": "r", "polled": 2, "fetched": 1, "resolved": 4, "unresolved": 1, "submitted": 3, "refused_code": "spotify_budget", "separating": 0,
        "separated": 4, "requeued": 0, "analyse_started": True,
    })  # fmt: skip
    assert "submitted 3" in text and "spotify_budget" in text and "analyse started" in text
    assert "resolved 4, unresolved (failed) 1" in text
    text = cli.format_status({"counts": {"queued": 5, "fetched": 1}, "budget": None})
    assert "queued 5" in text and "budget unavailable" in text
    text = cli.format_status({"counts": {"queued": 0}, "budget": {"batch_s": 3600.0, "batch_budget_s": 43200,
                              "total_s": 4000.0, "hard_cap_s": 86400, "batch_in_flight": 1,
                              "batch_allowed_now": False, "retry_after_s": 30}})  # fmt: skip
    assert "batch 3600/43200 s" in text and "retry in 30 s" in text


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch, fs, gcs):
    monkeypatch.setattr(cli, "_clients", lambda settings: (fs, gcs))
    return fs, gcs


async def _queue_one(fs) -> None:
    from karaoke_decide.voice_pool import queue

    await queue.enqueue(
        fs, [{"song_key": "muse|starlight", "artist": "Muse", "title": "Starlight", "source": "shared"}]
    )


def test_publish_builds_voice_db(no_bq, fakes, catalog_db: Path, availability_db: Path, capsys):
    import asyncio
    import json

    from karaoke_decide.voice_pool.voicedb import VOICE_BUCKET, VOICE_LATEST

    fs, gcs = fakes
    asyncio.run(_queue_one(fs))
    assert cli.main(["publish", "--catalog", str(catalog_db), "--availability", str(availability_db)]) == 0
    pointer = json.loads(gcs.bucket(VOICE_BUCKET).objects[VOICE_LATEST])
    assert (pointer["songs"], pointer["analysed"]) == (1, 0)
    assert "voice.db" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("task", "blocked", "all_failed", "publishes"),
    [("0", None, False, True), ("1", None, False, False), ("0", "no bq", False, False), ("0", None, True, False)],
)
def test_analyse_then_task0_publishes(
    monkeypatch, fakes, catalog_db: Path, availability_db: Path, capsys, task, blocked, all_failed, publishes
):
    import asyncio

    from karaoke_decide.voice_analysis import analyse
    from karaoke_decide.voice_pool.voicedb import VOICE_BUCKET, VOICE_LATEST

    monkeypatch.setenv("BIGQUERY_DISABLED", "0")
    monkeypatch.setattr("karaoke_decide.etl.refresh_common.configure_logging", lambda: None)
    monkeypatch.setenv("CLOUD_RUN_TASK_INDEX", task)
    monkeypatch.setenv("CLOUD_RUN_TASK_COUNT", "2")
    monkeypatch.setattr("google.cloud.bigquery.Client", lambda project=None: "bq")
    calls = []

    async def fake_run(fs, gcs, bq, *, run, limit, task_index, task_count):
        calls.append((bq, run, limit, task_index, task_count))
        return {"run": run, "task_index": task_index, "task_count": task_count, "listed": 1, "analysed": 1,
                "too_little_voice": 0, "failed": 0, "deferred": 0, "all_failed": all_failed,
                "blocked": blocked}  # fmt: skip

    monkeypatch.setattr(analyse, "run_analyse", fake_run)
    get_backend_settings.cache_clear()
    fs, gcs = fakes
    asyncio.run(_queue_one(fs))
    argv = [
        "analyse",
        "--run",
        "exec-9",
        "--limit",
        "4",
        "--catalog",
        str(catalog_db),
        "--availability",
        str(availability_db),
    ]
    try:
        assert cli.main(argv) == 0
    finally:
        get_backend_settings.cache_clear()
    assert calls == [("bq", "exec-9", 4, int(task), 2)]
    assert (VOICE_LATEST in gcs.bucket(VOICE_BUCKET).objects) is publishes
    out = capsys.readouterr().out
    assert "1 analysed" in out and ("blocked: no bq" in out) is bool(blocked)


def test_analyse_needs_bigquery(no_bq, capsys):
    assert cli.main(["analyse"]) == 2
    assert "BigQuery" in capsys.readouterr().err


def test_format_publish_empty():
    assert "nothing to publish" in cli.format_publish(None)


def test_fetch_runner_failure_logs_one_error_and_exits_1(no_bq, monkeypatch, caplog):
    from unittest.mock import MagicMock

    from karaoke_decide.voice_pool import fetch

    monkeypatch.setenv("FLACFETCH_API_URL", "https://flacfetch.invalid")
    monkeypatch.setenv("FLACFETCH_API_KEY", "k")
    monkeypatch.setattr("google.cloud.storage.Client", MagicMock())
    monkeypatch.setattr("backend.services.firestore_service.FirestoreService", MagicMock())

    async def boom(*a, **kw):
        raise fetch.JobRunnerFailedError("r7", [AttributeError("x")]) from AttributeError("x")

    monkeypatch.setattr(fetch, "run_fetch", boom)
    assert cli.main(["fetch"]) == 1
    (err,) = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert err.getMessage() == "voice pool fetch failed"  # stable; the run id is a json field
    assert err.json_fields["run"] == "r7" and err.json_fields["failed_calls"] == 1


def test_format_analyse_shows_a_systematic_stop():
    text = cli.format_analyse({
        "run": "r", "task_index": 0, "task_count": 1, "listed": 30, "analysed": 0, "too_little_voice": 0,
        "failed": 0, "deferred": 0, "blocked": None, "systematic": "ImportError",
    })  # fmt: skip
    assert "first songs all failed with ImportError" in text


def _old_catalog(catalog_db: Path, tmp_path: Path) -> Path:
    import shutil
    import sqlite3

    old = tmp_path / "old_catalog.db"
    shutil.copy(catalog_db, old)
    con = sqlite3.connect(old)
    for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='index' AND sql LIKE '%vr_%'").fetchall():
        con.execute(f'DROP INDEX "{name}"')
    for (c,) in [(r[1],) for r in con.execute("PRAGMA table_info(songs)") if r[1].startswith("vr_")]:
        con.execute(f"ALTER TABLE songs DROP COLUMN {c}")
    con.commit()
    con.close()
    return old


def test_select_old_catalog_works_and_require_flag_exits_2(
    no_bq, catalog_db: Path, availability_db: Path, tmp_path: Path, caplog, capsys
):
    old = _old_catalog(catalog_db, tmp_path)
    argv = [
        "select",
        "--n",
        "20",
        "--dry-run",
        "--no-probe",
        "--catalog",
        str(old),
        "--availability",
        str(availability_db),
    ]
    assert cli.main(argv) == 0
    assert "picked" in capsys.readouterr().out
    caplog.clear()
    assert cli.main([*argv, "--require-vocal-range"]) == 2
    assert "picked" not in capsys.readouterr().out
    assert sum("predates vocal-range" in r.getMessage() for r in caplog.records) == 1
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


def test_spotify_resolver_opens_catalog_lazily_and_resolves(reader, fake_bq):
    opened: list[object] = []

    def open_catalog(settings):
        opened.append(settings)
        return reader

    bq = fake_bq()
    resolver = cli.SpotifyResolver(object(), open_catalog=open_catalog, bq_client=bq)
    assert opened == []  # nothing loaded until a run needs it
    out = resolver(["adele|hello", "nobody|nothing"])
    assert set(out) == {"adele|hello"} and out["adele|hello"][0].startswith("sp")
    resolver(["adele|hello"])
    assert len(opened) == 1  # opened once per run


def test_spotify_resolver_catalog_failure_is_unavailable(fake_bq):
    from backend.services.catalog_store import CatalogLoadError
    from karaoke_decide.voice_pool.fetch import SpotifyResolveUnavailableError

    def broken(settings):
        raise CatalogLoadError("no published catalog.db")

    resolver = cli.SpotifyResolver(object(), open_catalog=broken, bq_client=fake_bq())
    with pytest.raises(SpotifyResolveUnavailableError, match="catalog.db"):
        resolver(["adele|hello"])
    resolver.close()  # nothing opened: a no-op


def test_load_catalog_only_reads_local_catalog(no_bq, catalog_db: Path, monkeypatch):
    from backend.services.catalog_store import CatalogLoadError, load_catalog_only

    monkeypatch.setenv("CATALOG_LOCAL_DIR", str(catalog_db.parent))
    get_backend_settings.cache_clear()
    reader = load_catalog_only(get_backend_settings())
    try:
        assert not reader.has_availability and reader.all_kn_ids_for_song_keys(["adele|hello"])
    finally:
        reader.close()
    monkeypatch.setenv("CATALOG_LOCAL_DIR", str(catalog_db.parent / "missing"))
    get_backend_settings.cache_clear()
    with pytest.raises(CatalogLoadError):
        load_catalog_only(get_backend_settings())
