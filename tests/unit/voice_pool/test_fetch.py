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


# ------------------------------------------------------------------ review fixes


async def test_manifest_write_forbidden_is_warning_and_analyse_still_runs(fs, ff, runner, clock, caplog):
    from google.api_core.exceptions import Forbidden

    from tests.unit.voice_pool.conftest import FakeGcs

    class ForbiddenGcs(FakeGcs):
        def bucket(self, name: str) -> Any:
            b = super().bucket(name)
            b.blob = lambda n: (_ for _ in ()).throw(Forbidden(f"403 storage.objects.create on {n}"))
            return b

    ff.budgets = [(200, {**ALLOWED, "batch_allowed_now": False, "retry_after_s": 9999})]
    ids = await seed(fs, 25, status="fetched", fetched_at=ts(clock, 1))
    await seed(fs, 1, prefix="done", status="separated")
    out = await go(fs, ff, runner, ForbiddenGcs(), clock)
    assert out["separating"] == 0 and {statuses(fs)[i] for i in ids} == {"fetched"}
    assert out["analyse_started"] is True and [c["job"] for c in runner.calls] == ["voice-pool-analyse"]
    assert any(r.levelno == logging.WARNING and "403" in r.getMessage() for r in caplog.records)
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


async def test_unreadable_results_are_a_warning(fs, ff, runner, clock, caplog):
    from google.api_core.exceptions import Forbidden

    from tests.unit.voice_pool.conftest import FakeGcs

    class ForbiddenList(FakeGcs):
        def bucket(self, name: str) -> Any:
            b = super().bucket(name)
            b.list_blobs = lambda **kw: (_ for _ in ()).throw(Forbidden("403 storage.objects.list"))
            return b

    ff.budgets = [(200, {**ALLOWED, "batch_allowed_now": False, "retry_after_s": 9999})]
    (i,) = await seed(fs, 1, status="separating", manifest_run="r0", separating_at=ts(clock, 1))
    out = await go(fs, ff, runner, ForbiddenList(), clock)
    assert out["separated"] == 0 and statuses(fs)[i] == "separating"
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


async def test_rejected_song_fails_alone_and_queue_moves_on(fs, ff, runner, gcs, clock):
    bad, good = await seed(fs, 2)
    ff.downloads = [(422, {"detail": [{"msg": "expected_duration_s too large"}]})]
    out = await go(fs, ff, runner, gcs, clock)
    assert out["submitted"] == 1 and out["refused_code"] is None
    assert (fs.cols[FETCH][bad]["status"], fs.cols[FETCH][bad]["attempts"]) == ("queued", 1)
    assert "422" in fs.cols[FETCH][bad]["error"] and fs.cols[FETCH][good]["status"] == "downloading"
    assert [s["source_id"] for s in ff.started] == ["sp000001"]


async def test_auth_failure_aborts_and_unclaims(fs, ff, runner, gcs, clock):
    (i,) = await seed(fs, 1)
    ff.downloads = [(401, {"detail": "Invalid API key"})]
    with pytest.raises(httpx.HTTPStatusError):
        await go(fs, ff, runner, gcs, clock)
    assert statuses(fs)[i] == "queued" and fs.cols[FETCH][i]["attempts"] == 0


async def test_claimed_song_is_skipped(fs, ff, runner, gcs, clock, monkeypatch):
    taken, free = await seed(fs, 2)
    real = fs.transform_document_atomically

    async def racing(collection: str, doc_id: str, transform: Any) -> Any:
        if doc_id == taken:  # another execution claimed it after our query
            fs.cols[FETCH][taken].update(status="downloading", flacfetch_id="other")
        return await real(collection, doc_id, transform)

    monkeypatch.setattr(fs, "transform_document_atomically", racing)
    out = await go(fs, ff, runner, gcs, clock)
    assert out["submitted"] == 1 and [s["source_id"] for s in ff.started] == ["sp000001"]
    assert fs.cols[FETCH][taken]["flacfetch_id"] == "other"
    assert fs.cols[FETCH][free]["flacfetch_id"] == "dl1" and statuses(fs)[free] == "downloading"


