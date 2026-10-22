"""v2 candidate generation: bounded, batched SQLite queries over catalog.db (+ ``av``).

Every source applies the request's ``where_sql`` filter; the union is deduped by
``song_key`` and hidden songs are dropped. Synchronous (SQLite): call it off the event loop.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import TypeVar

from backend.services.recs_v2.context import UserContextV2
from backend.services.recs_v2.filters import RecFilters, where_sql
from karaoke_decide.catalog.keys import artist_key
from karaoke_decide.catalog.moods import ENERGY_BANDS
from karaoke_decide.catalog.reader import CatalogReader
from karaoke_decide.song_search.library_filter import library_clause

# Source caps (task brief B8).
KNOWN_ARTISTS = 200
KNOWN_ARTIST_SONGS = 40
SIMILAR_ARTISTS = 100
SIMILAR_ARTIST_SONGS = 15
SIMILAR_PER_SEED = 30  # artist_similar keeps the top 30 per artist
QUIZ_SONGS = 300
CROWD_SONGS = 300
CROWD_MIN_POPULARITY = 70
MAKE_IT_SONGS = 10
KNOWN_SONGS = 300  # the user's own known songs, so "Songs you know" never depends on artist rank

ENERGY_PREF_BAND = {"chill": "low", "medium": "mid", "high": "high"}

_CHUNK = 500
_COLS = (
    "s.song_key, s.artist, s.title, s.artist_key, s.brand_count, s.is_classic, s.explicit, s.duration_ms, "
    "s.year, s.decade, s.popularity, s.energy, s.mood, s.lead_gender, s.genres_json, s.in_kn"
)
_POP_ORDER = "s.popularity DESC NULLS LAST, s.song_key"

_T = TypeVar("_T")


@dataclass
class Candidate:
    song_key: str
    artist: str
    title: str
    artist_key: str
    has_karaoke: bool
    community: bool
    commercial: bool
    karafun: bool
    brand_count: int
    is_classic: bool
    explicit: bool
    duration_ms: int | None
    year: int | None
    decade: str | None
    popularity: int | None
    energy: float | None
    mood: str | None
    lead_gender: str | None
    genres: tuple[str, ...]
    similar_via: str | None = None  # the seed artist_key this song's artist is similar to
    similar_score: float = 0.0  # max over seeds of weight(seed) * score(seed, artist)


def _chunks(items: Sequence[_T]) -> Iterator[Sequence[_T]]:
    for i in range(0, len(items), _CHUNK):
        yield items[i : i + _CHUNK]


def _marks(n: int) -> str:
    return ",".join("?" * n)


class _Gatherer:
    def __init__(self, reader: CatalogReader, f: RecFilters) -> None:
        self.conn = reader.conn()
        self.has_av = reader.has_availability
        self.where, self.params = where_sql(f, has_availability=self.has_av)
        self.rows: dict[str, sqlite3.Row] = {}

    def _add(self, rows: Iterator[sqlite3.Row] | list[sqlite3.Row]) -> int:
        n = 0
        for r in rows:
            self.rows.setdefault(r["song_key"], r)
            n += 1
        return n

    def per_artist(self, artist_keys: Sequence[str], per: int, extra: str = "1") -> None:
        """The ``per`` most popular matching songs for each artist."""
        keys = list(dict.fromkeys(k for k in artist_keys if k))
        for chunk in _chunks(keys):
            sql = f"""
                SELECT * FROM (
                    SELECT {_COLS}, ROW_NUMBER() OVER (PARTITION BY s.artist_key ORDER BY {_POP_ORDER}) AS rn
                    FROM songs s
                    WHERE s.artist_key IN ({_marks(len(chunk))}) AND ({extra}) AND ({self.where})
                ) WHERE rn <= ?
            """
            self._add(self.conn.execute(sql, (*chunk, *self.params, per)))

    def top(self, cond: str, params: Sequence[object], limit: int) -> int:
        sql = f"SELECT {_COLS} FROM songs s WHERE ({cond}) AND ({self.where}) ORDER BY {_POP_ORDER} LIMIT ?"
        return self._add(self.conn.execute(sql, (*params, *self.params, limit)))

    def by_keys(self, song_keys: Sequence[str]) -> None:
        for chunk in _chunks(list(song_keys)):
            sql = f"SELECT {_COLS} FROM songs s WHERE s.song_key IN ({_marks(len(chunk))}) AND ({self.where})"
            self._add(self.conn.execute(sql, (*chunk, *self.params)))

    def no_karaoke_cond(self) -> str:
        if self.has_av:
            return "NOT EXISTS (SELECT 1 FROM av.songs x WHERE x.song_key = s.song_key)"
        return "COALESCE(s.in_kn, 0) = 0"

    def availability(self) -> dict[str, tuple[bool, bool, bool]]:
        """``song_key -> (community, commercial, karafun)`` for songs with a karaoke version."""
        out: dict[str, tuple[bool, bool, bool]] = {}
        keys = list(self.rows)
        if not self.has_av:
            return {k: (False, True, False) for k in keys if self.rows[k]["in_kn"]}
        community_sql, community_params = library_clause("community", "x.song_key", schema="av")
        karafun_sql, karafun_params = library_clause("karafun", "x.song_key", schema="av")
        for chunk in _chunks(keys):
            sql = f"""
                SELECT x.song_key,
                    {community_sql},
                    EXISTS (SELECT 1 FROM av.versions v WHERE v.song_key = x.song_key AND v.kind = 'commercial'),
                    {karafun_sql}
                FROM av.songs x WHERE x.song_key IN ({_marks(len(chunk))})
            """
            for r in self.conn.execute(sql, (*community_params, *karafun_params, *chunk)):
                out[r[0]] = (bool(r[1]), bool(r[2]), bool(r[3]))
        return out


def similar_artists(reader: CatalogReader, ctx: UserContextV2, seeds: Sequence[str]) -> dict[str, tuple[str, float]]:
    """``artist_key -> (seed, weight(seed) * score)`` for the top ``SIMILAR_ARTISTS`` non-known artists."""
    best: dict[str, tuple[str, float]] = {}
    for seed, other, s in reader.similar(seeds, limit_per=SIMILAR_PER_SEED):
        if other in ctx.artists:
            continue  # already an artist the user loves
        value = ctx.artists[seed].weight * s
        cur = best.get(other)
        if cur is None or value > cur[1] or (value == cur[1] and seed < cur[0]):
            best[other] = (seed, value)
    ranked = sorted(best.items(), key=lambda kv: (-kv[1][1], kv[0]))[:SIMILAR_ARTISTS]
    return dict(ranked)


def _quiz(g: _Gatherer, ctx: UserContextV2) -> None:
    """Quiz genres (ignoring "other"), narrowed by quiz decades and energy band when set.

    A narrow genre x decade x energy slice tops up with the genre's other songs, so a
    niche answer still fills the source."""
    genres = sorted(ctx.genres - {"other"})
    decades = sorted(ctx.decades)
    band = ENERGY_BANDS.get(ENERGY_PREF_BAND.get(ctx.energy_pref or "", ""))
    base = "1"
    base_p: list[object] = []
    if genres:
        base = (
            "EXISTS (SELECT 1 FROM song_genres q WHERE q.song_key = s.song_key "
            f"AND q.genre_id IN ({_marks(len(genres))}))"
        )
        base_p = list(genres)
    narrow: list[str] = []
    narrow_p: list[object] = []
    if decades:
        narrow.append(f"s.decade IN ({_marks(len(decades))})")
        narrow_p.extend(decades)
    if band and (genres or decades):
        narrow.append("s.energy >= ? AND s.energy < ?")
        narrow_p.extend(band)
    if not genres and not decades:
        return
    if not narrow:
        g.top(base, base_p, QUIZ_SONGS)
        return
    narrow_sql = " AND ".join(f"({c})" for c in narrow)
    got = g.top(f"({base}) AND {narrow_sql}", [*base_p, *narrow_p], QUIZ_SONGS)
    if got < QUIZ_SONGS and genres:
        g.top(f"({base}) AND NOT COALESCE(({narrow_sql}), 0)", [*base_p, *narrow_p], QUIZ_SONGS - got)


def _candidate(r: sqlite3.Row, avail: dict[str, tuple[bool, bool, bool]]) -> Candidate:
    flags = avail.get(r["song_key"])
    try:
        genres = tuple(json.loads(r["genres_json"] or "[]"))
    except ValueError:
        genres = ()
    return Candidate(
        song_key=r["song_key"],
        artist=r["artist"] or "",
        title=r["title"] or "",
        artist_key=r["artist_key"] or "",
        has_karaoke=flags is not None,
        community=bool(flags and flags[0]),
        commercial=bool(flags and flags[1]),
        karafun=bool(flags and flags[2]),
        brand_count=int(r["brand_count"] or 0),
        is_classic=bool(r["is_classic"]),
        explicit=bool(r["explicit"]),
        duration_ms=r["duration_ms"],
        year=r["year"],
        decade=r["decade"],
        popularity=r["popularity"],
        energy=r["energy"],
        mood=r["mood"],
        lead_gender=r["lead_gender"],
        genres=genres,
    )


def _outside_catalog(key: str, artist: str, title: str) -> Candidate:
    """A known song with no karaoke version that catalog.db doesn't carry (no facets)."""
    return Candidate(
        song_key=key,
        artist=artist,
        title=title,
        artist_key=artist_key(artist),
        has_karaoke=False,
        community=False,
        commercial=False,
        karafun=False,
        brand_count=0,
        is_classic=False,
        explicit=False,
        duration_ms=None,
        year=None,
        decade=None,
        popularity=None,
        energy=None,
        mood=None,
        lead_gender=None,
        genres=(),
    )


