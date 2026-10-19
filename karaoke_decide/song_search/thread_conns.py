"""Every thread's read-only SQLite connection to one index, so ``close()`` can
reach them all (the search service's shutdown).

Connections are registered with the thread that opened them. Worker threads come
and go (AnyIO's pool retires idle threads), so each new registration first closes
and drops the connections of threads that have exited: the registry stays bounded
by the number of live threads. (``sqlite3.Connection`` can't be weak-referenced.)
"""

from __future__ import annotations

import sqlite3
import threading


class ThreadConnections:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: list[tuple[threading.Thread, sqlite3.Connection]] = []

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def add(self, conn: sqlite3.Connection) -> None:
        """Register ``conn`` for the current thread; close those of dead threads."""
        with self._lock:
            dead = [c for t, c in self._entries if not t.is_alive()]
            self._entries = [(t, c) for t, c in self._entries if t.is_alive()]
            self._entries.append((threading.current_thread(), conn))
        for c in dead:
            c.close()

    def discard(self, conn: sqlite3.Connection) -> None:
        with self._lock:
            self._entries = [(t, c) for t, c in self._entries if c is not conn]

    def close_all(self) -> None:
        with self._lock:
            conns = [c for _, c in self._entries]
            self._entries = []
        for c in conns:
            c.close()
