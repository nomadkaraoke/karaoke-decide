"""The phone's offline index (``offline.db``, spec §7.1).

A ``song_id.db``-schema index (read by the unchanged ``SongIdentifier`` and by the
mobile TS port) holding:

  * every karaoke song of ``song_id.db`` (``karaoke = 1``), with its versions;
  * **rivals** (``karaoke = 0``): the non-karaoke songs that would compete with a
    karaoke song for a query, so the matcher's margin / same-title popularity
    rules see the same field as in the cloud. Rule (``select_rivals``): same
    ``song_norm`` title as a karaoke song, popularity ≥ 30, the 10 most popular
    per title. A rival can win the identification ("no karaoke version yet") but
    never has versions.

Tables: ``songs`` (song-id columns + ``song_key``), ``songs_fts``, ``artists`` (+
``artists_fts``; popularity copied from the cloud index so the artist-first path
orders artists as in the cloud), ``vocab``, ``meta`` (song-id keys +
``offline_schema_version``, ``rivals``, ``versions``, ``availability_run``), plus
availability's ``versions``, ``libraries`` and ``song_libraries`` for the karaoke
songs. ``songs.song_key`` is ``text_normalize.song_key(artist, title)`` of the
song-id display spelling, the key availability stores versions under (ruling R9).

On top of the smaller index, the offline matcher raises the confident margin by
``OFFLINE_MARGIN_BONUS``: offline asks "Which one?" a little more often rather than
naming the wrong song.
"""

from __future__ import annotations

import csv
import os
import sqlite3
from pathlib import Path
from typing import Any

from karaoke_decide.song_search.build_song_id import build as build_song_id
from karaoke_decide.song_search.text_normalize import song_key

OFFLINE_SCHEMA_VERSION = 1
OFFLINE_MARGIN_BONUS = 0.03

RIVAL_MIN_POP = 30
RIVALS_PER_TITLE = 10

# A song-id TSV row: (artist, title, popularity | None, karaoke 0/1)
SongRow = tuple[str, str, int | None, int]

_VERSIONS_SQL = """
    CREATE TABLE versions(
        song_key TEXT NOT NULL,
        brand_code TEXT NOT NULL,
        brand_name TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('community', 'commercial')),
        youtube_url TEXT NULL,
        rank INT NOT NULL
    );
    CREATE TABLE libraries(code TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, source TEXT NOT NULL);
    CREATE TABLE song_libraries(song_key TEXT NOT NULL, library_code TEXT NOT NULL,
                                PRIMARY KEY (song_key, library_code)) WITHOUT ROWID;
"""


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def select_rivals(
    song_id_db: Path,
    karaoke_title_norms: set[str],
    *,
    min_pop: int = RIVAL_MIN_POP,
    per_title_cap: int = RIVALS_PER_TITLE,
) -> list[SongRow]:
    """Non-karaoke songs whose title norm (``songs.nt`` = ``song_norm(title)``) is in
    ``karaoke_title_norms``, with popularity ≥ ``min_pop``: the ``per_title_cap``
    most popular per title. → ``[(artist, title, pop, 0)]`` ordered by title norm
    then popularity."""
    if not karaoke_title_norms:
        return []
    conn = _ro(Path(song_id_db))
    try:
        conn.execute("CREATE TEMP TABLE titles(nt TEXT PRIMARY KEY) WITHOUT ROWID")
        conn.executemany("INSERT OR IGNORE INTO temp.titles VALUES (?)", ((t,) for t in karaoke_title_norms))
        rows = conn.execute(
            """
            SELECT artist, title, pop FROM (
                SELECT s.artist, s.title, s.pop, s.nt,
                       ROW_NUMBER() OVER (PARTITION BY s.nt ORDER BY s.pop DESC, s.rowid) AS n
                FROM songs s JOIN temp.titles t ON t.nt = s.nt
                WHERE s.karaoke = 0 AND s.pop >= ?
            ) WHERE n <= ? ORDER BY nt, n
            """,
            (min_pop, per_title_cap),
        ).fetchall()
    finally:
        conn.close()
    return [(a, t, p, 0) for a, t, p in rows]


def _karaoke_songs(song_id_db: Path) -> tuple[list[SongRow], set[str]]:
    """(karaoke song rows, their title norms)."""
    conn = _ro(song_id_db)
    try:
        rows = conn.execute("SELECT artist, title, pop, nt FROM songs WHERE karaoke = 1 ORDER BY rowid").fetchall()
    finally:
        conn.close()
    return [(a, t, p, 1) for a, t, p, _nt in rows], {nt for *_rest, nt in rows}


