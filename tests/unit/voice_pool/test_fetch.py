"""Voice-pool fetch scheduler: flacfetch client, the budget-aware submission loop, download
polling and the separation / analyse hand-offs (fakes only: no flacfetch, no GCP)."""

from __future__ import annotations

import json
import logging
import random
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from google.api_core.exceptions import NotFound

from karaoke_decide.voice_pool import fetch, queue, separate
from karaoke_decide.voice_pool.flacfetch_client import FlacfetchClient, FlacfetchUnavailableError, Refused
from karaoke_decide.voice_pool.models import pool_id

FETCH = queue.FETCH_COLLECTION
GEN = separate.GEN_BUCKET
ALLOWED = {
    "window_s": 86400, "total_s": 100.0, "batch_s": 50.0, "batch_budget_s": 43200, "hard_cap_s": 86400,
    "batch_in_flight": 0, "interactive_in_flight": 0, "batch_allowed_now": True, "retry_after_s": 0,
}  # fmt: skip


class FakeFlacfetch:
    """flacfetch v0.35 over httpx.MockTransport.

    ``downloads`` / ``budgets``: scripted ``(status_code, body)`` responses, consumed in order;
    once empty, a download is accepted and the budget allows. ``statuses``: download id ->
    ``(status_code, body)``.
    """

    def __init__(self) -> None:
        self.downloads: list[tuple[int, Any]] = []
        self.budgets: list[tuple[int, Any]] = []
        self.statuses: dict[str, tuple[int, Any]] = {}
        self.started: list[dict[str, Any]] = []
        self.requests: list[httpx.Request] = []
        self.down = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("flacup unreachable", request=request)
        path = request.url.path
        if path == "/spotify/budget":
            code, body = self.budgets.pop(0) if self.budgets else (200, ALLOWED)
            return httpx.Response(code, json=body)
        if path == "/download-by-id":
            code, body = self.downloads.pop(0) if self.downloads else (200, None)
            if code != 200:
                headers = {"Retry-After": str(body["detail"]["retry_after_s"])} if code == 429 else {}
                return httpx.Response(code, json=body, headers=headers)
            self.started.append(json.loads(request.content))
            return httpx.Response(200, json={"download_id": f"dl{len(self.started)}", "status": "queued"})
        if path.startswith("/download/") and path.endswith("/status"):
            code, body = self.statuses.get(path.split("/")[2], (404, {"detail": "Download not found"}))
            return httpx.Response(code, json=body)
        return httpx.Response(404)  # pragma: no cover

    def client(self) -> FlacfetchClient:
        return FlacfetchClient("http://flacup.test/", "k3y", httpx.Client(transport=httpx.MockTransport(self.handler)))


@pytest.fixture
def ff() -> FakeFlacfetch:
    return FakeFlacfetch()


def refused(code: str, retry_after_s: int) -> tuple[int, Any]:
    return 429, {"detail": {"code": code, "retry_after_s": retry_after_s}}


async def seed(fs, n: int, *, prefix: str = "song", **doc: Any) -> list[str]:
    """``n`` queued songs (added in order), then ``doc`` fields applied; returns their pool ids."""
    items = [
        {"song_key": f"{prefix}|{i:03d}", "artist": "Invented Artist", "title": f"Invented {i}", "source": "shared",
         "spotify_id": f"sp{i:06d}", "duration_ms": 200_400}
        for i in range(n)
    ]  # fmt: skip
    await queue.enqueue(fs, items)
    ids = [pool_id(i["song_key"]) for i in items]
    base = datetime(2026, 10, 22, tzinfo=UTC)
    for k, i in enumerate(ids):
        fs.cols[FETCH][i]["added_at"] = base + timedelta(seconds=k)
        fs.cols[FETCH][i].update(doc)
    return ids


def ts(clock, hours_ago: float) -> datetime:
    return datetime.fromtimestamp(clock.now(), UTC) - timedelta(hours=hours_ago)