def gather_candidates(reader: CatalogReader, ctx: UserContextV2, f: RecFilters) -> list[Candidate]:
    g = _Gatherer(reader, f)
    known_artists = [k for k, _ in sorted(ctx.artists.items(), key=lambda kv: (-kv[1].weight, kv[0]))][:KNOWN_ARTISTS]

    # Songs you know (bounded by weight), then known artists, similar artists, quiz, crowd pleasers, make it.
    known = sorted(ctx.known_songs.items(), key=lambda kv: (-kv[1], kv[0]))[:KNOWN_SONGS]
    g.by_keys([k for k, _ in known])
    g.per_artist(known_artists, KNOWN_ARTIST_SONGS)
    similar = similar_artists(reader, ctx, known_artists) if known_artists else {}
    g.per_artist(list(similar), SIMILAR_ARTIST_SONGS)
    _quiz(g, ctx)
    g.top("s.is_classic = 1 OR s.popularity >= ?", [CROWD_MIN_POPULARITY], CROWD_SONGS)
    g.per_artist(known_artists, MAKE_IT_SONGS, g.no_karaoke_cond())

    avail = g.availability()
    out: dict[str, Candidate] = {}
    for key, row in g.rows.items():
        if key in ctx.hidden:
            continue
        c = _candidate(row, avail)
        via = similar.get(c.artist_key)
        if via is not None:
            c.similar_via, c.similar_score = via
        out[key] = c

    # Known songs with no karaoke version that catalog.db doesn't carry have no facets to
    # filter on, so they only join an unfiltered request.
    if g.where == "1":
        for key, artist, title in ctx.known_without_karaoke:
            if key not in out and key not in ctx.hidden:
                out[key] = _outside_catalog(key, artist, title)
    return list(out.values())
