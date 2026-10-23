"""Build catalog.db from in-memory rows (the BigQuery extraction lives elsewhere)."""

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from karaoke_decide.catalog.schema import CATALOG_SCHEMA_VERSION, DDL
from karaoke_decide.vocal_range.tsv import VocalRanges

COVERAGE_FACETS = ("genre", "mood", "decade", "lead_gender", "duration", "popularity", "explicit", "vocal_range")


@dataclass(frozen=True)
class SongRow:
    song_key: str
    artist: str
    title: str
    artist_key: str = ""
    brand_count: int = 0
    is_classic: int = 0
    explicit: int | None = None
    duration_ms: int | None = None
    year: int | None = None
    decade: str | None = None
    lb_listeners: int | None = None
    spotify_pop: int | None = None
    popularity: int | None = None  # overwritten by build_catalog_db with the blend
    energy: float | None = None
    valence: float | None = None
    danceability: float | None = None
    tempo: float | None = None
    mood: str | None = None
    lead_gender: str | None = None
    genres: tuple[str, ...] = ()
    in_kn: int = 1
    vr_low: float | None = None
    vr_high: float | None = None
    vr_p10: float | None = None
    vr_p50: float | None = None
    vr_p90: float | None = None
    vr_tier: str | None = None
    vr_octave_conf: float | None = None
    vr_duet: int | None = None


@dataclass(frozen=True)
class ArtistRow:
    artist_key: str
    name: str
    mbid: str | None = None
    lb_listeners: int | None = None
    popularity: int | None = None
    gender: str | None = None
    genres: tuple[str, ...] = ()
    vr_p10: float | None = None
    vr_p50: float | None = None
    vr_p90: float | None = None
    vr_basis: str | None = None


@dataclass(frozen=True)
class SimilarRow:
    artist_key: str
    similar_key: str
    score: float
    source: str


@dataclass(frozen=True)
class KnRow:
    kn_id: int
    song_key: str
    artist: str
    title: str
    brands: str
    brand_count: int
    match_key: str


@dataclass(frozen=True)
class CatalogRows:
    songs: Sequence[SongRow]
    artists: Sequence[ArtistRow] = ()
    similar: Sequence[SimilarRow] = ()
    kn: Sequence[KnRow] = ()
    genre_map: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CatalogStats:
    songs: int
    artists: int
    similar: int
    kn: int
    coverage: dict[str, float]
    bytes: int


def popularity_blend(lb_pct: float | None, spotify_pop: int | None) -> int | None:
    """0-100 popularity from the LB listener percentile (0-1) and Spotify popularity (0-100)."""
    if lb_pct is not None and spotify_pop is not None:
        return round(100 * (0.6 * lb_pct + 0.4 * spotify_pop / 100))
    if lb_pct is not None:
        return round(100 * lb_pct)
    if spotify_pop is not None:
        return round(spotify_pop)
    return None


def with_vocal_range(rows: CatalogRows, ranges: VocalRanges) -> CatalogRows:
    """``rows`` with ``vr_*`` filled: the song's own range, else its artist's range (tier
    ``rough``, octave confidence and duet unknown), else left NULL."""
    songs = []
    for s in rows.songs:
        r = ranges.lookup(s.song_key, s.artist_key)
        if r is None:
            songs.append(s)
            continue
        songs.append(
            replace(
                s,
                vr_low=r.low,
                vr_high=r.high,
                vr_p10=r.p10,
                vr_p50=r.p50,
                vr_p90=r.p90,
                vr_tier=r.tier,
                vr_octave_conf=r.octave_confidence,
                vr_duet=None if r.is_duet is None else int(r.is_duet),
            )  # fmt: skip
        )
    artists = []
    for a in rows.artists:
        ar = ranges.artists.get(a.artist_key)
        artists.append(a if ar is None else replace(a, vr_p10=ar.p10, vr_p50=ar.p50, vr_p90=ar.p90, vr_basis=ar.basis))
    return replace(rows, songs=songs, artists=artists)


def _dedupe_songs(songs: Sequence[SongRow]) -> list[SongRow]:
    """One row per song_key; the more-listened row wins (first on ties)."""
    best: dict[str, SongRow] = {}
    for song in songs:
        current = best.get(song.song_key)
        if current is None or (song.lb_listeners or -1) > (current.lb_listeners or -1):
            best[song.song_key] = song
    return list(best.values())


def _lb_percentiles(songs: Sequence[SongRow]) -> dict[str, float]:
    """song_key -> average-rank percentile (0-1) of lb_listeners among songs that have one."""
    listened = sorted(s.lb_listeners for s in songs if s.lb_listeners is not None)
    n = len(listened)
    if n == 0:
        return {}
    first: dict[int, int] = {}
    count: dict[int, int] = {}
    for i, value in enumerate(listened):
        first.setdefault(value, i)
        count[value] = count.get(value, 0) + 1
    result: dict[str, float] = {}
    for s in songs:
        if s.lb_listeners is None:
            continue
        if n == 1:
            result[s.song_key] = 1.0
        else:
            rank = first[s.lb_listeners] + (count[s.lb_listeners] - 1) / 2
            result[s.song_key] = rank / (n - 1)
    return result


