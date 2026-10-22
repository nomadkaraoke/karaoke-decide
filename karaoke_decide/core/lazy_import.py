"""Deferred imports for heavy client libraries (API cold start).

``lazy_module("google.cloud.firestore")`` returns a stand-in that imports the real module on
first attribute access, so importing a service module (and with it ``backend.main``) doesn't
pay for Firestore/grpc/protobuf, Cloud Tasks or ytmusicapi. Modules use it as::

    if TYPE_CHECKING:
        from google.cloud import firestore
    else:
        firestore = lazy_module("google.cloud.firestore")

so type checkers see the real module and ``firestore.AsyncClient`` works unchanged at runtime
(``unittest.mock.patch("pkg.mod.firestore.AsyncClient")`` too: it sets and removes the
attribute on the stand-in). ``preload`` imports them for real, off the request path.
"""

from __future__ import annotations

import importlib
import logging
import threading
import time
from collections.abc import Iterable
from types import ModuleType
from typing import Any

logger = logging.getLogger(__name__)


class LazyModule:
    """Imports ``name`` on first attribute access, then delegates to it."""

    def __init__(self, name: str) -> None:
        self.__dict__["_lazy_name"] = name

    def _lazy_load(self) -> ModuleType:
        return importlib.import_module(self.__dict__["_lazy_name"])

    def __getattr__(self, attr: str) -> Any:
        return getattr(self._lazy_load(), attr)

    def __repr__(self) -> str:
        return f"<lazy module {self.__dict__['_lazy_name']!r}>"


def lazy_module(name: str) -> Any:
    return LazyModule(name)


def preload(names: Iterable[str]) -> threading.Thread:
    """Import ``names`` in a daemon thread (so the first request doesn't); never raises."""

    def work() -> None:
        started = time.monotonic()
        for name in names:
            try:
                importlib.import_module(name)
            except Exception as exc:  # a broken optional client must not take the API down
                logger.warning("Preloading %s failed: %s", name, exc)
        logger.info("Preloaded client libraries in %.1fs", time.monotonic() - started)

    thread = threading.Thread(target=work, name="preload-imports", daemon=True)
    thread.start()
    return thread