async def test_refusal_and_outage_revert_the_claim(fs, ff, runner, gcs, clock):
    (i,) = await seed(fs, 1)
    ff.downloads = [refused("spotify_budget", 7200)]
    await go(fs, ff, runner, gcs, clock)
    assert statuses(fs)[i] == "queued" and fs.cols[FETCH][i]["flacfetch_id"] is None
    ff.downloads = [(503, {"detail": {"code": "budget_unavailable", "retry_after_s": 60}})]
    await go(fs, ff, runner, gcs, clock)
    assert statuses(fs)[i] == "queued" and fs.cols[FETCH][i]["attempts"] == 0


async def test_short_retry_waits_at_least_the_batch_gap(fs, ff, runner, gcs, clock):
    await seed(fs, 1)
    ff.downloads = [refused("batch_gap", 3)]
    out = await go(fs, ff, runner, gcs, clock)
    assert out["submitted"] == 1 and clock.sleeps == [fetch.MIN_WAIT_S] and fetch.MIN_WAIT_S == 30


async def test_forgotten_download_with_audio_in_gcs_is_fetched(fs, ff, runner, gcs, clock):
    uploaded, lost = await seed(fs, 2, status="downloading", flacfetch_id="gone")
    gcs.bucket(GEN).objects[f"voice-pool/audio/{uploaded}/original.flac"] = b"fLaC"
    ff.budgets = [(200, {**ALLOWED, "batch_allowed_now": False, "retry_after_s": 9999})]
    out = await go(fs, ff, runner, gcs, clock)
    assert out["fetched"] == 1
    assert fs.cols[FETCH][uploaded]["status"] == "fetched"
    assert fs.cols[FETCH][uploaded]["audio_gcs"] == f"gs://{GEN}/voice-pool/audio/{uploaded}/original.flac"
    assert (fs.cols[FETCH][lost]["status"], fs.cols[FETCH][lost]["attempts"]) == ("queued", 1)


async def test_fresh_claim_is_left_alone_but_orphaned_claim_is_recovered(fs, ff, runner, gcs, clock):
    fresh, orphan = await seed(fs, 2, status="downloading", flacfetch_id=None)
    fs.cols[FETCH][fresh]["updated_at"] = ts(clock, 0.05)  # 3 min: another run is starting it
    fs.cols[FETCH][orphan]["updated_at"] = ts(clock, 1)  # crashed between claim and start
    ff.budgets = [(200, {**ALLOWED, "batch_allowed_now": False, "retry_after_s": 9999})]
    out = await go(fs, ff, runner, gcs, clock)
    assert out["polled"] == 1 and statuses(fs)[fresh] == "downloading"
    assert (fs.cols[FETCH][orphan]["status"], fs.cols[FETCH][orphan]["attempts"]) == ("queued", 1)


async def test_unexpected_runner_failure_lets_the_run_finish_then_raises_once(fs, ff, gcs, clock, caplog):
    """A bug in the runner (e.g. AttributeError from a wrong client method) must not cost the
    run's downloads or its other hand-offs: each failing call is a WARNING, the run finishes,
    then raises ONE JobRunnerFailedError (the CLI's one ERROR)."""

    class BrokenRunner:
        calls: list[str] = []

        def run(self, job: str, region: str, args: list[str] | None = None, task_count: int | None = None) -> str:
            self.calls.append(job)
            return f"projects/p/locations/{region}/jobs/{job}/executions/e1"

        def is_running(self, job: str, region: str) -> bool:
            if job == separate.SEPARATE_JOB:
                raise AttributeError("'JobsClient' object has no attribute 'list_executions'")
            return False

    queued = await seed(fs, 1)
    (sep,) = await seed(fs, 1, prefix="sep", status="separating", manifest_run="r0", separating_at=ts(clock, 1))
    fetched = await seed(fs, 25, prefix="f", status="fetched", fetched_at=ts(clock, 1))
    await seed(fs, 1, prefix="done", status="separated")
    runner = BrokenRunner()
    with pytest.raises(fetch.JobRunnerFailedError) as exc:
        await go(fs, ff, runner, gcs, clock)
    assert isinstance(exc.value.__cause__, AttributeError)
    assert str(exc.value) == fetch.JobRunnerFailedError.MESSAGE  # stable text; details in log_extra
    fields = exc.value.log_extra["json_fields"]
    assert fields["run"] == "20261022-130000" and fields["failed_calls"] == 2  # collect + separate checks
    assert "list_executions" in fields["first_error"]
    assert statuses(fs)[queued[0]] == "downloading"  # the download step still ran
    assert statuses(fs)[sep] == "separating"  # unknown running state: never requeued as stale
    assert {statuses(fs)[i] for i in fetched} == {"fetched"}  # no second separation submitted
    assert runner.calls == ["voice-pool-analyse"]  # analyse still started
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)  # the CLI logs the one ERROR


