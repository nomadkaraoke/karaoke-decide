"""Read-only queries over ``catalog.db``, with the search service's ``availability.db``
ATTACHed as ``av`` (join on ``song_key``) when the main API has it.

One read-only connection per thread (SQLite connections aren't shared across threads);
every thread's connection is registered so ``close()`` can reach them all.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from karaoke_decide.catalog.keys import artist_key, match_key
from karaoke_decide.song_search.thread_conns import ThreadConnections

# SQLite's default host-parameter limit is 999 on older builds; stay under it.
_CHUNK = 500

_T = TypeVar("_T")


@dataclass(frozen=True)
class KnMatch:
    """A KaraokeNerds catalog row (``kn_song_map``) matched by ``match_key``."""

    kn_id: int
    artist: str
    title: str
    brands: str
    brand_count: int


def _chunks(items: Sequence[_T]) -> Iterator[Sequence[_T]]:
    for i in range(0, len(items), _CHUNK):
        yield items[i : i + _CHUNK]


def _marks(n: int) -> str:
    return ",".join("?" * n)


class CatalogReader:
    def __init__(self, catalog_path: Path, availability_path: Path | None) -> None:
        self.catalog_path = Path(catalog_path)
        self.availability_path = Path(availability_path) if availability_path is not None else None
        self._local = threading.local()
        self._conns = ThreadConnections()
        self._generation = 0
        self.conn()  # fail fast on a missing / unreadable file

    @property
    def has_availability(self) -> bool:
        return self.availability_path is not None

    def conn(self) -> sqlite3.Connection:
        """This thread's read-only connection (``av`` attached when availability is present)."""
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None or getattr(self._local, "gen", -1) != self._generation:
            conn = sqlite3.connect(f"file:{self.catalog_path}?mode=ro", uri=True, check_same_thread=False)
            try:
                conn.row_factory = sqlite3.Row
                conn.execute("SELECT 1 FROM meta LIMIT 1")
                if self.availability_path is not None:
                    conn.execute("ATTACH DATABASE ? AS av", (f"file:{self.availability_path}?mode=ro",))
                    conn.execute("SELECT 1 FROM av.songs LIMIT 1")
            except BaseException:
                conn.close()
                raise
            self._conns.add(conn)
            self._local.conn, self._local.gen = conn, self._generation
        return conn

    def close(self) -> None:
        """Close every thread's connection; later queries reopen."""
        self._generation += 1
        self._conns.close_all()

    # ---------------------------------------------------------------- queries

    def meta(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in self.conn().execute("SELECT key, value FROM meta")}

    def artist_mbid(self, name: str) -> str | None:
        row = self.conn().execute("SELECT mbid FROM artists WHERE artist_key = ?", (artist_key(name),)).fetchone()
        return row["mbid"] if row and row["mbid"] else None

    def similar(self, artist_keys: Sequence[str], limit_per: int = 30) -> list[tuple[str, str, float]]:
        """``(artist_key, similar_key, score)``, the top ``limit_per`` per artist by score."""
        keys = list(dict.fromkeys(artist_keys))
        out: list[tuple[str, str, float]] = []
        for chunk in _chunks(keys):
            rows = self.conn().execute(
                f"""
                SELECT artist_key, similar_key, score FROM (
                    SELECT artist_key, similar_key, score,
                           ROW_NUMBER() OVER (PARTITION BY artist_key ORDER BY score DESC, similar_key) AS rn
                    FROM artist_similar WHERE artist_key IN ({_marks(len(chunk))})
                ) WHERE rn <= ?
                ORDER BY score DESC, artist_key, similar_key
                """,
                (*chunk, limit_per),
            )
            out.extend((r[0], r[1], float(r[2])) for r in rows)
        out.sort(key=lambda r: (-r[2], r[0], r[1]))
        return out

    def kn_song_keys(self, kn_ids: Sequence[int]) -> dict[int, str]:
        """``{kn_id: song_key}`` for the KaraokeNerds ids catalog.db knows."""
        ids = list(dict.fromkeys(kn_ids))
        out: dict[int, str] = {}
        for chunk in _chunks(ids):
            rows = self.conn().execute(
                f"SELECT kn_id, song_key FROM kn_song_map WHERE kn_id IN ({_marks(len(chunk))})", tuple(chunk)
            )
            out.update((int(r[0]), r[1]) for r in rows)
        return out

    def kn_ids_for_song_keys(self, song_keys: Sequence[str]) -> dict[str, int]:
        """``{song_key: kn_id}``; with several KaraokeNerds rows per song the one with the most brands wins."""
        keys = list(dict.fromkeys(song_keys))
        out: dict[str, int] = {}
        for chunk in _chunks(keys):
            rows = self.conn().execute(
                f"""
                SELECT song_key, kn_id FROM kn_song_map WHERE song_key IN ({_marks(len(chunk))})
                ORDER BY brand_count DESC, kn_id DESC
                """,
                tuple(chunk),
            )
            for r in rows:  # rows arrive best first
                out.setdefault(r[0], int(r[1]))
        return out

    def match_kn(self, artist: str, title: str) -> KnMatch | None:
        """The KaraokeNerds song for a listening-history track (legacy ``CatalogLookup`` key).

        Several KN rows can share one ``match_key``; the one with the most brands wins
        (ties: lowest ``kn_id``), so the result is deterministic."""
        if not artist or not title:
            return None
        row = (
            self.conn()
            .execute(
                """
            SELECT kn_id, artist, title, brands, brand_count FROM kn_song_map
            WHERE match_key = ? ORDER BY brand_count DESC, kn_id LIMIT 1
            """,
                (match_key(artist, title),),
            )
            .fetchone()
        )
        if row is None:
            return None
        return KnMatch(
            kn_id=int(row["kn_id"]),
            artist=row["artist"],
            title=row["title"],
            brands=row["brands"] or "",
            brand_count=int(row["brand_count"] or 0),
        )

    def top_songs_for_artists(self, artist_keys: Sequence[str], per_artist: int) -> list[sqlite3.Row]:
        """``songs`` rows, the ``per_artist`` most popular for each artist."""
        keys = list(dict.fromkeys(artist_keys))
        out: list[sqlite3.Row] = []
        for chunk in _chunks(keys):
            out.extend(
                self.conn().execute(
                    f"""
                    SELECT * FROM (
                        SELECT s.*, ROW_NUMBER() OVER (
                            PARTITION BY s.artist_key ORDER BY s.popularity DESC, s.song_key
                        ) AS artist_rank
                        FROM songs s WHERE s.artist_key IN ({_marks(len(chunk))})
                    ) WHERE artist_rank <= ?
                    ORDER BY artist_key, artist_rank
                    """,
                    (*chunk, per_artist),
                )
            )
        return out
