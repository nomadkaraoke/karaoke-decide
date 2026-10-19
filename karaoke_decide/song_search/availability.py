# Vendored from kjbox kj-controller/{karaoke_nerds.py,catalog_mirror.py,routes.py} @ 8973e4b; keep in step (parity tests in tests/unit/song_search/).
"""Availability index: which karaoke versions exist for a song (``availability.db``).

Merges our KaraokeNerds exports into one row per song (``song_key``):

  * community (``{"Items": [{Artist, Title, Brand, Watch}]}``): free web versions,
    each with a YouTube URL;
  * full (bare list ``[{Artist, Title, Brands}]``, Brands = comma-separated codes):
    every release KN knows about. Codes the song already has as a community version
    are skipped; the rest merge in as commercial versions with no URL (disc/library
    only) — kjbox ``karaoke_nerds._group_results`` + ``_merge_full_catalog``.

Search follows kjbox ``catalog_mirror._search_source``: FTS5 MATCH → (1-2 char
queries) substring LIKE → trigram candidates (``ORDER BY rank``, capped at 250)
through the ``fuzzy_match.score`` full-coverage gate. Results are already one
group per song and are ordered by kjbox ``routes._group_relevance``. Versions
are sorted as in ``routes._group_search_results``: playable first, then
``rank_version``. Here "playable" means "has a YouTube URL" (no local files or
Divebar mirror in decide).

Decide-side changes from kjbox: each KN row resolves to its ``song_id.db`` song via
``build_song_id.merge_key`` (the key song_id merges spellings on, file-quality tags
dropped) and is stored under ``song_key`` of song_id's display spelling, with its
popularity; unresolved rows use ``song_key`` of their own spelling. Every merged
spelling is indexed. The ``libraries`` / ``song_libraries`` tables are seeded from
the brand registry (spec §5.4).

Schema:
  songs(song_key PK, artist, title, pop, version_count)
  versions(song_key, brand_code, brand_name, kind community|commercial, youtube_url, rank)
  songs_fts      FTS5 over ``index_text(artist, title)`` (unicode61 remove_diacritics 2)
  songs_trigram  FTS5 trigram over ``normalize(artist + " " + title)``
  libraries(code PK, name, kind, source) / song_libraries(song_key, library_code)
  meta(key, value)
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import re
import sqlite3
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from karaoke_decide.song_search import fuzzy_match
from karaoke_decide.song_search.brand_names import brand_name
from karaoke_decide.song_search.build_song_id import merge_key
from karaoke_decide.song_search.text_normalize import (
    NORMALIZER_VERSION,
    SEARCH_INDEX_VERSION,
    fts_match_query,
    group_key,
    index_text,
    normalize,
    song_key,
)
from karaoke_decide.song_search.version_priority import (
    COMMERCIAL_BRANDS,
    COMMUNITY_BRANDS,
    rank_version,
    resolve_brand,
)

logger = logging.getLogger(__name__)

AVAILABILITY_SCHEMA_VERSION = 1

# Groups returned per search/popular call. SQLite treats LIMIT -1 as unlimited, so
# every caller-supplied limit is clamped into [1, MAX_LIMIT] (A77).
MAX_LIMIT = 60

# Rows pulled from the FTS / trigram index before relevance ordering. bm25-ranked,
# so the true match sits near the top; each fuzzy candidate costs a rapidfuzz score
# (kjbox catalog_mirror._FUZZY_CANDIDATE_LIMIT).
CANDIDATE_LIMIT = 250

# Version-count weight cap in group relevance (one brand-farm song can't dominate).
_VERSION_COUNT_CAP = 12

LIBRARY_SOURCE_KN_BRAND = "kn_brand"

# Extract a YouTube video id from KaraokeNerds' stored watch-URL forms
# (youtu.be/<id>, /watch?v=<id>, /embed/<id>, /shorts/<id>) — kjbox karaoke_nerds._YT_ID_RE.
_YT_ID_RE = re.compile(r"(?:youtu\.be/|youtube\.com/(?:watch\?(?:[^&]*&)*v=|embed/|shorts/|v/))([A-Za-z0-9_-]{11})")

_SCHEMA = """
    PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE songs(
        song_key TEXT PRIMARY KEY,
        artist TEXT NOT NULL,
        title TEXT NOT NULL,
        pop INTEGER,
        version_count INTEGER NOT NULL
    );
    CREATE TABLE versions(
        song_key TEXT NOT NULL,
        brand_code TEXT NOT NULL,
        brand_name TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('community', 'commercial')),
        youtube_url TEXT NULL,
        rank INT NOT NULL
    );
    CREATE VIRTUAL TABLE songs_fts USING fts5(text, tokenize='unicode61 remove_diacritics 2');
    CREATE VIRTUAL TABLE songs_trigram USING fts5(norm_text, tokenize='trigram');
    CREATE TABLE libraries(code TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, source TEXT NOT NULL);
    CREATE TABLE song_libraries(song_key TEXT NOT NULL, library_code TEXT NOT NULL,
                                PRIMARY KEY (song_key, library_code)) WITHOUT ROWID;
