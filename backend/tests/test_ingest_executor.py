"""The bounded ingest executor runs background work off the default pool."""

import contextvars
import logging
import threading

import pytest

from backend.services import ingest_executor
from backend.services.ingest_executor import INGEST_MAX_WORKERS, default_pool_size, log_pool_sizes, run_ingest

request_tag: contextvars.ContextVar[str] = contextvars.ContextVar("request_tag", default="unset")


async def test_run_ingest_runs_on_an_ingest_thread_with_args_and_context() -> None:
    def work(a: int, *, b: int) -> tuple[int, str, str]:
        return a + b, threading.current_thread().name, request_tag.get()

    request_tag.set("sync-job-1")
    total, thread_name, tag = await run_ingest(work, 2, b=3)

    assert total == 5
    assert thread_name.startswith("ingest")
    assert tag == "sync-job-1"  # contextvars travel with the call, as with asyncio.to_thread


async def test_run_ingest_propagates_exceptions() -> None:
    def boom() -> None:
        raise ValueError("bad chunk")

    with pytest.raises(ValueError, match="bad chunk"):
        await run_ingest(boom)


def test_pool_is_bounded() -> None:
    assert INGEST_MAX_WORKERS == 2
    assert ingest_executor.INGEST_EXECUTOR._max_workers == INGEST_MAX_WORKERS


def test_default_pool_size_matches_threadpool_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ingest_executor.os, "process_cpu_count", lambda: 1, raising=False)
    monkeypatch.setattr(ingest_executor.os, "cpu_count", lambda: 1)
    assert default_pool_size() == 5
    monkeypatch.setattr(ingest_executor.os, "process_cpu_count", lambda: 64, raising=False)
    monkeypatch.setattr(ingest_executor.os, "cpu_count", lambda: 64)
    assert default_pool_size() == 32


def test_log_pool_sizes(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="backend.services.ingest_executor"):
        log_pool_sizes()
    assert "os.cpu_count()=" in caplog.text
    assert f"default executor={default_pool_size()} workers" in caplog.text
    assert "ingest executor=2 workers" in caplog.text