def _coverage(songs: Sequence[SongRow]) -> dict[str, float]:
    in_kn = [s for s in songs if s.in_kn]
    if not in_kn:
        return {name: 0.0 for name in COVERAGE_FACETS}
    checks = {
        "genre": lambda s: bool(s.genres),
        "mood": lambda s: s.mood is not None,
        "decade": lambda s: s.decade is not None,
        "lead_gender": lambda s: s.lead_gender is not None,
        "duration": lambda s: s.duration_ms is not None,
        "popularity": lambda s: s.popularity is not None,
        "explicit": lambda s: s.explicit is not None,
        "vocal_range": lambda s: s.vr_p50 is not None,  # informational: catalog_gate never blocks on it
    }
    return {name: sum(1 for s in in_kn if check(s)) / len(in_kn) for name, check in checks.items()}


def _has_popularity_signal(s: SongRow) -> bool:
    return s.lb_listeners is not None or s.spotify_pop is not None


def build_catalog_db(rows: CatalogRows, dest: Path, run: str) -> CatalogStats:
    """Write catalog.db to ``dest`` (replacing any existing file) and return its stats."""
    songs = _dedupe_songs(rows.songs)
    percentiles = _lb_percentiles(songs)
    songs = [
        SongRow(**{**s.__dict__, "popularity": popularity_blend(percentiles.get(s.song_key), s.spotify_pop)})
        for s in songs
    ]
    artists: dict[str, ArtistRow] = {}
    for a in rows.artists:
        current = artists.get(a.artist_key)
        if current is None or (a.lb_listeners or -1) > (current.lb_listeners or -1):
            artists[a.artist_key] = a
    # Coverage describes the whole KaraokeNerds catalogue; measured before pruning.
    coverage = _coverage(songs)
    # Size budget (<150 MB raw, <50 MB gz): ~55% of KN songs have neither a ListenBrainz nor a
    # Spotify popularity signal (and no mood/duration either). They can't rank in any section,
    # so the recommendation candidate table skips them; they stay in kn_song_map, so id/library
    # lookups still resolve.
    songs = [s for s in songs if _has_popularity_signal(s)]

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.unlink(missing_ok=True)
    conn = sqlite3.connect(dest)
    try:
        conn.executescript(DDL)
        conn.executemany(
            f"INSERT INTO songs VALUES ({','.join('?' * 29)})",
            [
                (
                    s.song_key, s.artist, s.title, s.artist_key, s.brand_count, s.is_classic, s.explicit,
                    s.duration_ms, s.year, s.decade, s.lb_listeners, s.spotify_pop, s.popularity,
                    s.energy, s.valence, s.danceability, s.tempo, s.mood, s.lead_gender,
                    json.dumps(list(s.genres)), s.in_kn,
                    s.vr_low, s.vr_high, s.vr_p10, s.vr_p50, s.vr_p90, s.vr_tier, s.vr_octave_conf, s.vr_duet,
                )
                for s in songs
            ],
        )  # fmt: skip
        conn.executemany(
            "INSERT OR IGNORE INTO song_genres VALUES (?,?)",
            [(s.song_key, g) for s in songs for g in s.genres],
        )
        conn.executemany(
            "INSERT INTO artists VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    a.artist_key,
                    a.name,
                    a.mbid,
                    a.lb_listeners,
                    a.popularity,
                    a.gender,
                    json.dumps(list(a.genres)),
                    a.vr_p10,
                    a.vr_p50,
                    a.vr_p90,
                    a.vr_basis,
                )
                for a in artists.values()
            ],  # fmt: skip
        )
        conn.executemany(
            "INSERT OR IGNORE INTO artist_similar VALUES (?,?,?,?)",
            [(r.artist_key, r.similar_key, r.score, r.source) for r in rows.similar],
        )
        conn.executemany("INSERT INTO genre_map VALUES (?,?)", sorted(rows.genre_map.items()))
        conn.executemany(
            "INSERT INTO kn_song_map VALUES (?,?,?,?,?,?,?)",
            [(k.kn_id, k.song_key, k.artist, k.title, k.brands, k.brand_count, k.match_key) for k in rows.kn],
        )
        conn.executemany(
            "INSERT INTO meta VALUES (?,?)",
            [
                ("schema", str(CATALOG_SCHEMA_VERSION)),
                ("run", run),
                ("built_at", datetime.now(UTC).isoformat()),
                ("coverage", json.dumps(coverage)),
            ],
        )
        conn.commit()
        n_similar = conn.execute("SELECT COUNT(*) FROM artist_similar").fetchone()[0]
        n_kn = conn.execute("SELECT COUNT(*) FROM kn_song_map").fetchone()[0]
        conn.execute("VACUUM")
    finally:
        conn.close()
    return CatalogStats(
        songs=len(songs),
        artists=len(artists),
        similar=n_similar,
        kn=n_kn,
        coverage=coverage,
        bytes=dest.stat().st_size,
    )