async def test_unexpected_submit_failure_keeps_songs_fetched(fs, ff, runner, gcs, clock):
    from tests.unit.voice_pool.conftest import FakeRunner

    ff.budgets = [(200, {**ALLOWED, "batch_allowed_now": False, "retry_after_s": 9999})]
    ids = await seed(fs, 25, status="fetched", fetched_at=ts(clock, 1))
    broken = FakeRunner(fail={separate.SEPARATE_JOB: AttributeError("no run_job")})
    broken.is_running = lambda job, region: False  # type: ignore[method-assign]
    with pytest.raises(fetch.JobRunnerFailedError):
        await go(fs, ff, broken, gcs, clock)
    assert {statuses(fs)[i] for i in ids} == {"fetched"}


async def test_runner_helpers_still_raise_without_an_error_list(fs, gcs, clock):
    from tests.unit.voice_pool.conftest import FakeRunner

    await seed(fs, 1, status="separated")
    with pytest.raises(AttributeError):
        await fetch.maybe_analyse(fs, FakeRunner(fail={"voice-pool-analyse": AttributeError("x")}))


# ------------------------------------------------------------------ final-review fixes


def _failed_downloads(ff, ids, error="spotify capture: account region blocked"):
    for k, pid in enumerate(ids):
        ff.statuses[f"dl{k}"] = (200, {"download_id": f"dl{k}", "status": "failed", "error": error})


async def test_systematic_download_failures_are_one_error_and_stop_submissions(fs, ff, runner, gcs, clock, caplog):
    ids = await seed(fs, 5, prefix="dl", status="downloading")
    for k, pid in enumerate(ids):
        fs.cols[FETCH][pid]["flacfetch_id"] = f"dl{k}"
    _failed_downloads(ff, ids[:4])
    ff.statuses["dl4"] = (200, {"download_id": "dl4", "status": "cancelled", "error": None})
    queued = await seed(fs, 3, prefix="q")
    out = await go(fs, ff, runner, gcs, clock)
    assert (out["submitted"], out["refused_code"]) == (0, "downloads_failing")
    assert ff.started == [] and {statuses(fs)[q] for q in queued} == {"queued"}  # no budget spent
    assert {statuses(fs)[i] for i in ids} == {"queued"} and all(fs.cols[FETCH][i]["attempts"] == 1 for i in ids)
    (err,) = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert err.getMessage() == "voice pool fetch: downloads are failing systematically; no new downloads this run"
    fields = err.json_fields
    assert fields["top_error"] == "spotify capture: account region blocked" and fields["top_error_count"] == 4
    assert fields["failures"] == 5 and fields["run"] == "20261022-130000"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "download of" in r.getMessage()]
    assert len(warnings) == 5  # each failure on its own


