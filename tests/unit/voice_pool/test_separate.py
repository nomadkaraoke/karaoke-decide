"""Voice-pool separation hand-off: gen's manifest, the job run, result collection, and the
Cloud Run job runner (fakes only: no GCP)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from google.cloud import run_v2

from karaoke_decide.voice_pool import queue, separate
from karaoke_decide.voice_pool.models import pool_id

FETCH = queue.FETCH_COLLECTION
GEN = separate.GEN_BUCKET
NOW = 1_800_000_000.0


def at(hours_ago: float) -> datetime:
    return datetime.fromtimestamp(NOW, UTC) - timedelta(hours=hours_ago)


async def fetched(fs, n: int, **doc: Any) -> list[dict[str, Any]]:
    items = [
        {"song_key": f"s|{i:03d}", "artist": "Invented", "title": f"T{i}", "source": "shared", "spotify_id": f"sp{i}"}
        for i in range(n)
    ]
    await queue.enqueue(fs, items)
    out = []
    for i in items:
        pid = pool_id(i["song_key"])
        fs.cols[FETCH][pid].update(
            {"status": "fetched", "audio_gcs": f"gs://{GEN}/voice-pool/audio/{pid}/original.flac", "fetched_at": at(1)}
        )
        fs.cols[FETCH][pid].update(doc)
        out.append({"id": pid, **fs.cols[FETCH][pid]})
    return out


async def test_manifest_lines_and_task_count(fs, gcs, runner):
    items = await fetched(fs, 41)
    items[3]["audio_gcs"] = None  # falls back to the canonical path
    uri = separate.write_manifest(gcs, "20261022-130000", items[:3])
    assert uri == f"gs://{GEN}/voice-pool/manifests/20261022-130000.jsonl"
    body = gcs.bucket(GEN).objects["voice-pool/manifests/20261022-130000.jsonl"].decode()
    lines = [json.loads(x) for x in body.splitlines()]
    assert lines == [
        {"id": i["id"], "audio": f"gs://{GEN}/voice-pool/audio/{i['id']}/original.flac"} for i in items[:3]
    ]
    assert separate.manifest_line(items[3]) == {
        "id": items[3]["id"],
        "audio": f"gs://{GEN}/voice-pool/audio/{items[3]['id']}/original.flac",
    }

    execution = await separate.submit_batch(fs, gcs, runner, items, "r1", now=lambda: NOW)
    assert execution.endswith("voice-pool-separate-job-1")
    (call,) = runner.calls
    assert call == {
        "job": "voice-pool-separate-job",
        "region": "us-east4",
        "args": [
            "python", "-m", "backend.workers.voice_pool_separate_worker", "--manifest",
            f"gs://{GEN}/voice-pool/manifests/r1.jsonl",
        ],
        "task_count": 3,  # ceil(41 / 20)
    }  # fmt: skip
    for i in items:
        doc = fs.cols[FETCH][i["id"]]
        assert doc["status"] == "separating" and doc["manifest_run"] == "r1"
        assert doc["separating_at"] == datetime.fromtimestamp(NOW, UTC)
    assert separate.task_count(1) == 1 and separate.task_count(20) == 1 and separate.task_count(21) == 2
    assert separate.task_count(80) == 4 and separate.task_count(200) == 4


async def test_submit_batch_caps_at_80(fs, gcs, runner):
    items = await fetched(fs, 95)
    await separate.submit_batch(fs, gcs, runner, items, "r1", now=lambda: NOW)
    assert runner.calls[0]["task_count"] == 4
    body = gcs.bucket(GEN).objects["voice-pool/manifests/r1.jsonl"].decode()
    assert len(body.splitlines()) == 80
    st = [fs.cols[FETCH][i["id"]]["status"] for i in items]
    assert st.count("separating") == 80 and st[80:] == ["fetched"] * 15  # leftovers wait for the next run


async def test_failed_job_start_leaves_items_fetched(fs, gcs):
    from tests.unit.voice_pool.conftest import FakeRunner

    items = await fetched(fs, 3)
    runner = FakeRunner(fail={separate.SEPARATE_JOB: RuntimeError("quota")})
    try:
        await separate.submit_batch(fs, gcs, runner, items, "r1", now=lambda: NOW)
    except RuntimeError:
        pass
    assert {fs.cols[FETCH][i["id"]]["status"] for i in items} == {"fetched"}


def _results(gcs, run: str, part: int, lines: list[Any]) -> None:
    body = "\n".join(x if isinstance(x, str) else json.dumps(x) for x in lines) + "\n"
    gcs.bucket(GEN).objects[f"voice-pool/manifests/{run}.results/part-{part}.jsonl"] = body.encode()


async def test_results_mark_separated_and_stale_run_requeues(fs, gcs, caplog):
    a, b, c, d = (i["id"] for i in await fetched(fs, 4, status="separating", manifest_run="r1", separating_at=at(1)))
    fs.cols[FETCH][c]["attempts"] = 2
    vocals = f"gs://{GEN}/voice-pool/stems/{a}/vocals.flac"
    _results(gcs, "r1", 0, [{"id": a, "ok": True, "vocals": vocals, "seconds": 41.5, "error": None}, "not json"])
    _results(gcs, "r1", 1, [
        {"id": b, "ok": False, "vocals": None, "seconds": 3.0, "error": "decode error"},
        {"id": c, "ok": False, "vocals": None, "seconds": 3.0, "error": "decode error"},
        {"id": "someone-else", "ok": True, "vocals": "gs://x", "seconds": 1.0, "error": None},
    ])  # fmt: skip
    assert await separate.collect_results(fs, gcs, "r1", now=lambda: NOW) == (1, 2)
    da, db, dc, dd = (fs.cols[FETCH][i] for i in (a, b, c, d))
    assert (da["status"], da["vocals_gcs"], da["separation_s"]) == ("separated", vocals, 41.5)
    assert (db["status"], db["attempts"], db["error"], db["manifest_run"]) == ("fetched", 1, "decode error", None)
    assert (dc["status"], dc["attempts"]) == ("failed", 3)
    assert dd["status"] == "separating"  # no result yet, 1 h old: still running
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)

    # 5 h later with no result: the execution timed out or died -> back to fetched (attempts unchanged).
    fs.cols[FETCH][d]["separating_at"] = at(5.1)
    assert await separate.collect_results(fs, gcs, "r1", now=lambda: NOW, requeue_stale=False) == (0, 0)
    assert await separate.collect_results(fs, gcs, "r1", now=lambda: NOW) == (0, 1)
    assert (dd["status"], dd["attempts"], dd["manifest_run"]) == ("fetched", 0, None)


def test_cloud_run_job_runner():
    client = MagicMock()
    client.run_job.return_value = SimpleNamespace(metadata=SimpleNamespace(name="projects/p/.../executions/e1"))
    runner = separate.CloudRunJobRunner("nomadkaraoke", client=client)
    assert runner.run("voice-pool-separate-job", "us-east4", args=["a", "b"], task_count=3) == (
        "projects/p/.../executions/e1"
    )
    req = client.run_job.call_args.kwargs["request"]
    assert isinstance(req, run_v2.RunJobRequest)
    assert req.name == "projects/nomadkaraoke/locations/us-east4/jobs/voice-pool-separate-job"
    assert list(req.overrides.container_overrides[0].args) == ["a", "b"] and req.overrides.task_count == 3

    runner.run("voice-pool-analyse", "us-central1", args=["analyse"])
    req = client.run_job.call_args.kwargs["request"]
    assert req.overrides.task_count == 0 and list(req.overrides.container_overrides[0].args) == ["analyse"]

    client.list_executions.return_value = [
        run_v2.Execution(name="e2", completion_time=datetime(2026, 10, 22, tzinfo=UTC)),
        run_v2.Execution(name="e1"),
    ]
    assert runner.is_running("voice-pool-analyse", "us-central1") is True
    lreq = client.list_executions.call_args.kwargs["request"]
    assert lreq.parent == "projects/nomadkaraoke/locations/us-central1/jobs/voice-pool-analyse"
    client.list_executions.return_value = [
        run_v2.Execution(name="e2", completion_time=datetime(2026, 10, 22, tzinfo=UTC))
    ]
    assert runner.is_running("voice-pool-analyse", "us-central1") is False
