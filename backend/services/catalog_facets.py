"""Catalog facets for a set of song keys (mood, energy band, karaoke availability, vocal-range tier).

Synchronous SQLite over ``catalog.db`` (+ ``av``): call it off the event loop. The availability
SQL is shared with the v2 recommender (``availability_flags``)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from typing import Literal

from pydantic import BaseModel

from karaoke_decide.catalog.moods import energy_band
from karaoke_decide.catalog.reader import CatalogReader
from karaoke_decide.song_search.library_filter import library_clause

CHUNK = 500


class SongFacets(BaseModel):
    mood: str | None = None
    energy_band: Literal["low", "mid", "high"] | None = None
    has_karaoke: bool = False
    community: bool = False
    karafun: bool = False
    vr_tier: str | None = None


def _chunks(keys: Sequence[str]) -> Iterator[list[str]]:
    for i in range(0, len(keys), CHUNK):
        yield list(keys[i : i + CHUNK])


def _marks(n: int) -> str:
    return ",".join("?" * n)


def availability_flags(
    conn: sqlite3.Connection, has_av: bool, in_kn: Mapping[str, bool]
) -> dict[str, tuple[bool, bool, bool]]:
    """``song_key -> (community, commercial, karafun)`` for the songs (keys of ``in_kn``) with a
    karaoke version. ``in_kn`` is catalog.db's ``songs.in_kn`` per key."""
    keys = list(in_kn)
    if not has_av:
        return {k: (False, True, False) for k in keys if in_kn[k]}
    out: dict[str, tuple[bool, bool, bool]] = {}
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
        for r in conn.execute(sql, (*community_params, *karafun_params, *chunk)):
            out[r[0]] = (bool(r[1]), bool(r[2]), bool(r[3]))
    for k in keys:  # in KN but under another spelling than availability.db's: commercial only
        if k not in out and in_kn[k]:
            out[k] = (False, True, False)
    return out


def song_facets(reader: CatalogReader, keys: Sequence[str]) -> dict[str, SongFacets]:
    """Facets per catalogued key; keys not in the catalog are omitted."""
    conn = reader.conn()
    rows: dict[str, sqlite3.Row] = {}
    for chunk in _chunks(list(dict.fromkeys(keys))):
        sql = f"SELECT song_key, mood, energy, in_kn, vr_tier FROM songs WHERE song_key IN ({_marks(len(chunk))})"
        for r in conn.execute(sql, chunk):
            rows[r["song_key"]] = r
    avail = availability_flags(conn, reader.has_availability, {k: bool(r["in_kn"]) for k, r in rows.items()})
    out: dict[str, SongFacets] = {}
    for k, r in rows.items():
        a = avail.get(k)
        band = energy_band(r["energy"])
        out[k] = SongFacets(
            mood=r["mood"],
            energy_band=band,  # type: ignore[arg-type]
            has_karaoke=a is not None,
            community=bool(a and a[0]),
            karafun=bool(a and a[2]),
            vr_tier=r["vr_tier"],
        )
    return out


def song_titles(reader: CatalogReader, keys: Sequence[str]) -> dict[str, tuple[str, str]]:
    """``song_key -> (artist, title)`` for catalogued keys."""
    conn = reader.conn()
    out: dict[str, tuple[str, str]] = {}
    for chunk in _chunks(list(dict.fromkeys(keys))):
        for r in conn.execute(
            f"SELECT song_key, artist, title FROM songs WHERE song_key IN ({_marks(len(chunk))})", chunk
        ):
            out[r["song_key"]] = (r["artist"] or "", r["title"] or "")
    return out