async def go(fs, ff, runner, gcs, clock, **kw: Any) -> dict[str, Any]:
    kw.setdefault("deadline_s", 3300)
    return await fetch.run_fetch(
        fs,
        ff.client(),
        runner,
        gcs,
        run="20261022-130000",
        rng=random.Random(7),
        sleep=clock.sleep,
        now=clock.now,
        **kw,
    )


def statuses(fs) -> dict[str, str]:
    return {k: d["status"] for k, d in fs.cols[FETCH].items()}


# ------------------------------------------------------------------ flacfetch client


def test_start_batch_request():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"download_id": "abc", "status": "queued"})

    client = FlacfetchClient("http://ff.test/", "k3y", httpx.Client(transport=httpx.MockTransport(handler)))
    assert client.start_batch("sp1", 201, "voice-pool/audio/p1/", "original") == "abc"
    (req,) = seen
    assert str(req.url) == "http://ff.test/download-by-id" and req.headers["X-API-Key"] == "k3y"
    assert json.loads(req.content) == {
        "source_name": "Spotify", "source_id": "sp1", "download_url": "https://open.spotify.com/track/sp1",
        "upload_to_gcs": True, "gcs_path": "voice-pool/audio/p1/", "output_filename": "original",
        "priority": "batch", "expected_duration_s": 201, "delete_local_after_upload": True,
    }  # fmt: skip


def test_client_refusals_and_outages(ff):
    client = ff.client()
    ff.downloads = [refused("batch_gap", 12), (503, {"detail": {"code": "budget_unavailable", "retry_after_s": 60}})]
    assert client.start_batch("sp1", 200, "d/", "original") == Refused("batch_gap", 12)
    with pytest.raises(FlacfetchUnavailableError) as e:
        client.start_batch("sp1", 200, "d/", "original")
    assert e.value.code == "budget_unavailable"
    ff.budgets = [(503, {**ALLOWED, "batch_allowed_now": False, "retry_after_s": 60})]
    with pytest.raises(FlacfetchUnavailableError):
        client.budget()
    assert client.budget()["batch_allowed_now"] is True
    assert client.status("missing") is None  # flacfetch forgot it (restart)
    ff.statuses["d1"] = (200, {"download_id": "d1", "status": "complete", "gcs_path": "gs://b/x.flac"})
    assert client.status("d1")["status"] == "complete"
    ff.statuses["d2"] = (502, {})
    with pytest.raises(FlacfetchUnavailableError):
        client.status("d2")
    ff.down = True
    with pytest.raises(FlacfetchUnavailableError):
        client.budget()
    ff.down = False
    ff.downloads = [(401, {"detail": "Invalid API key"})]
    with pytest.raises(httpx.HTTPStatusError):  # misconfiguration is a real failure
        client.start_batch("sp1", 200, "d/", "original")


# ------------------------------------------------------------------ submission loop


async def test_submits_until_refused(fs, ff, runner, gcs, clock, caplog):
    caplog.set_level(logging.INFO)
    ids = await seed(fs, 5)
    ff.downloads = [(200, None), (200, None), refused("spotify_budget", 7200)]
    out = await go(fs, ff, runner, gcs, clock)
    assert out["submitted"] == 2 and out["refused_code"] == "spotify_budget"
    st = statuses(fs)
    assert [st[i] for i in ids] == ["downloading", "downloading", "queued", "queued", "queued"]
    assert fs.cols[FETCH][ids[0]]["flacfetch_id"] == "dl1" and fs.cols[FETCH][ids[1]]["flacfetch_id"] == "dl2"
    first = ff.started[0]
    assert first["gcs_path"] == f"voice-pool/audio/{ids[0]}/" and first["output_filename"] == "original"
    assert first["expected_duration_s"] == 200 and first["priority"] == "batch" and first["source_id"] == "sp000000"
    assert "spotify_budget" in caplog.text
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)


