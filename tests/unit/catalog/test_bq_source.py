"""Tests for the catalog BigQuery source (fake client, no GCP credentials)."""

import ast
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from karaoke_decide.catalog import bq_source as src
from karaoke_decide.catalog.keys import song_key
from karaoke_decide.services.bq_limits import MAX_BYTES_CATALOG

SQL_CONSTANTS = {
    name: getattr(src, name)
    for name in ("KN_SQL", "SONG_FACETS_SQL", "NO_KARAOKE_SQL", "ARTISTS_SQL", "SIMILAR_SQL", "RAW_GENRES_SQL")
}

KN = [
    {"Id": 1, "Artist": "Alpha Band", "Title": "Sunny Day", "Brands": "CC,KV,SF", "brand_count": 3},
    {"Id": 2, "Artist": "Alpha Band", "Title": "Quiet Night", "Brands": "CC", "brand_count": 1},
    {"Id": 3, "Artist": "Alpha Band", "Title": "Old Favourite", "Brands": ",".join(["B"] * 25), "brand_count": 25},
]
FACETS = [
    {
        "karaoke_id": 1, "energy": 0.9, "valence": 0.8, "danceability": 0.7, "tempo": 120.0,
        "explicit": False, "duration_ms": 200_000, "spotify_pop": 60, "artist_spotify_id": "sp1",
        "year": 1994, "lb_listeners": 5000,
    },
]  # fmt: skip
NO_KARAOKE = [
    {"recording_mbid": "r1", "title": "Quiet Night", "artist": "Alpha Band", "listeners": 9000,
     "duration_ms": 1, "spotify_pop": 1, "year": 1990},
    {"recording_mbid": "r2", "title": "Hidden Hit", "artist": "Alpha Band", "listeners": 8000,
     "duration_ms": 210_000, "spotify_pop": 40, "year": 2003},
]  # fmt: skip
ARTISTS = [
    {"mbid": "m1", "name": "Alpha Band", "popularity": 70, "spotify_genres": ["classic rock"],
     "mb_tags": ["indie"], "lb_listeners": 20000, "gender": "mixed"},
    {"mbid": "m2", "name": "Beta Singer", "popularity": 50, "spotify_genres": [], "mb_tags": [],
     "lb_listeners": 10, "gender": "female"},
]  # fmt: skip
SIMILAR = [
    {"artist_a_name": "Alpha Band", "artist_b_name": "Beta Singer", "jaccard_similarity": 0.02},
    {"artist_a_name": "Alpha Band", "artist_b_name": "Gamma", "jaccard_similarity": 0.01},
    {"artist_a_name": "Unknown Act", "artist_b_name": "Alpha Band", "jaccard_similarity": 0.5},
]  # fmt: skip
RAW_GENRES = [{"genre": "classic rock"}, {"genre": "polka"}, {"genre": "k-pop"}]


def fake_bq():
    """A client that routes on SQL substrings and records every job_config."""
    bq = MagicMock()
    bq.configs = []

    def query(sql, job_config=None):
        bq.configs.append(job_config)
        if sql == src.KN_SQL:
            rows = KN
        elif sql == src.SONG_FACETS_SQL:
            rows = FACETS
        elif sql == src.NO_KARAOKE_SQL:
            rows = NO_KARAOKE
        elif sql == src.ARTISTS_SQL:
            rows = ARTISTS
        elif sql == src.SIMILAR_SQL:
            rows = SIMILAR
        elif sql == src.RAW_GENRES_SQL:
            rows = RAW_GENRES
        else:
            raise AssertionError("unexpected query")
        job = MagicMock()
        job.result.side_effect = lambda page_size=None: iter(rows)
        return job

    bq.query.side_effect = query
    return bq


@pytest.mark.parametrize("name", SQL_CONSTANTS)
def test_sql_never_maxes_audio_features(name):
    assert not re.search(r"MAX\(\s*(af\.)?(energy|valence|danceability|tempo)", SQL_CONSTANTS[name], re.IGNORECASE)


def test_facets_pick_one_track():
    assert "ROW_NUMBER() OVER (PARTITION BY" in src.SONG_FACETS_SQL


def test_every_query_has_byte_cap_and_streams_pages():
    bq = fake_bq()
    src.fetch_catalog_rows(bq)
    assert len(bq.configs) == 6
    assert all(c.maximum_bytes_billed == MAX_BYTES_CATALOG for c in bq.configs)
    for call in bq.query.return_value.result.call_args_list:
        assert call.kwargs.get("page_size") == 50_000


def test_dry_run_reads_nothing():
    bq = fake_bq()
    rows = src.fetch_catalog_rows(bq, dry_run=True)
    assert len(bq.configs) == 6
    assert all(c.dry_run for c in bq.configs)
    assert list(rows.songs) == []


def test_rows_assembled():
    rows = src.fetch_catalog_rows(fake_bq())
    songs = {s.song_key: s for s in rows.songs}

    sunny = songs[song_key("Alpha Band", "Sunny Day")]
    assert sunny.in_kn == 1 and sunny.brand_count == 3 and sunny.is_classic == 0
    assert sunny.mood == "happy" and sunny.decade == "1990s" and sunny.year == 1994
    assert sunny.explicit == 0 and sunny.duration_ms == 200_000 and sunny.spotify_pop == 60
    assert sunny.lead_gender == "mixed"
    assert set(sunny.genres) == {"classic-rock", "rock", "indie"}
    assert sunny.energy == 0.9 and sunny.tempo == 120.0

    quiet = songs[song_key("Alpha Band", "Quiet Night")]
    assert quiet.mood is None and quiet.decade is None  # no facets
    assert songs[song_key("Alpha Band", "Old Favourite")].is_classic == 1

    # r1 is already in KN so only r2 becomes a no-karaoke row.
    hidden = songs[song_key("Alpha Band", "Hidden Hit")]
    assert hidden.in_kn == 0 and hidden.lb_listeners == 8000 and hidden.decade == "2000s"
    assert len(rows.songs) == 4

    assert {k.kn_id for k in rows.kn} == {1, 2, 3}
    assert rows.kn[0].match_key == "alpha band:sunny day"
    assert [a.artist_key for a in rows.artists] == ["alpha band"]  # Beta has no songs
    assert rows.artists[0].gender == "mixed"

    # Unknown Act is not a catalog artist; scores scale by the per-artist max.
    assert {(s.artist_key, s.similar_key): s.score for s in rows.similar} == {
        ("alpha band", "beta singer"): 1.0,
        ("alpha band", "gamma"): 0.5,
    }
    assert all(s.source == "mlhd" for s in rows.similar)
    assert rows.genre_map == {"classic rock": "classic-rock", "k-pop": "kpop"}


def test_no_karaoke_capped_at_ten_per_artist():
    many = [
        {"recording_mbid": f"r{i}", "title": f"Track {i}", "artist": "Alpha Band", "listeners": 5000 - i}
        for i in range(15)
    ]
    out = src._no_karaoke_songs(iter(many), set(), {})
    assert len(out) == 10
    assert min(s.lb_listeners for s in out) == 4991


def test_no_musicbrainz_http():
    tree = ast.parse(Path(src.__file__).read_text())
    imported = {
        alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    } | {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert not imported & {"httpx", "requests", "urllib", "aiohttp"}
    assert "musicbrainz.org" not in Path(src.__file__).read_text()
