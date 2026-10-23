import json
import sqlite3
from pathlib import Path

from karaoke_decide.catalog.build import build_catalog_db, popularity_blend, with_vocal_range
from karaoke_decide.vocal_range.tsv import ArtistRange, SongRange, VocalRanges


def _rows(db: Path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_build_roundtrip(tmp_path, make_rows):
    dest = tmp_path / "catalog.db"
    stats = build_catalog_db(make_rows(), dest, run="r1")
    assert len(_rows(dest, "SELECT * FROM songs")) == 3
    assert _rows(dest, "SELECT song_key, genre_id FROM song_genres ORDER BY song_key, genre_id") == [
        ("artist 1|song 1", "indie"),
        ("artist 1|song 1", "rock"),
        ("artist 2|song 2", "pop"),
    ]
    meta = dict(_rows(dest, "SELECT key, value FROM meta"))
    assert meta["schema"] == "2"
    assert meta["run"] == "r1"
    assert meta["built_at"]
    assert json.loads(meta["coverage"])["genre"] == stats.coverage["genre"]
    assert _rows(dest, "SELECT genres_json FROM songs WHERE song_key = 'artist 2|song 2'") == [('["pop"]',)]
    assert (stats.songs, stats.artists, stats.similar, stats.kn) == (3, 2, 1, 1)
    assert stats.bytes == dest.stat().st_size
    assert _rows(dest, "SELECT * FROM genre_map") == [("alt rock", "indie")]


def test_rebuild_replaces_existing_file(tmp_path, make_rows):
    dest = tmp_path / "catalog.db"
    build_catalog_db(make_rows(), dest, run="r1")
    build_catalog_db(make_rows(), dest, run="r2")
    assert _rows(dest, "SELECT value FROM meta WHERE key = 'run'") == [("r2",)]


def test_duplicate_song_key_keeps_more_listened(tmp_path, make_rows, song):
    low = song(1, lb_listeners=10, title="Low")
    high = song(1, lb_listeners=999, title="High")
    dest = tmp_path / "catalog.db"
    stats = build_catalog_db(make_rows(songs=[low, high]), dest, run="r")
    assert stats.songs == 1
    assert _rows(dest, "SELECT title FROM songs") == [("High",)]
    # order must not matter
    build_catalog_db(make_rows(songs=[high, low]), dest, run="r")
    assert _rows(dest, "SELECT title FROM songs") == [("High",)]


def test_popularity_blend():
    assert popularity_blend(1.0, 100) == 100
    assert popularity_blend(0.5, None) == 50
    assert popularity_blend(None, 80) == 80
    assert popularity_blend(None, None) is None
    assert popularity_blend(0.5, 50) == 50


def test_popularity_uses_lb_percentile(tmp_path, make_rows, song):
    songs = [song(1, lb_listeners=10, spotify_pop=None), song(2, lb_listeners=500, spotify_pop=None)]
    songs.append(song(3, lb_listeners=None, spotify_pop=80))
    dest = tmp_path / "catalog.db"
    build_catalog_db(make_rows(songs=songs), dest, run="r")
    pops = dict(_rows(dest, "SELECT song_key, popularity FROM songs"))
    assert pops == {"artist 1|song 1": 0, "artist 2|song 2": 100, "artist 3|song 3": 80}


def test_songs_without_any_popularity_signal_are_pruned_but_coverage_counts_them(tmp_path, make_rows, song):
    songs = [song(1, lb_listeners=10), song(2, spotify_pop=40), song(3, lb_listeners=None, spotify_pop=None, mood=None)]
    dest = tmp_path / "catalog.db"
    stats = build_catalog_db(make_rows(songs=songs), dest, run="r")
    assert stats.songs == 2
    assert {k for (k,) in _rows(dest, "SELECT song_key FROM songs")} == {"artist 1|song 1", "artist 2|song 2"}
    assert _rows(dest, "SELECT COUNT(*) FROM song_genres WHERE song_key = 'artist 3|song 3'") == [(0,)]
    assert stats.coverage["mood"] == 2 / 3  # measured over the whole KN catalogue, before pruning


def test_coverage(tmp_path, make_rows, song):
    songs = [song(1), song(2), song(3, mood=None), song(4, mood=None), song(5, in_kn=0)]
    stats = build_catalog_db(make_rows(songs=songs), tmp_path / "catalog.db", run="r")
    assert stats.coverage["mood"] == 0.5
    assert stats.coverage["genre"] == 1.0
    assert set(stats.coverage) == {
        "genre", "mood", "decade", "lead_gender", "duration", "popularity", "explicit", "vocal_range",
    }  # fmt: skip


def test_indexes_exist(tmp_path, make_rows):
    dest = tmp_path / "catalog.db"
    build_catalog_db(make_rows(), dest, run="r")
    names = {r[0] for r in _rows(dest, "SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {
        "idx_songs_artist_key",
        "idx_songs_popularity",
        "idx_songs_decade",
        "idx_songs_year",
        "idx_kn_song_map_match_key",
        "idx_kn_song_map_song_key",
    } <= names


def test_catalog_vr_columns_and_rough_fallback(tmp_path, make_rows):
    ranges = VocalRanges(
        songs={"artist 1|song 1": SongRange(50.0, 70.0, 53.0, 60.0, 66.0, "notes", 0.9, True)},
        artists={"artist 2": ArtistRange(45.0, 65.0, 48.0, 55.0, 61.0, "p412")},
        run="vr1",
    )
    dest = tmp_path / "catalog.db"
    stats = build_catalog_db(with_vocal_range(make_rows(), ranges), dest, run="r")
    cols = "vr_low, vr_high, vr_p10, vr_p50, vr_p90, vr_tier, vr_octave_conf, vr_duet"
    by_key = {r[0]: r[1:] for r in _rows(dest, f"SELECT song_key, {cols} FROM songs")}
    assert by_key["artist 1|song 1"] == (50.0, 70.0, 53.0, 60.0, 66.0, "notes", 0.9, 1)
    assert by_key["artist 2|song 2"] == (45.0, 65.0, 48.0, 55.0, 61.0, "rough", None, None)
    assert by_key["artist 3|song 3"] == (None,) * 8
    artists = {r[0]: r[1:] for r in _rows(dest, "SELECT artist_key, vr_p10, vr_p50, vr_p90, vr_basis FROM artists")}
    assert artists["artist 2"] == (48.0, 55.0, 61.0, "p412")
    assert artists["artist 1"] == (None, None, None, None)
    assert stats.coverage["vocal_range"] == 2 / 3


def test_catalog_without_vocal_range_has_null_columns(tmp_path, make_rows):
    dest = tmp_path / "catalog.db"
    stats = build_catalog_db(make_rows(), dest, run="r")
    assert _rows(dest, "SELECT count(*) FROM songs WHERE vr_p50 IS NOT NULL OR vr_tier IS NOT NULL") == [(0,)]
    assert stats.coverage["vocal_range"] == 0.0