async def test_spacing_uses_rng_between_submissions(fs, ff, runner, gcs, clock):
    await seed(fs, 6)
    out = await go(fs, ff, runner, gcs, clock)
    assert out["submitted"] == 6 and out["refused_code"] is None
    assert len(clock.sleeps) == 5 and all(45 <= s <= 180 for s in clock.sleeps)
    expected = random.Random(7)
    assert clock.sleeps == [expected.uniform(45, 180) for _ in range(5)]


async def test_stops_at_deadline(fs, ff, runner, gcs, clock):
    await seed(fs, 50)
    out = await go(fs, ff, runner, gcs, clock, deadline_s=600)
    assert 3 <= out["submitted"] < 50
    assert sum(clock.sleeps[: out["submitted"] - 1]) < 600
    assert list(statuses(fs).values()).count("downloading") == out["submitted"]


async def test_transient_refusal_waits_and_retries(fs, ff, runner, gcs, clock):
    ids = await seed(fs, 2)
    ff.downloads = [refused("batch_busy", 90)]
    ff.budgets = [(200, ALLOWED), (200, {**ALLOWED, "batch_allowed_now": False, "retry_after_s": 40})]
    out = await go(fs, ff, runner, gcs, clock)
    assert out["submitted"] == 2 and out["refused_code"] is None
    assert clock.sleeps[0] == 90  # Retry-After of the 429, then the same song again
    assert clock.sleeps[1] == 40  # budget closed: wait its retry_after_s
    assert len(clock.sleeps) == 3 and 45 <= clock.sleeps[2] <= 180  # then the usual spacing
    assert [ff.started[0]["source_id"], ff.started[1]["source_id"]] == ["sp000000", "sp000001"]
    assert all(statuses(fs)[i] == "downloading" for i in ids)


async def test_closed_budget_past_deadline_stops(fs, ff, runner, gcs, clock):
    await seed(fs, 3)
    ff.budgets = [(200, {**ALLOWED, "batch_allowed_now": False, "retry_after_s": 5000})]
    out = await go(fs, ff, runner, gcs, clock)
    assert out["submitted"] == 0 and out["refused_code"] == "budget_closed"
    assert not ff.started and not clock.sleeps


async def test_unfetchable_items_fail_without_submitting(fs, ff, runner, gcs, clock):
    (no_id,) = await seed(fs, 1, prefix="a", spotify_id=None)
    (long_,) = await seed(fs, 1, prefix="b", duration_ms=1_300_000)
    (ok,) = await seed(fs, 1, prefix="c")
    out = await go(fs, ff, runner, gcs, clock)
    assert out["submitted"] == 1 and [s["source_id"] for s in ff.started] == ["sp000000"]
    st = statuses(fs)
    assert st[no_id] == "failed" and st[long_] == "failed" and st[ok] == "downloading"
    assert "spotify" in fs.cols[FETCH][no_id]["error"] and "duration" in fs.cols[FETCH][long_]["error"]


async def test_budget_endpoint_unreachable_is_warning_not_error(fs, ff, runner, gcs, clock, caplog):
    await seed(fs, 3)
    ff.down = True
    out = await go(fs, ff, runner, gcs, clock)
    assert out["submitted"] == 0 and out["refused_code"] == "unavailable"
    assert any(r.levelno == logging.WARNING and "flacfetch" in r.getMessage() for r in caplog.records)
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


async def test_budget_unavailable_503_is_warning(fs, ff, runner, gcs, clock, caplog):
    await seed(fs, 3)
    ff.downloads = [(503, {"detail": {"code": "budget_unavailable", "retry_after_s": 60}})]
    out = await go(fs, ff, runner, gcs, clock)
    assert out["submitted"] == 0 and out["refused_code"] == "budget_unavailable"
    assert set(statuses(fs).values()) == {"queued"}
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


# ------------------------------------------------------------------ polling


