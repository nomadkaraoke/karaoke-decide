"""The phone's offline index (``offline.db``, spec §7.1).

A ``song_id.db``-schema index (read by the unchanged ``SongIdentifier`` and by the
mobile TS port) holding:

  * every karaoke song of ``song_id.db`` (``karaoke = 1``), with its versions;
  * **rivals** (``karaoke = 0``): the non-karaoke songs that would compete with a
    karaoke song for a query. Rule (``select_rivals``): same **base title**
    (``rival_key``: the matcher's dedupe title, version suffixes such as "(Live)" /
    "- Remastered" dropped, plus one trailing plural "s" folded, on both sides) as a
    karaoke song, any popularity, the 10 most popular per base title ranked by
    ``COALESCE(pop, 30)`` (how the matcher counts a blank). The matcher's same-title
    popularity lead, generic-title count and margin then see the same competitors
    as in the cloud for those titles. Other near-titles (fuzzy misspellings, word
    variants) are not carried; the residual cases are measured by ``dual_eval`` in
    the build job's gate (A7/A10). A rival can win the identification ("no karaoke
    version yet") but never has versions.

Tables: ``songs`` (song-id columns + ``song_key``), ``songs_fts``, ``artists`` (+
``artists_fts``; display name, popularity and ``songs`` copied from the cloud index
so the artist-first path orders artists as in the cloud; ``songs`` is the artist's
cloud song count, informational, not read by the matcher), ``vocab``, ``meta`` (song-id keys +
``offline_schema_version``, ``rivals``, ``versions``, ``availability_run``), plus
availability's ``versions``, ``libraries`` and ``song_libraries`` for the karaoke
songs. ``songs.song_key`` is ``text_normalize.song_key(artist, title)`` of the
song-id display spelling, the key availability stores versions under (ruling R9).

Vocal range (Phase 3c), additive columns on ``songs`` (no schema bump: the app requires an
exact ``offline_schema_version`` and ignores extra columns): ``vr_low``, ``vr_high``,
``vr_p10``, ``vr_p50``, ``vr_p90``, ``vr_octave_conf`` (REAL) and ``vr_tier`` (INTEGER,
``vocal_range.tsv.TIER_CODES``: measured 4, notes 3, estimated 2, rough 1). Filled from the
song's own range, else its artist's (``identity_normalize(artist)``) as tier rough, else NULL.
``meta.vocal_range_run`` names the vocal-range run used ("" = none).

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
from karaoke_decide.song_search.identify import base_title, song_norm
from karaoke_decide.song_search.text_normalize import identity_normalize, song_key
from karaoke_decide.vocal_range.tsv import TIER_CODES, VocalRanges

OFFLINE_SCHEMA_VERSION = 1
OFFLINE_MARGIN_BONUS = 0.03

RIVALS_PER_TITLE = 10
# The matcher scores a blank popularity as 30; rivals are ranked the same way.
BLANK_POP = 30

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


def _uri(path: Path) -> str:
    return Path(path).resolve().as_uri()


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{_uri(path)}?mode=ro", uri=True)


def rival_key(title: str) -> str:
    """Base-title key rivals are matched on: ``identify.base_title`` (the matcher's
    per-artist dedupe title) — or the whole space-less title when it is all suffix —
    with one trailing plural "s" folded ("Lanterns" == "Lantern")."""
    key = base_title(title) or song_norm(title).replace(" ", "")
    return key[:-1] if len(key) > 1 and key.endswith("s") else key


def select_rivals(
    song_id_db: Path,
    karaoke_title_keys: set[str],
    *,
    per_title_cap: int = RIVALS_PER_TITLE,
) -> list[SongRow]:
    """Non-karaoke songs whose ``rival_key(title)`` is in ``karaoke_title_keys``: the
    ``per_title_cap`` most popular per key by ``COALESCE(pop, 30)``, then rowid.
    → ``[(artist, title, pop, 0)]`` ordered by key then rank."""
    if not karaoke_title_keys:
        return []
    conn = _ro(Path(song_id_db))
    try:
        conn.create_function("rival_key", 1, rival_key, deterministic=True)
        conn.execute("CREATE TEMP TABLE titles(k TEXT PRIMARY KEY) WITHOUT ROWID")
        conn.executemany("INSERT OR IGNORE INTO temp.titles VALUES (?)", ((t,) for t in karaoke_title_keys))
        rows = conn.execute(
            """
            SELECT artist, title, pop FROM (
                SELECT s.artist, s.title, s.pop, s.k,
                       ROW_NUMBER() OVER (PARTITION BY s.k ORDER BY COALESCE(s.pop, ?) DESC, s.rowid) AS n
                FROM (SELECT rowid, artist, title, pop, rival_key(title) AS k FROM songs WHERE karaoke = 0) s
                JOIN temp.titles t ON t.k = s.k
            ) WHERE n <= ? ORDER BY k, n
            """,
            (BLANK_POP, per_title_cap),
        ).fetchall()
    finally:
        conn.close()
    return [(a, t, p, 0) for a, t, p in rows]


def _karaoke_songs(song_id_db: Path) -> tuple[list[SongRow], set[str]]:
    """(karaoke song rows, their rival keys)."""
    conn = _ro(song_id_db)
    try:
        rows = conn.execute("SELECT artist, title, pop FROM songs WHERE karaoke = 1 ORDER BY rowid").fetchall()
    finally:
        conn.close()
    return [(a, t, p, 1) for a, t, p in rows], {rival_key(t) for _a, t, _p in rows}


def _write_tsv(path: Path, rows: list[SongRow]) -> None:
    """The song-id TSV format ``build_song_id`` reads (blank popularity = None)."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        for artist, title, pop, karaoke in rows:
            w.writerow([artist, title, "" if pop is None else pop, karaoke])