def _write_tsv(path: Path, rows: list[SongRow]) -> None:
    """The song-id TSV format ``build_song_id`` reads (blank popularity = None)."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        for artist, title, pop, karaoke in rows:
            w.writerow([artist, title, "" if pop is None else pop, karaoke])


def build_offline_db(song_id_db: Path, availability_db: Path, out_path: Path, run: str) -> dict[str, Any]:
    """Build ``out_path`` (atomic replace) from ``song_id.db`` + ``availability.db``.

    → ``{songs, rivals, versions, bytes}``: karaoke songs, rival songs, version rows,
    file size."""
    song_id_db, availability_db, out_path = Path(song_id_db), Path(availability_db), Path(out_path)
    karaoke, title_norms = _karaoke_songs(song_id_db)
    rivals = select_rivals(song_id_db, title_norms)

    dst = str(out_path)
    tsv, tmp = Path(dst + ".songs.tsv"), dst + ".build"
    try:
        _write_tsv(tsv, karaoke + rivals)
        # The song-id builder over the filtered rows: same normalisation, FTS, vocab.
        built = build_song_id([tsv], Path(tmp), run)
        if built["songs"] != len(karaoke) + len(rivals):  # song-id rows are already merged: nothing re-merges
            raise RuntimeError(f"offline build merged songs: {built['songs']} != {len(karaoke) + len(rivals)}")
        n_versions = _add_offline_tables(Path(tmp), song_id_db, availability_db, n_rivals=len(rivals))
        os.replace(tmp, dst)
    finally:
        for stale in (tsv, Path(tmp)):
            if stale.exists():
                stale.unlink()
    return {"songs": len(karaoke), "rivals": len(rivals), "versions": n_versions, "bytes": out_path.stat().st_size}


def _add_offline_tables(db_path: Path, song_id_db: Path, availability_db: Path, *, n_rivals: int) -> int:
    """song_key column, cloud artist popularity, versions/libraries; → version rows."""
    conn = sqlite3.connect(f"file:{db_path}", uri=True)  # URI mode: the ATTACHes below are read-only
    try:
        conn.execute("ALTER TABLE songs ADD COLUMN song_key TEXT")
        conn.executemany(
            "UPDATE songs SET song_key = ? WHERE rowid = ?",
            [(song_key(a, t), rid) for rid, a, t in conn.execute("SELECT rowid, artist, title FROM songs").fetchall()],
        )
        conn.execute("CREATE INDEX songs_song_key ON songs(song_key)")
        conn.executescript(_VERSIONS_SQL)
        conn.execute("ATTACH DATABASE ? AS cloud", (f"file:{song_id_db}?mode=ro",))
        conn.execute("ATTACH DATABASE ? AS av", (f"file:{availability_db}?mode=ro",))
        conn.execute(
            "UPDATE artists SET (artist, pop, songs) = "
            "(SELECT c.artist, c.pop, c.songs FROM cloud.artists c WHERE c.na = artists.na) "
            "WHERE na IN (SELECT na FROM cloud.artists)"
        )
        # Versions for karaoke songs only: a rival never produces karaoke rows.
        conn.execute(
            "INSERT INTO versions SELECT v.song_key, v.brand_code, v.brand_name, v.kind, v.youtube_url, v.rank "
            "FROM av.versions v WHERE v.song_key IN (SELECT song_key FROM songs WHERE karaoke = 1) ORDER BY v.rowid"
        )
        conn.execute("INSERT INTO libraries SELECT * FROM av.libraries ORDER BY code")
        conn.execute(
            "INSERT INTO song_libraries SELECT l.song_key, l.library_code FROM av.song_libraries l "
            "WHERE l.song_key IN (SELECT song_key FROM songs WHERE karaoke = 1)"
        )
        conn.execute("CREATE INDEX versions_song ON versions(song_key)")
        n_versions = conn.execute("SELECT count(*) FROM versions").fetchone()[0]
        av_run = conn.execute("SELECT value FROM av.meta WHERE key = 'run'").fetchone()
        conn.executemany(
            "INSERT OR REPLACE INTO meta VALUES (?,?)",
            [
                ("offline_schema_version", str(OFFLINE_SCHEMA_VERSION)),
                ("rivals", str(n_rivals)),
                ("versions", str(n_versions)),
                ("availability_run", av_run[0] if av_run else ""),
            ],
        )
        conn.commit()
        conn.execute("DETACH DATABASE cloud")
        conn.execute("DETACH DATABASE av")
        conn.execute("VACUUM")
    finally:
        conn.close()
    return int(n_versions)


# ============================================================== query side


def offline_groups(db_path: str | Path, ident: dict[str, Any]) -> list[dict[str, Any]]:
    """Karaoke groups the phone shows for an ``identify`` result over ``offline.db``.

    ``confident`` → the identified song's group; ``candidates`` → one group per
    candidate (identify order) that has versions; ``none`` → ``[]``. Songs without
    versions (rivals) produce no group. Each group:
    ``{song_key, artist, title, karaoke, version_count, versions}``, versions
    playable first then ``rank`` (availability's order)."""
    status = ident.get("status")
    if status == "confident" and ident.get("best"):
        songs = [ident["best"]]
    elif status == "candidates":
        songs = list(ident.get("candidates") or [])
    else:
        return []
    keys = list(dict.fromkeys(song_key(s["artist"], s["title"]) for s in songs))
    conn = _ro(Path(db_path))
    try:
        groups = []
        for key in keys:
            song = conn.execute(
                "SELECT artist, title, karaoke FROM songs WHERE song_key = ? AND karaoke = 1 LIMIT 1", (key,)
            ).fetchone()
            if song is None:
                continue
            versions = [
                {"brand_code": c, "brand_name": n, "kind": k, "youtube_url": u, "rank": r}
                for c, n, k, u, r in conn.execute(
                    "SELECT brand_code, brand_name, kind, youtube_url, rank FROM versions "
                    "WHERE song_key = ? ORDER BY youtube_url IS NULL, rank, rowid",
                    (key,),
                )
            ]
            if versions:
                groups.append(
                    {
                        "song_key": key,
                        "artist": song[0],
                        "title": song[1],
                        "karaoke": True,
                        "version_count": len(versions),
                        "versions": versions,
                    }
                )
        return groups
    finally:
        conn.close()