async def test_complete_download_marks_fetched(fs, ff, runner, gcs, clock):
    done, busy = await seed(fs, 2, status="downloading")
    fs.cols[FETCH][done]["flacfetch_id"] = "d1"
    fs.cols[FETCH][busy]["flacfetch_id"] = "d2"
    uri = f"gs://{GEN}/voice-pool/audio/{done}/original.flac"
    ff.statuses = {
        "d1": (200, {"download_id": "d1", "status": "complete", "gcs_path": uri}),
        "d2": (200, {"download_id": "d2", "status": "uploading", "gcs_path": None}),
    }
    out = await go(fs, ff, runner, gcs, clock)
    assert out["polled"] == 2 and out["fetched"] == 1
    doc = fs.cols[FETCH][done]
    assert doc["status"] == "fetched" and doc["audio_gcs"] == uri
    assert doc["fetched_at"] == datetime.fromtimestamp(clock.now(), UTC)
    assert fs.cols[FETCH][busy]["status"] == "downloading"


async def test_failed_three_times_marks_failed(fs, ff, runner, gcs, clock):
    first, last, lost = await seed(fs, 3, status="downloading")
    fs.cols[FETCH][first].update(flacfetch_id="d1", attempts=0)
    fs.cols[FETCH][last].update(flacfetch_id="d2", attempts=2)
    fs.cols[FETCH][lost].update(flacfetch_id="gone", attempts=0)
    failed = {"status": "failed", "error": "capture failed", "gcs_path": None}
    ff.statuses = {"d1": (200, {"download_id": "d1", **failed}), "d2": (200, {"download_id": "d2", **failed})}
    ff.budgets = [(200, {**ALLOWED, "batch_allowed_now": False, "retry_after_s": 9999})]  # no new submissions
    out = await go(fs, ff, runner, gcs, clock)
    assert out["polled"] == 3 and out["fetched"] == 0
    a, b, c = (fs.cols[FETCH][i] for i in (first, last, lost))
    assert (a["status"], a["attempts"], a["flacfetch_id"], a["error"]) == ("queued", 1, None, "capture failed")
    assert (b["status"], b["attempts"]) == ("failed", 3)
    assert (c["status"], c["attempts"]) == ("queued", 1) and "not found" in c["error"]


async def test_complete_without_gcs_path_counts_as_failure(fs, ff, runner, gcs, clock):
    (i,) = await seed(fs, 1, status="downloading", flacfetch_id="d1")
    ff.statuses = {"d1": (200, {"download_id": "d1", "status": "complete", "gcs_path": None})}
    ff.budgets = [(200, {**ALLOWED, "batch_allowed_now": False, "retry_after_s": 9999})]
    await go(fs, ff, runner, gcs, clock)
    assert fs.cols[FETCH][i]["status"] == "queued" and fs.cols[FETCH][i]["attempts"] == 1


async def test_status_unreachable_leaves_downloading(fs, ff, runner, gcs, clock, caplog):
    (i,) = await seed(fs, 1, status="downloading", flacfetch_id="d1")
    ff.statuses = {"d1": (500, {})}
    out = await go(fs, ff, runner, gcs, clock)
    assert fs.cols[FETCH][i]["status"] == "downloading" and out["fetched"] == 0
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


# ------------------------------------------------------------------ separation + analyse hand-off


async def test_separation_submitted_at_25_or_6h(fs, ff, runner, gcs, clock):
    ff.budgets = [(200, {**ALLOWED, "batch_allowed_now": False, "retry_after_s": 9999})] * 3
    ids = await seed(fs, 24, status="fetched", fetched_at=ts(clock, 1))
    out = await go(fs, ff, runner, gcs, clock)
    assert out["separating"] == 0 and not runner.calls  # 24 fresh: wait

    fs.cols[FETCH][ids[5]]["fetched_at"] = ts(clock, 6.5)  # oldest > 6 h: go
    out = await go(fs, ff, runner, gcs, clock)
    assert out["separating"] == 24
    (call,) = runner.calls
    assert call["job"] == separate.SEPARATE_JOB and call["region"] == "us-east4" and call["task_count"] == 2
    assert {statuses(fs)[i] for i in ids} == {"separating"}

    more = await seed(fs, 25, prefix="more", status="fetched", fetched_at=ts(clock, 0.5))
    out = await go(fs, ff, runner, gcs, clock)
    assert out["separating"] == 25 and runner.calls[-1]["task_count"] == 2
    assert {statuses(fs)[i] for i in more} == {"separating"}