@pytest.mark.parametrize(("failures", "successes"), [(4, 0), (5, 1)])
async def test_few_failures_or_any_success_keep_submitting(fs, ff, runner, gcs, clock, caplog, failures, successes):
    ids = await seed(fs, failures + successes, prefix="dl", status="downloading")
    for k, pid in enumerate(ids):
        fs.cols[FETCH][pid]["flacfetch_id"] = f"dl{k}"
    _failed_downloads(ff, ids[:failures])
    for k in range(failures, failures + successes):
        ff.statuses[f"dl{k}"] = (200, {"download_id": f"dl{k}", "status": "complete", "gcs_path": "gs://b/a.flac"})
    ff.budgets = [(200, ALLOWED), (200, {**ALLOWED, "batch_allowed_now": False, "retry_after_s": 9999})]
    await seed(fs, 2, prefix="q")
    out = await go(fs, ff, runner, gcs, clock)
    assert out["submitted"] >= 1 and out["refused_code"] != "downloads_failing"
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_hung_download_counts_a_failed_attempt_after_2h(fs, ff, runner, gcs, clock):
    ff.budgets = [(200, {**ALLOWED, "batch_allowed_now": False, "retry_after_s": 9999})]
    hung, fresh, legacy = await seed(fs, 3, prefix="h", status="downloading")
    for k, (pid, hours) in enumerate(((hung, 2.5), (fresh, 1.0))):
        fs.cols[FETCH][pid].update(flacfetch_id=f"dl{k}", download_started_at=ts(clock, hours))
        ff.statuses[f"dl{k}"] = (200, {"download_id": f"dl{k}", "status": "downloading"})
    # submitted before download_started_at existed: falls back to updated_at
    fs.cols[FETCH][legacy].update(flacfetch_id="dl2", updated_at=ts(clock, 3))
    ff.statuses["dl2"] = (200, {"download_id": "dl2", "status": "queued"})
    await go(fs, ff, runner, gcs, clock)
    for pid in (hung, legacy):
        doc = fs.cols[FETCH][pid]
        assert (doc["status"], doc["attempts"], doc["flacfetch_id"]) == ("queued", 1, None)
        assert "after 2 h" in doc["error"]
    assert statuses(fs)[fresh] == "downloading" and fs.cols[FETCH][fresh]["attempts"] == 0


async def test_submission_records_download_start(fs, ff, runner, gcs, clock):
    ff.budgets = [(200, ALLOWED), (200, {**ALLOWED, "batch_allowed_now": False, "retry_after_s": 9999})]
    (pid,) = await seed(fs, 1)
    await go(fs, ff, runner, gcs, clock)
    assert fs.cols[FETCH][pid]["download_started_at"] == datetime.fromtimestamp(clock.now(), UTC)


@pytest.mark.parametrize(
    "exc_name", ["ServiceUnavailable", "DeadlineExceeded", "InternalServerError", "TooManyRequests"]
)
async def test_transient_cloud_run_errors_are_warnings(fs, ff, gcs, clock, caplog, exc_name):
    import google.api_core.exceptions as gexc

    from tests.unit.voice_pool.conftest import FakeRunner

    ff.budgets = [(200, {**ALLOWED, "batch_allowed_now": False, "retry_after_s": 9999})]
    await seed(fs, 1, status="separated")
    runner = FakeRunner(fail={"voice-pool-analyse": getattr(gexc, exc_name)("blip")})
    out = await go(fs, ff, runner, gcs, clock)  # no JobRunnerFailedError
    assert out["analyse_started"] is False
    assert any(r.levelno == logging.WARNING and "transient" in r.getMessage() for r in caplog.records)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_transient_error_starting_separation_reverts_claims(fs, ff, gcs, clock, caplog):
    from google.api_core.exceptions import ServiceUnavailable

    from tests.unit.voice_pool.conftest import FakeRunner

    ff.budgets = [(200, {**ALLOWED, "batch_allowed_now": False, "retry_after_s": 9999})]
    ids = await seed(fs, 25, status="fetched", fetched_at=ts(clock, 1))
    runner = FakeRunner(fail={separate.SEPARATE_JOB: ServiceUnavailable("blip")})
    runner.is_running = lambda job, region: False  # type: ignore[method-assign]
    out = await go(fs, ff, runner, gcs, clock)
    assert out["separating"] == 0
    assert {statuses(fs)[i] for i in ids} == {"fetched"}
    assert all(fs.cols[FETCH][i]["manifest_run"] is None for i in ids)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
