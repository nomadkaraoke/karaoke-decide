"""Tests for the vocal-range-gen job (karaoke_decide.etl.vocal_range_gen).

GCS is the shared in-memory FakeBucket, Firestore a dict of job docs, and the audio
decode plus pyin are patched: no librosa or audio files are needed."""

from __future__ import annotations

import csv
import io
import json
import logging
from typing import Any

import pytest

from karaoke_decide.etl import vocal_range_gen as vrg
from karaoke_decide.vocal_range.pitch import PitchStats
from tests.unit.fake_gcs import FakeBucket

STATS = PitchStats(low=48.0, high=70.0, p10=50.0, p50=58.0, p90=66.0, voiced_s=61.0)


def _words(*spans: tuple[float, float], singer: int | None = None) -> list[dict[str, Any]]:
    out = []
    for s, e in spans:
        w: dict[str, Any] = {"text": "la", "start_time": s, "end_time": e}
        if singer is not None:
            w["singer"] = singer
        out.append(w)
    return out


def _corr(words: list[dict[str, Any]], wrapped: bool = False) -> dict[str, Any]:
    body = {"corrected_segments": [{"words": words}]}
    return {"corrections": body} if wrapped else body


def test_select_word_windows_both_shapes() -> None:
    words = _words((1.0, 1.5), (1.6, 2.0), (5.0, 6.0))
    expected = {"all": [(1.0, 2.0), (5.0, 6.0)]}  # 0.1 s gap merged
    assert vrg.select_word_windows(_corr(words)) == expected
    assert vrg.select_word_windows(_corr(words, wrapped=True)) == expected


def test_duet_windows_per_singer() -> None:
    words = _words((1.0, 2.0), singer=1) + _words((3.0, 4.0), singer=2) + _words((5.0, 6.0), singer=1)
    assert vrg.select_word_windows(_corr(words)) == {"1": [(1.0, 2.0), (5.0, 6.0)], "2": [(3.0, 4.0)]}


def test_empty_corrections_have_no_windows() -> None:
    assert vrg.select_word_windows({}) == {}
    assert vrg.select_word_windows(_corr([{"text": "x"}])) == {}


def test_shard_is_stable_and_complete() -> None:
    ids = [f"job{i}" for i in range(200)]
    parts = [vrg.shard(ids, i, 7) for i in range(7)]
    assert sorted(sum(parts, [])) == sorted(ids)
    assert parts == [vrg.shard(ids, i, 7) for i in range(7)]
    assert all(parts)


def _gen(jobs: dict[str, dict[str, Any]], with_stem: set[str]) -> FakeBucket:
    b = FakeBucket()
    for jid, corr in jobs.items():
        b.objects[f"jobs/{jid}/lyrics/corrections.json"] = json.dumps(corr).encode()
        if jid in with_stem:
            b.objects[f"jobs/{jid}/stems/vocals_clean.flac"] = b"flac"
    return b


def _rows(out: FakeBucket, name: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(out.objects[name].decode())))


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch) -> list[list[tuple[float, float]]]:
    seen: list[list[tuple[float, float]]] = []
    monkeypatch.setattr(vrg, "load_audio", lambda blob: ([0.0], 16000))

    def fake_analyze(y: Any, sr: int, windows: Any = None) -> PitchStats:
        seen.append(windows)
        return STATS

    monkeypatch.setattr(vrg, "analyze_vocals", fake_analyze)
    return seen


def test_run_skips_done_and_writes_csv(patched: list[Any]) -> None:
    corr = _corr(_words((1.0, 2.0)))
    gen = _gen({"a": corr, "b": corr, "c": corr}, with_stem={"a", "b", "c"})
    out = FakeBucket()
    out.objects["vocal-range/gen_measured/old/part-0.csv"] = (
        b"source_id,artist,title,lead_part,low_midi,high_midi,p10,p50,p90,voiced_seconds\n" b"a,X,Y,all,1,2,1,1,1,30\n"
    )
    jobs = {
        "a": {"artist": "A", "title": "T"},
        "b": {"artist": "B", "title": "Song, With Comma"},
        "c": {"artist": "C", "title": "T", "tenant_id": "acme"},
    }
    summary = vrg.run(gen, out, jobs.get, run_id="r1", task_index=0, task_count=1)
    rows = _rows(out, "vocal-range/gen_measured/r1/part-0.csv")
    assert [r["source_id"] for r in rows] == ["b"]
    assert rows[0]["title"] == "Song, With Comma" and rows[0]["lead_part"] == "all"
    assert rows[0]["low_midi"] == "48.0" and rows[0]["voiced_seconds"] == "61.0"
    assert summary["analysed"] == 1 and summary["skipped_done"] == 1 and summary["skipped_tenant"] == 1
    assert patched == [[(1.0, 2.0)]]