def build_offline_db(
    song_id_db: Path, availability_db: Path, out_path: Path, run: str, vocal_range: VocalRanges | None = None
) -> dict[str, Any]:
    """Build ``out_path`` (atomic replace) from ``song_id.db`` + ``availability.db``
    (+ ``vocal_range``: NULL ``vr_*`` without it).

    → ``{songs, rivals, versions, bytes}``: karaoke songs, rival songs, version rows,
    file size."""
    song_id_db, availability_db, out_path = Path(song_id_db), Path(availability_db), Path(out_path)
    karaoke, title_keys = _karaoke_songs(song_id_db)
    rivals = select_rivals(song_id_db, title_keys)

    dst = str(out_path)
    tsv, tmp = Path(dst + ".songs.tsv"), dst + ".build"
    try:
        _write_tsv(tsv, karaoke + rivals)
        # The song-id builder over the filtered rows: same normalisation, FTS, vocab.
        built = build_song_id([tsv], Path(tmp), run)
        if built["songs"] != len(karaoke) + len(rivals):  # song-id rows are already merged: nothing re-merges
            raise RuntimeError(f"offline build merged songs: {built['songs']} != {len(karaoke) + len(rivals)}")
        n_versions = _add_offline_tables(
            Path(tmp), song_id_db, availability_db, n_rivals=len(rivals), vocal_range=vocal_range or VocalRanges()
        )
        os.replace(tmp, dst)
    finally:
        for stale in (tsv, Path(tmp)):
            if stale.exists():
                stale.unlink()
    return {"songs": len(karaoke), "rivals": len(rivals), "versions": n_versions, "bytes": out_path.stat().st_size}


_VR_COLUMNS = (
    ("vr_low", "REAL"),
    ("vr_high", "REAL"),
    ("vr_p10", "REAL"),
    ("vr_p50", "REAL"),
    ("vr_p90", "REAL"),
    ("vr_tier", "INTEGER"),
    ("vr_octave_conf", "REAL"),
)


def _song_rows(conn: sqlite3.Connection, vocal_range: VocalRanges) -> list[tuple[Any, ...]]:
    """(song_key, *vr_*, rowid) per song row."""
    out = []
    for rid, artist, title in conn.execute("SELECT rowid, artist, title FROM songs").fetchall():
        key = song_key(artist, title)
        r = vocal_range.lookup(key, identity_normalize(artist))
        vr = (
            (None,) * len(_VR_COLUMNS)
            if r is None
            else (r.low, r.high, r.p10, r.p50, r.p90, TIER_CODES[r.tier], r.octave_confidence)
        )
        out.append((key, *vr, rid))
    return out


def _add_offline_tables(
    db_path: Path, song_id_db: Path, availability_db: Path, *, n_rivals: int, vocal_range: VocalRanges
) -> int:
    """song_key + vr_* columns, cloud artist popularity, versions/libraries; → version rows."""
    conn = sqlite3.connect(_uri(db_path), uri=True)  # URI mode: the ATTACHes below are read-only
    try:
        conn.execute("ALTER TABLE songs ADD COLUMN song_key TEXT")
        for name, kind in _VR_COLUMNS:
            conn.execute(f"ALTER TABLE songs ADD COLUMN {name} {kind}")
        sets = ", ".join(f"{name} = ?" for name, _ in (("song_key", ""), *_VR_COLUMNS))
        conn.executemany(f"UPDATE songs SET {sets} WHERE rowid = ?", _song_rows(conn, vocal_range))
        conn.execute("CREATE INDEX songs_song_key ON songs(song_key)")
        conn.executescript(_VERSIONS_SQL)
        conn.execute("ATTACH DATABASE ? AS cloud", (f"{_uri(song_id_db)}?mode=ro",))
        conn.execute("ATTACH DATABASE ? AS av", (f"{_uri(availability_db)}?mode=ro",))
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
                ("vocal_range_run", vocal_range.run),
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
