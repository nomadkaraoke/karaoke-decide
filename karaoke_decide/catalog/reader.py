"""Read-only queries over ``catalog.db``, with the search service's ``availability.db``
ATTACHed as ``av`` (join on ``song_key``) when the main API has it.

One read-only connection per thread (SQLite connections aren't shared across threads);
every thread's connection is registered so ``close()`` can reach them all.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections import Counter
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


@dataclass(frozen=True)
class ArtistSummary:
    """A catalog.db artist with its karaoke-song aggregates (the quiz/search fallback when BigQuery is off)."""

    artist_key: str
    name: str
    mbid: str | None
    popularity: int
    genres: tuple[str, ...]
    song_count: int = 0  # karaoke songs (in_kn); brand_total sums theirs
    brand_total: int = 0
    top_songs: tuple[str, ...] = ()
    primary_decade: str | None = None


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _genres(raw: str | None) -> tuple[str, ...]:
    try:
        value = json.loads(raw) if raw else []
    except ValueError:
        return ()
    return tuple(str(g) for g in value) if isinstance(value, list) else ()


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

    def has_karaoke_key(self, song_key: str) -> bool:
        """Whether ``song_key`` has a karaoke version: it is in ``availability.db`` or is a
        catalog.db song that came from KaraokeNerds (the two sources spell some titles differently)."""
        conn = self.conn()
        if self.has_availability and conn.execute("SELECT 1 FROM av.songs WHERE song_key = ?", (song_key,)).fetchone():
            return True
        return conn.execute("SELECT 1 FROM songs WHERE song_key = ? AND in_kn = 1", (song_key,)).fetchone() is not None

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

    def popular_artist_keys(
        self,
        *,
        limit: int,
        genres: Sequence[str] = (),
        decades: Sequence[str] = (),
        exclude_keys: Sequence[str] = (),
    ) -> list[str]:
        """The most popular artists with at least one karaoke song (``in_kn``), best first.

        ``genres`` (our genre ids) keeps artists tagged with any of them; ``decades`` ("1980s")
        keeps artists with a song from any of them; ``exclude_keys`` are dropped."""
        song_filter = ""
        params: list[object] = []
        if decades:
            song_filter = f" AND s.decade IN ({_marks(len(decades))})"
            params.extend(decades)
        genre_filter = ""
        if genres:
            genre_filter = (
                f"AND EXISTS (SELECT 1 FROM json_each(a.genres_json) g WHERE g.value IN ({_marks(len(genres))}))"
            )
            params.extend(genres)
        excluded = set(exclude_keys)
        rows = self.conn().execute(
            f"""
            SELECT a.artist_key FROM artists a
            WHERE EXISTS (SELECT 1 FROM songs s WHERE s.artist_key = a.artist_key AND s.in_kn = 1{song_filter})
            {genre_filter}
            ORDER BY COALESCE(a.popularity, 0) DESC, COALESCE(a.lb_listeners, 0) DESC, a.artist_key
            LIMIT ?
            """,
            (*params, limit + len(excluded)),
        )
        return [r[0] for r in rows if r[0] not in excluded][:limit]

    def artist_summaries(self, artist_keys: Sequence[str], top_songs: int = 3) -> dict[str, ArtistSummary]:
        """``{artist_key: ArtistSummary}`` for the keys catalog.db has an artist row for."""
        keys = list(dict.fromkeys(artist_keys))
        artists: dict[str, sqlite3.Row] = {}
        for chunk in _chunks(keys):
            for r in self.conn().execute(
                f"SELECT artist_key, name, mbid, popularity, genres_json FROM artists "
                f"WHERE artist_key IN ({_marks(len(chunk))})",
                tuple(chunk),
            ):
                artists[r["artist_key"]] = r
        stats: dict[str, tuple[int, int]] = {}
        decades: dict[str, Counter[str]] = {}
        for chunk in _chunks(list(artists)):
            for r in self.conn().execute(
                f"SELECT artist_key, COUNT(*), COALESCE(SUM(brand_count), 0) FROM songs "
                f"WHERE artist_key IN ({_marks(len(chunk))}) AND in_kn = 1 GROUP BY artist_key",
                tuple(chunk),
            ):
                stats[r[0]] = (int(r[1]), int(r[2]))
            for r in self.conn().execute(
                f"SELECT artist_key, decade, COUNT(*) FROM songs "
                f"WHERE artist_key IN ({_marks(len(chunk))}) AND in_kn = 1 AND decade IS NOT NULL "
                f"GROUP BY artist_key, decade",
                tuple(chunk),
            ):
                decades.setdefault(r[0], Counter())[r[1]] = int(r[2])
        titles: dict[str, list[str]] = {}
        for r in self.top_songs_for_artists(list(artists), top_songs):
            titles.setdefault(r["artist_key"], []).append(r["title"])
        out: dict[str, ArtistSummary] = {}
        for key in keys:
            a = artists.get(key)
            if a is None:
                continue
            count, brands = stats.get(key, (0, 0))
            # Most songs wins; ties go to the earlier decade, so the answer is deterministic.
            by_decade = sorted(decades.get(key, Counter()).items(), key=lambda d: (-d[1], d[0]))
            out[key] = ArtistSummary(
                artist_key=key,
                name=a["name"] or key,
                mbid=a["mbid"] or None,
                popularity=int(a["popularity"] or 0),
                genres=_genres(a["genres_json"]),
                song_count=count,
                brand_total=brands,
                top_songs=tuple(titles.get(key, ())),
                primary_decade=by_decade[0][0] if by_decade else None,
            )
        return out

    def search_artists(self, query: str, limit: int = 10) -> list[ArtistSummary]:
        """Artists whose name matches ``query`` (normalised like ``artist_key``): exact, then
        prefix, then word-prefix, then substring; most popular first within each tier."""
        q = artist_key(query)
        if not q:
            return []
        esc = _like_escape(q)
        rows = self.conn().execute(
            r"""
            SELECT artist_key, name, mbid, popularity, genres_json,
                   CASE WHEN artist_key = ? THEN 0
                        WHEN artist_key LIKE ? ESCAPE '\' THEN 1
                        WHEN artist_key LIKE ? ESCAPE '\' THEN 2
                        ELSE 3 END AS tier
            FROM artists
            WHERE artist_key LIKE ? ESCAPE '\'
            ORDER BY tier, COALESCE(popularity, 0) DESC, artist_key
            LIMIT ?
            """,
            (q, f"{esc}%", f"% {esc}%", f"%{esc}%", limit),
        )
        return [
            ArtistSummary(
                artist_key=r["artist_key"],
                name=r["name"] or r["artist_key"],
                mbid=r["mbid"] or None,
                popularity=int(r["popularity"] or 0),
                genres=_genres(r["genres_json"]),
            )
            for r in rows
        ]
