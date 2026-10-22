"""library_clause / group_matches_library, and the AvailabilityIndex library parameter."""

import sqlite3

from karaoke_decide.song_search.availability import AvailabilityIndex
from karaoke_decide.song_search.library_filter import LIBRARY_CODES, group_matches_library, library_clause

SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE songs(song_key TEXT PRIMARY KEY, artist TEXT, title TEXT, pop INTEGER, version_count INTEGER);
CREATE TABLE versions(song_key TEXT, brand_code TEXT, brand_name TEXT, kind TEXT, youtube_url TEXT, rank INT);
CREATE TABLE song_libraries(song_key TEXT, library_code TEXT);
"""


def _index(tmp_path):
    path = tmp_path / "availability.db"
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    songs = [
        ("a", "A", "Alpha", 90, 1),
        ("b", "B", "Beta", 80, 1),
        ("c", "C", "Gamma", 70, 2),
        ("d", "D", "Delta", 60, 0),
    ]
    db.executemany("INSERT INTO songs VALUES (?,?,?,?,?)", songs)
    db.executemany(
        "INSERT INTO versions VALUES (?,?,?,?,?,?)",
        [
            ("a", "KV", "Karaoke Version", "commercial", None, 1),
            ("b", "CC", "Karaoke Community", "community", "https://youtu.be/x", 1),
            ("c", "KV", "Karaoke Version", "commercial", None, 1),
            ("c", "NOMAD", "Nomad", "community", None, 2),
        ],
    )
    db.executemany("INSERT INTO song_libraries VALUES (?,?)", [("a", "KV"), ("b", "CC"), ("c", "KV"), ("c", "NOMAD")])
    db.commit()
    db.close()
    return AvailabilityIndex(path)


def test_codes():
    assert LIBRARY_CODES == {"karafun": "KV"}


def test_clause_any_is_noop():
    assert library_clause("any") == ("1", [])


def test_clause_params():
    assert library_clause("karafun")[1] == ["KV"]
    assert library_clause("community")[1] == []
    assert "s.song_key" in library_clause("community", "s.song_key")[0]


def test_popular_filters(tmp_path):
    idx = _index(tmp_path)
    keys = lambda lib: [g["song_key"] for g in idx.popular(10, lib)]  # noqa: E731
    assert keys("any") == ["a", "b", "c", "d"]
    assert keys("karafun") == ["a", "c"]
    assert keys("community") == ["b", "c"]


def test_group_matches_library():
    g = {"versions": [{"kind": "commercial", "brand_code": "KV"}]}
    assert group_matches_library(g, "any") and group_matches_library(g, "karafun")
    assert not group_matches_library(g, "community")