"""


# ============================================================== build side


def _normalize_youtube_url(url: str | None) -> str:
    """Canonical youtube.com/watch?v=<id>; the stripped original if no id parses; "" if empty.

    kjbox karaoke_nerds._normalize_youtube_url.
    """
    url = (url or "").strip()
    if not url:
        return ""
    m = _YT_ID_RE.search(url)
    if m:
        return f"https://www.youtube.com/watch?v={m.group(1)}"
    return url


def _iter_kn_export(path: Path) -> Iterator[dict[str, Any]]:
    """Rows of a gzipped KN export: community is ``{"Items": [...]}``, full a bare
    list (kjbox catalog_mirror._iter_kn_export accepts either shape)."""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get("Items") or []
    for item in data:
        if isinstance(item, dict):
            yield item


# A song_id.db song: (display artist, display title, popularity).
_Canonical = tuple[str, str, int | None]


class _Song:
    __slots__ = ("artist", "title", "pop", "canonical", "spellings", "tracks", "seen", "codes")

    def __init__(self, artist: str, title: str) -> None:
        self.artist = artist
        self.title = title
        self.pop: int | None = None
        self.canonical = False  # display spelling + pop come from song_id.db
        self.spellings: dict[tuple[str, str], None] = {}  # every spelling merged in (ordered set)
        self.tracks: list[dict[str, Any]] = []
        self.seen: set[tuple[str, str | None]] = set()  # community (brand, url) dedupe
        self.codes: set[str] = set()  # upper-cased brand codes present


def _merge(
    community_rows: Iterator[dict[str, Any]],
    full_rows: Iterator[dict[str, Any]],
    song_ids: dict[str, _Canonical],
) -> dict[str, _Song]:
    """kjbox ``_group_results`` (community, deduped by (brand, url)) then
    ``_merge_full_catalog`` (full codes not already on the song → commercial, no URL).

    Songs are keyed by ``song_key`` of the song_id.db song a KN row resolves to
    through ``build_song_id.merge_key`` (so "Get Ur Freak On" and "Get Your Freak
    On" are one song, under song_id's display spelling — the key A4/A5 join on).
    Unresolved rows fall back to ``song_key`` of their own spelling (first seen shown).
    """
    songs: dict[str, _Song] = {}

    def get(artist: str, title: str) -> _Song:
        canon = song_ids.get(merge_key(artist, title))
        if canon is not None:
            key = song_key(canon[0], canon[1])
        else:
            key = song_key(artist, title)
        s = songs.get(key)
        if s is None:
            s = songs[key] = _Song(artist, title)
        if canon is not None and not s.canonical:
            s.artist, s.title, s.pop = canon
            s.canonical = True
            s.spellings[(canon[0], canon[1])] = None
        s.spellings[(artist, title)] = None
        return s

    for r in community_rows:
        artist = (r.get("Artist") or "").strip()
        title = (r.get("Title") or "").strip()
        if not title:
            continue
        code = (r.get("Brand") or "").strip()
        url = _normalize_youtube_url(r.get("Watch")) or None
        song = get(artist, title)
        if (code, url) in song.seen:
            continue
        song.seen.add((code, url))
        song.codes.add(code.upper())
        song.tracks.append({"brand_code": code, "youtube_url": url, "is_community": True})

    for r in full_rows:
        artist = (r.get("Artist") or "").strip()
        title = (r.get("Title") or "").strip()
        if not title:
            continue
        codes = [c.strip() for c in (r.get("Brands") or "").split(",")]
        codes = [c for c in codes if c]
        if not codes:
            continue
        song = get(artist, title)
        for code in codes:
            if code.upper() in song.codes:
                continue
            song.codes.add(code.upper())
            song.tracks.append({"brand_code": code, "youtube_url": None, "is_community": False})

    return songs


def _index_texts(song: _Song) -> tuple[str, str]:
    """(FTS text, trigram text) over every spelling merged into ``song``, so featured
    names and alternate spellings stay searchable."""
    fts: dict[str, None] = {}
    tri: dict[str, None] = {}
    for artist, title in song.spellings:
        fts[index_text(artist, title)] = None
        tri[normalize(f"{artist} {title}")] = None
    return " ".join(t for t in fts if t), " ".join(t for t in tri if t)


def _sorted_versions(tracks: list[dict[str, Any]]) -> list[tuple[str, str, str, str | None, int]]:
    """(brand_code, brand_name, kind, youtube_url, rank), playable first then rank
    (kjbox routes._group_search_results), brand code as a deterministic tie-break."""
    out = []
    for t in tracks:
        rank = rank_version(t)
        kind = "community" if t["is_community"] else "commercial"
        out.append((t["brand_code"], brand_name(t["brand_code"]), kind, t["youtube_url"], rank))
    out.sort(key=lambda v: (v[3] is None, v[4], v[0], v[3] or ""))
    return out


def _load_song_ids(song_id_db: Path | None) -> dict[str, _Canonical]:
    """merge_key → (display artist, display title, pop) for every song_id.db song."""
    if song_id_db is None:
        return {}
    out: dict[str, _Canonical] = {}
    conn = sqlite3.connect(f"file:{song_id_db}?mode=ro", uri=True)
    try:
        for artist, title, pop in conn.execute("SELECT artist, title, pop FROM songs ORDER BY rowid"):
            key = merge_key(artist, title)
            cur = out.get(key)
            # build() merges on merge_key, so keys are unique; keep the more popular if not.
            if cur is None or (pop is not None and (cur[2] is None or pop > cur[2])):
                out[key] = (artist, title, pop)
    finally:
        conn.close()
    return out


def _library_rows() -> list[tuple[str, str, str, str]]:
    rows = [(c, brand_name(c), "community", LIBRARY_SOURCE_KN_BRAND) for c, _, _ in COMMUNITY_BRANDS]
    rows += [(c, brand_name(c), "commercial", LIBRARY_SOURCE_KN_BRAND) for c, _, _ in COMMERCIAL_BRANDS]
    return rows


def build_availability_db(
    full_json_gz: Path,
    community_json_gz: Path,
    song_id_db: Path | None,
    out_path: Path,
    run: str,
) -> dict[str, Any]:
    """Build ``out_path`` from the KN full + community exports; atomic replace.

    → ``{songs, versions, song_libraries, built_at, run}`` (counts also in ``meta``)."""
    songs = _merge(
        _iter_kn_export(Path(community_json_gz)), _iter_kn_export(Path(full_json_gz)), _load_song_ids(song_id_db)
    )
    library_codes = {c for c, _, _, _ in _library_rows()}

    dst = str(out_path)
    tmp = dst + ".new"
    for stale in (tmp, tmp + "-journal"):
        if os.path.exists(stale):
            os.remove(stale)
    db = sqlite3.connect(tmp)
    try:
        db.executescript(_SCHEMA)
        n_versions = 0
        n_links = 0
        song_rows: list[tuple[int, str, str, str, int | None, int]] = []
        version_rows: list[tuple[str, str, str, str, str | None, int]] = []
        fts_rows: list[tuple[int, str]] = []
        tri_rows: list[tuple[int, str]] = []
        link_rows: list[tuple[str, str]] = []
        for rowid, (key, s) in enumerate(sorted(songs.items()), start=1):
            versions = _sorted_versions(s.tracks)
            song_rows.append((rowid, key, s.artist, s.title, s.pop, len(versions)))
            version_rows.extend((key, *v) for v in versions)
            fts_text, tri_text = _index_texts(s)
            fts_rows.append((rowid, fts_text))
            tri_rows.append((rowid, tri_text))
            # A brand links to a library only when it IS that library's catalogue
            # (registry brands); unknown brands stay just brands (spec §5.4).
            libs = {c for c in (resolve_brand(brand_code=v[0])[0] for v in versions) if c in library_codes}
            link_rows.extend((key, code) for code in sorted(libs))
            n_versions += len(versions)
            n_links += len(libs)
        db.executemany(
            "INSERT INTO songs(rowid, song_key, artist, title, pop, version_count) VALUES (?,?,?,?,?,?)", song_rows
        )
        db.executemany("INSERT INTO versions VALUES (?,?,?,?,?,?)", version_rows)
        db.executemany("INSERT INTO songs_fts(rowid, text) VALUES (?,?)", fts_rows)
        db.executemany("INSERT INTO songs_trigram(rowid, norm_text) VALUES (?,?)", tri_rows)
        db.executemany("INSERT INTO libraries VALUES (?,?,?,?)", _library_rows())
        db.executemany("INSERT INTO song_libraries VALUES (?,?)", link_rows)
        db.execute("CREATE INDEX versions_song ON versions(song_key)")
        db.execute("CREATE INDEX songs_pop ON songs(pop DESC)")
        db.execute("INSERT INTO songs_fts(songs_fts) VALUES ('optimize')")
        db.execute("INSERT INTO songs_trigram(songs_trigram) VALUES ('optimize')")
        built_at = int(time.time())
        db.executemany(
            "INSERT INTO meta VALUES (?,?)",
            [
                ("schema_version", str(AVAILABILITY_SCHEMA_VERSION)),
                ("normalizer_version", str(NORMALIZER_VERSION)),
                ("search_index_version", SEARCH_INDEX_VERSION),
                ("songs", str(len(song_rows))),
                ("versions", str(n_versions)),
                ("song_libraries", str(n_links)),
                ("built_at", str(built_at)),
                ("run", run),
            ],
        )
        db.commit()
        db.execute("VACUUM")
    except BaseException:
        db.close()
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    db.close()
    os.replace(tmp, dst)
    return {
        "songs": len(song_rows),
        "versions": n_versions,
        "song_libraries": n_links,
        "built_at": built_at,
        "run": run,
    }


# ============================================================== query side


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), MAX_LIMIT))


def _group_relevance(title: str, playable: bool, version_count: int, query_key: str) -> tuple[int, int, int]:
    """Higher is better: title equals the query (2) > title prefix either way (1) > other;
    then playable now; then version count capped at 12 (the "well-known recording"
    signal). kjbox routes._group_relevance, with in_library → has a YouTube URL."""
    title_key = group_key("", title)
    if query_key and title_key == query_key:
        title_score = 2
    elif query_key and (title_key.startswith(query_key) or query_key.startswith(title_key)):
        title_score = 1
    else:
        title_score = 0
    return (title_score, 1 if playable else 0, min(version_count, _VERSION_COUNT_CAP))


class AvailabilityIndex:
    """Read-only queries over ``availability.db``. Thread-safe (one connection per thread)."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._local = threading.local()
        self._conn()  # fail fast on a missing / unreadable file

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("SELECT 1 FROM meta LIMIT 1")
            self._local.conn = conn
        return conn

    # ---------------------------------------------------------------- public

    def search(self, query: str, limit: int = MAX_LIMIT) -> list[dict[str, Any]]:
        """Groups matching ``query``, most relevant first. ``[]`` on no match or any
        index trouble (search must never get worse than empty — A76)."""
        limit = _clamp(limit)
        try:
            rows = self._candidates(query or "")
            if not rows:
                return []
            playable = self._playable({r["song_key"] for r in rows})
            query_key = group_key("", query)
            # Stable sort: equal relevance keeps index (bm25 / fuzzy) order.
            rows.sort(
                key=lambda r: _group_relevance(r["title"], r["song_key"] in playable, r["version_count"], query_key),
                reverse=True,
            )
            return self._groups(rows[:limit])
        except sqlite3.Error:
            logger.warning("availability search failed for %r", query, exc_info=True)
            return []

    def song(self, key: str) -> dict[str, Any] | None:
        """One song with all its versions (best first), or None if unknown."""
        row = self._conn().execute("SELECT * FROM songs WHERE song_key = ?", (key,)).fetchone()
        return self._groups([row])[0] if row else None

    def popular(self, limit: int = 30) -> list[dict[str, Any]]:
        """Most popular songs with any version (popularity from song_id.db)."""
        rows = (
            self._conn()
            .execute(
                "SELECT * FROM songs WHERE pop IS NOT NULL ORDER BY pop DESC, version_count DESC, song_key LIMIT ?",
                (_clamp(limit),),
            )
            .fetchall()
        )
        return self._groups(rows)

    # --------------------------------------------------------------- ladder

    def _candidates(self, query: str) -> list[sqlite3.Row]:
        """kjbox catalog_mirror._search_source: FTS5 MATCH → short-query LIKE →
        trigram + fuzzy_match gate."""
        normalized = normalize(query)
        fts_query = fts_match_query(normalized)
        if not fts_query:
            return []
        # sqlite errors propagate to search(), which logs and returns [].
        rows = (
            self._conn()
            .execute(
                "SELECT s.* FROM songs_fts f JOIN songs s ON s.rowid = f.rowid "
                "WHERE songs_fts MATCH ? ORDER BY rank LIMIT ?",
                (fts_query, CANDIDATE_LIMIT),
            )
            .fetchall()
        )
        if rows:
            return list(rows)
        if len(normalized) < 3:
            return self._short_query_like(normalized)
        return self._fuzzy(normalized)

    def _short_query_like(self, normalized: str) -> list[sqlite3.Row]:
        """Substring fallback for sub-trigram (1-2 char) queries only."""
        return list(
            self._conn()
            .execute(
                "SELECT s.* FROM songs_trigram t JOIN songs s ON s.rowid = t.rowid WHERE t.norm_text LIKE ? LIMIT ?",
                (f"%{normalized}%", CANDIDATE_LIMIT),
            )
            .fetchall()
        )

    def _fuzzy(self, norm_q: str) -> list[sqlite3.Row]:
        """Typo-tolerant fallback: bm25-ranked trigram candidates re-ranked by
        ``fuzzy_match.score`` (every significant query word must be covered)."""
        trigrams = {norm_q[i : i + 3] for i in range(len(norm_q) - 2)}
        match_expr = " OR ".join('"' + t.replace('"', '""') + '"' for t in sorted(trigrams))
        candidates = (
            self._conn()
            .execute(
                "SELECT s.*, t.norm_text AS norm_text FROM songs_trigram t JOIN songs s ON s.rowid = t.rowid "
                "WHERE songs_trigram MATCH ? ORDER BY rank LIMIT ?",
                (match_expr, CANDIDATE_LIMIT),
            )
            .fetchall()
        )
        q_sig = fuzzy_match.significant_tokens(norm_q)
        scored = []
        for c in candidates:
            res = fuzzy_match.score(norm_q, c["norm_text"], q_sig=q_sig)
            if res is not None:
                scored.append((res, c))
        scored.sort(key=lambda s: s[0], reverse=True)
        return [c for _, c in scored]

    # -------------------------------------------------------------- shaping

    def _playable(self, keys: set[str]) -> set[str]:
        ks = sorted(keys)
        marks = ",".join("?" * len(ks))
        rows = self._conn().execute(
            f"SELECT DISTINCT song_key FROM versions WHERE song_key IN ({marks}) AND youtube_url IS NOT NULL", ks
        )
        return {r[0] for r in rows}

    def _groups(self, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        """Song rows → group dicts, each with its versions best-first."""
        if not rows:
            return []
        keys = [r["song_key"] for r in rows]
        marks = ",".join("?" * len(keys))
        by_key: dict[str, list[dict[str, Any]]] = {k: [] for k in keys}
        for v in self._conn().execute(
            "SELECT song_key, brand_code, brand_name, kind, youtube_url, rank FROM versions "
            f"WHERE song_key IN ({marks}) ORDER BY song_key, youtube_url IS NULL, rank, rowid",
            keys,
        ):
            by_key[v["song_key"]].append(
                {
                    "brand_code": v["brand_code"],
                    "brand_name": v["brand_name"],
                    "kind": v["kind"],
                    "youtube_url": v["youtube_url"],
                    "rank": v["rank"],
                }
            )
        return [
            {
                "song_key": r["song_key"],
                "artist": r["artist"],
                "title": r["title"],
                "pop": r["pop"],
                "version_count": r["version_count"],
                "versions": by_key[r["song_key"]],
            }
            for r in rows
        ]