def test_duet_writes_one_row_per_singer(patched: list[Any]) -> None:
    words = _words((1.0, 2.0), singer=1) + _words((3.0, 4.0), singer=2)
    gen = _gen({"d": _corr(words)}, with_stem={"d"})
    out = FakeBucket()
    vrg.run(gen, out, {"d": {"artist": "A", "title": "T"}}.get, run_id="r", task_index=0, task_count=1)
    rows = _rows(out, "vocal-range/gen_measured/r/part-0.csv")
    assert sorted(r["lead_part"] for r in rows) == ["1", "2"]


def test_corrections_updated_is_preferred(patched: list[Any]) -> None:
    gen = _gen({"a": _corr(_words((1.0, 2.0)))}, with_stem={"a"})
    gen.objects["jobs/a/lyrics/corrections_updated.json"] = json.dumps(_corr(_words((7.0, 8.0)))).encode()
    vrg.run(gen, FakeBucket(), {"a": {"artist": "A", "title": "T"}}.get, run_id="r", task_index=0, task_count=1)
    assert patched == [[(7.0, 8.0)]]


def test_missing_stem_is_skipped_and_counted(patched: list[Any], caplog: pytest.LogCaptureFixture) -> None:
    corr = _corr(_words((1.0, 2.0)))
    gen = _gen({"a": corr, "b": corr, "c": corr}, with_stem={"a"})
    out = FakeBucket()
    jobs = {j: {"artist": "A", "title": "T"} for j in "abc"}
    with caplog.at_level(logging.INFO):
        summary = vrg.run(gen, out, jobs.get, run_id="r", task_index=0, task_count=1)
    assert summary["jobs_listed"] == 3 and summary["with_stem"] == 1 and summary["analysed"] == 1
    assert summary["skipped_no_stem"] == 2
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len([r for r in caplog.records if "no_stem" in r.getMessage()]) == 1  # one aggregate line


def test_all_failures_log_error(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    def boom(blob: Any) -> Any:
        raise RuntimeError("decode failed")

    monkeypatch.setattr(vrg, "load_audio", boom)
    gen = _gen({"a": _corr(_words((1.0, 2.0)))}, with_stem={"a"})
    with caplog.at_level(logging.INFO):
        summary = vrg.run(
            gen, FakeBucket(), {"a": {"artist": "A", "title": "T"}}.get, run_id="r", task_index=0, task_count=1
        )
    assert summary["failed"] == 1
    levels = [r.levelno for r in caplog.records]
    assert levels.count(logging.ERROR) == 1


def test_run_id_prefers_explicit_then_env_then_cloud_run_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOCAL_RANGE_RUN", raising=False)
    monkeypatch.setenv("CLOUD_RUN_EXECUTION", "vocal-range-gen-abc12")
    assert vrg.resolve_run_id() == "vocal-range-gen-abc12"  # same for every task of the execution
    monkeypatch.setenv("VOCAL_RANGE_RUN", "mine")
    assert vrg.resolve_run_id() == "mine"
    assert vrg.resolve_run_id("explicit") == "explicit"
    monkeypatch.delenv("VOCAL_RANGE_RUN")
    monkeypatch.delenv("CLOUD_RUN_EXECUTION")
    assert len(vrg.resolve_run_id()) == 15  # timestamp fallback


def test_error_only_when_every_attempted_job_failed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Skipped (tenant) jobs are not attempts: one success + one failure is not an ERROR."""
    calls = iter([RuntimeError("x"), ([0.0], 16000)])

    def load(blob: Any) -> Any:
        v = next(calls)
        if isinstance(v, Exception):
            raise v
        return v

    monkeypatch.setattr(vrg, "load_audio", load)
    monkeypatch.setattr(vrg, "analyze_vocals", lambda y, sr, windows=None: STATS)
    corr = _corr(_words((1.0, 2.0)))
    gen = _gen({"a": corr, "b": corr, "t": corr}, with_stem={"a", "b", "t"})
    jobs = {"a": {"artist": "A"}, "b": {"artist": "B"}, "t": {"tenant_id": "x"}}
    with caplog.at_level(logging.INFO):
        vrg.run(gen, FakeBucket(), jobs.get, run_id="r", task_index=0, task_count=1)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_non_zero_task_logs_warning_not_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(blob: Any) -> Any:
        raise RuntimeError("decode failed")

    monkeypatch.setattr(vrg, "load_audio", boom)
    monkeypatch.setattr(vrg, "shard", lambda names, idx, count: names)  # give task 1 the job
    gen = _gen({"a": _corr(_words((1.0, 2.0)))}, with_stem={"a"})
    with caplog.at_level(logging.INFO):
        vrg.run(gen, FakeBucket(), {"a": {"artist": "A", "title": "T"}}.get, run_id="r", task_index=1, task_count=2)
    levels = [r.levelno for r in caplog.records]
    assert logging.ERROR not in levels
    assert any("every job failed" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)