async def test_separation_waits_while_separate_job_running(fs, ff, runner, gcs, clock):
    ff.budgets = [(200, {**ALLOWED, "batch_allowed_now": False, "retry_after_s": 9999})]
    await seed(fs, 30, status="fetched", fetched_at=ts(clock, 1))
    runner.running.add(separate.SEPARATE_JOB)
    out = await go(fs, ff, runner, gcs, clock)
    assert out["separating"] == 0 and not runner.calls


async def test_results_collected_during_run(fs, ff, runner, gcs, clock):
    ff.budgets = [(200, {**ALLOWED, "batch_allowed_now": False, "retry_after_s": 9999})]
    a, b = await seed(fs, 2, status="separating", manifest_run="r0", separating_at=ts(clock, 1))
    line = {
        "id": a,
        "ok": True,
        "vocals": f"gs://{GEN}/voice-pool/stems/{a}/vocals.flac",
        "seconds": 40.0,
        "error": None,
    }
    gcs.bucket(GEN).objects["voice-pool/manifests/r0.results/part-0.jsonl"] = (json.dumps(line) + "\n").encode()
    runner.running.add(separate.SEPARATE_JOB)
    out = await go(fs, ff, runner, gcs, clock)
    assert out["separated"] == 1 and statuses(fs)[a] == "separated" and statuses(fs)[b] == "separating"
    assert out["analyse_started"] is True
    assert runner.calls == [
        {"job": "voice-pool-analyse", "region": "us-central1", "args": ["analyse"], "task_count": None}
    ]


async def test_analyse_started_once_when_separated(fs, ff, runner, gcs, clock):
    ff.budgets = [(200, {**ALLOWED, "batch_allowed_now": False, "retry_after_s": 9999})] * 3
    out = await go(fs, ff, runner, gcs, clock)
    assert out["analyse_started"] is False and not runner.calls  # nothing separated

    await seed(fs, 2, status="separated")
    out = await go(fs, ff, runner, gcs, clock)
    assert out["analyse_started"] is True and len(runner.calls) == 1

    runner.running.add("voice-pool-analyse")
    out = await go(fs, ff, runner, gcs, clock)
    assert out["analyse_started"] is False and len(runner.calls) == 1


async def test_missing_analyse_job_is_warning(fs, ff, gcs, clock, caplog):
    from tests.unit.voice_pool.conftest import FakeRunner

    ff.budgets = [(200, {**ALLOWED, "batch_allowed_now": False, "retry_after_s": 9999})]
    await seed(fs, 1, status="separated")
    runner = FakeRunner(fail={"voice-pool-analyse": NotFound("job voice-pool-analyse")})
    out = await go(fs, ff, runner, gcs, clock)
    assert out["analyse_started"] is False
    assert any(r.levelno == logging.WARNING and "voice-pool-analyse" in r.getMessage() for r in caplog.records)
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


async def test_status_summary(fs, ff, clock):
    await seed(fs, 2)
    await seed(fs, 1, prefix="x", status="fetched")
    out = await fetch.status_summary(fs, ff.client())
    assert out["counts"]["queued"] == 2 and out["counts"]["fetched"] == 1 and out["counts"]["failed"] == 0
    assert out["budget"]["batch_allowed_now"] is True
    ff.down = True
    assert (await fetch.status_summary(fs, ff.client()))["budget"] is None
    assert (await fetch.status_summary(fs, None))["budget"] is None
