"""ThreadConnections: bounded by live threads, close_all closes everything."""

import sqlite3
import threading

import pytest

from karaoke_decide.song_search.thread_conns import ThreadConnections


def _open() -> sqlite3.Connection:
    return sqlite3.connect(":memory:", check_same_thread=False)


def test_dead_threads_connections_closed_on_next_add() -> None:
    reg = ThreadConnections()
    opened: list[sqlite3.Connection] = []
    for _ in range(50):

        def work() -> None:
            c = _open()
            opened.append(c)
            reg.add(c)

        t = threading.Thread(target=work)
        t.start()
        t.join()
        assert len(reg) == 1  # only the newest; every earlier one was pruned
    reg.add(_open())  # main thread
    assert len(reg) == 1
    for c in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            c.execute("SELECT 1")
    reg.close_all()
    assert len(reg) == 0


def test_discard_forgets_without_closing() -> None:
    reg = ThreadConnections()
    c = _open()
    reg.add(c)
    reg.discard(c)
    assert len(reg) == 0
    c.execute("SELECT 1")
    c.close()
