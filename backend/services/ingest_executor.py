"""A small, bounded thread pool for background sync / import work.

Sync tasks (Cloud Tasks -> ``/internal/sync/process``) and import uploads run on the API service.
Their blocking steps (track matching, the BigQuery artist-metadata lookup, export-chunk
aggregation, ingest row/doc building) run here instead of the event loop's default executor, so
one big sync or import can never take every default-pool thread that user requests'
``asyncio.to_thread`` calls (catalog search, recommendations) wait on.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import logging
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import ParamSpec, TypeVar

logger = logging.getLogger(__name__)

INGEST_MAX_WORKERS = 2

INGEST_EXECUTOR = ThreadPoolExecutor(max_workers=INGEST_MAX_WORKERS, thread_name_prefix="ingest")

P = ParamSpec("P")
R = TypeVar("R")


async def run_ingest(fn: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs) -> R:
    """``asyncio.to_thread`` on ``INGEST_EXECUTOR``: run blocking background work off the loop."""
    loop = asyncio.get_running_loop()
    ctx = contextvars.copy_context()
    return await loop.run_in_executor(INGEST_EXECUTOR, functools.partial(ctx.run, fn, *args, **kwargs))


def default_pool_size() -> int:
    """The worker count of asyncio's default executor (``ThreadPoolExecutor()``'s default)."""
    cpus = getattr(os, "process_cpu_count", os.cpu_count)() or 1
    return min(32, cpus + 4)


def log_pool_sizes() -> None:
    """One startup line, for diagnosing capacity on Cloud Run."""
    logger.info(
        "Thread pools: os.cpu_count()=%s, default executor=%d workers, ingest executor=%d workers",
        os.cpu_count(),
        default_pool_size(),
        INGEST_MAX_WORKERS,
    )
