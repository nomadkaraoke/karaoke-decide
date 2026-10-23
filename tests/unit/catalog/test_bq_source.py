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
SONG_VR = [
    {"song_key": "alpha band|sunny day", "low_midi": 50.0, "high_midi": 70.0, "p10": 53.0, "p50": 60.0,
     "p90": 66.0, "tier": "measured", "octave_confidence": None, "is_duet": False},
]  # fmt: skip
ARTIST_VR = [
    {"artist_key": "alpha band", "low_midi": 48.0, "high_midi": 68.0, "p10": 51.0, "p50": 58.0, "p90": 64.0,
     "basis": "songs"},
]  # fmt: skip


def fake_bq(vr_error=None):
    """A client that routes on SQL substrings and records every job_config."""
    bq = MagicMock()
    bq.configs = []
    bq.jobs = []

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
        elif sql in (src.VOCAL_RANGE_SQL, src.ARTIST_VOCAL_RANGE_SQL):
            if vr_error is not None:
                raise vr_error
            rows = SONG_VR if sql == src.VOCAL_RANGE_SQL else ARTIST_VR
        else:
            raise AssertionError("unexpected query")
        job = MagicMock()
        job.result.side_effect = lambda page_size=None: iter(rows)
        bq.jobs.append(job)
        return job

    bq.query.side_effect = query
    return bq


@pytest.mark.parametrize("name", SQL_CONSTANTS)
def test_sql_never_maxes_audio_features(name):
    assert not re.search(
        r"(MAX|MIN|AVG|ANY_VALUE)\(\s*(\w+\.)?(energy|valence|danceability|tempo)\b", SQL_CONSTANTS[name], re.IGNORECASE
    )


def test_facets_pick_one_track():
    assert "ROW_NUMBER() OVER (PARTITION BY" in src.SONG_FACETS_SQL


def test_every_query_has_byte_cap_and_streams_pages():
    bq = fake_bq()
    src.fetch_catalog_rows(bq)
    assert len(bq.configs) == 8
    assert all(c.maximum_bytes_billed == MAX_BYTES_CATALOG for c in bq.configs)
    assert len(bq.jobs) == 8
    for job in bq.jobs:
        job.result.assert_called_once_with(page_size=50_000)


def test_dry_run_reads_nothing():
    bq = fake_bq()
    rows = src.fetch_catalog_rows(bq, dry_run=True)
    assert len(bq.configs) == 8
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


def test_no_karaoke_listener_floor_fits_listenbrainz_scale():
    # LB all-time listener counts are small (max ~10k, p99 ~117): a 1000 floor kept only ~3k
    # recordings worldwide and the catalog got ~200 "make it" songs instead of tens of thousands.
    assert 10 <= src.NO_KARAOKE_MIN_LISTENERS <= 50
    assert f"listeners >= {src.NO_KARAOKE_MIN_LISTENERS}" in src.NO_KARAOKE_SQL


def test_no_karaoke_sql_dedupes_songs_before_ranking_per_artist():
    # Several MB recordings of one song (album, single, reissue) must not use up an artist's
    # candidate slots: keep the most-listened recording per (artist, title) first, then rank.
    sql = " ".join(src.NO_KARAOKE_SQL.split())
    per_song = sql.find("PARTITION BY l.artist_normalized, l.base_title")
    per_artist = sql.find("PARTITION BY artist_normalized ORDER BY")
    assert per_song != -1 and per_artist != -1
    assert per_song < per_artist


def test_no_karaoke_sql_compares_bracket_free_titles_with_kn():
    sql = " ".join(src.NO_KARAOKE_SQL.split())
    assert "k.base_title = l.base_title" in sql
    assert sql.count("REGEXP_REPLACE(Title, r'[\\[(][^\\])]*[\\])]', '')") == 1
    assert "REGEXP_REPLACE(r.title, r'[\\[(][^\\])]*[\\])]', '')" in sql


def _kn_row(kn_id, artist, title):
    from karaoke_decide.catalog.build import KnRow

    return KnRow(kn_id, song_key(artist, title), artist, title, "SF", 1, "")


def test_no_karaoke_skips_bracketed_variants_of_kn_songs():
    # KN has "22" and "Fearless (Acoustic)": "22 (Taylor's Version)" and "Fearless" already have a
    # karaoke version, so they are not "make it" candidates. A genuinely new song still is.
    kn = [_kn_row(1, "Tay Singer", "22"), _kn_row(2, "Tay Singer", "Fearless (Acoustic)")]
    rows = [
        {"recording_mbid": "a", "title": "22 (Taylor’s Version)", "artist": "Tay Singer", "listeners": 600},
        {"recording_mbid": "b", "title": "Fearless", "artist": "Tay Singer", "listeners": 500},
        {"recording_mbid": "c", "title": "Brand New [From The Vault]", "artist": "Tay Singer", "listeners": 400},
        {"recording_mbid": "d", "title": "Brand New", "artist": "Tay Singer", "listeners": 300},
    ]
    out = src._no_karaoke_songs(iter(rows), src._kn_title_keys(kn), {})
    assert [s.title for s in out] == ["Brand New [From The Vault]"]


def test_no_karaoke_drops_interludes_and_skits():
    titles = [
        "Intro",
        "Outro",
        "Interlude",
        "Prelude",
        "Skit",
        "Album Interlude",
        "Real Hit",
        "Introspection",
        "Skitter",
    ]
    rows = [
        {"recording_mbid": f"r{i}", "title": t, "artist": "Alpha Band", "listeners": 5000 - i}
        for i, t in enumerate(titles)
    ]
    out = src._no_karaoke_songs(iter(rows), set(), {})
    assert sorted(s.title for s in out) == ["Introspection", "Real Hit", "Skitter"]


def test_no_karaoke_skips_songs_whose_loose_key_matches_a_kn_song():
    # Prod bug (run 20261022-015849): spelling variants that song_key keeps apart ("U.S.A." vs
    # "Usa", "I M Good" vs "I'm Good", a two-artist credit vs the primary artist) leaked into
    # "make it" although KN has the song. Genuinely absent songs still come through.
    kn = [
        _kn_row(1, "Miley Cyrus", "Party In The Usa"),
        _kn_row(2, "David Guetta & Bebe Rexha", "I M Good (Blue)"),
        _kn_row(3, "Maroon 5", "One More Night"),
    ]
    rows = [
        {"recording_mbid": "a", "title": "Party In The U.S.A.", "artist": "Miley Cyrus", "listeners": 900},
        {"recording_mbid": "b", "title": "I'm Good (Blue)", "artist": "David Guetta", "listeners": 800},
        {"recording_mbid": "c", "title": "I'm Good (Blue)", "artist": "David Guetta, Bebe Rexha", "listeners": 790},
        {"recording_mbid": "d", "title": "One More Night", "artist": "BTS", "listeners": 700},
    ]
    known = frozenset({"miley cyrus", "david guetta", "bebe rexha", "maroon 5", "bts"})
    out = src._no_karaoke_songs(iter(rows), src._kn_title_keys(kn, known), {}, known)
    assert [(s.artist, s.title) for s in out] == [("BTS", "One More Night")]


def test_no_karaoke_dedupes_loose_variants_of_one_candidate():
    rows = [
        {"recording_mbid": "a", "title": "Brand New Day", "artist": "Alpha Band", "listeners": 900},
        {"recording_mbid": "b", "title": "Brand New Day!", "artist": "Alpha Band", "listeners": 800},
    ]
    out = src._no_karaoke_songs(iter(rows), set(), {})
    assert [s.title for s in out] == ["Brand New Day"]


def test_vocal_range_sql_has_byte_cap():
    bq = fake_bq()
    src.fetch_catalog_rows(bq)
    sqls = [c.args[0] for c in bq.query.call_args_list]
    for sql in (src.VOCAL_RANGE_SQL, src.ARTIST_VOCAL_RANGE_SQL):
        i = sqls.index(sql)
        assert bq.configs[i].maximum_bytes_billed == MAX_BYTES_CATALOG
    assert "song_vocal_range`" in src.VOCAL_RANGE_SQL and "octave_confidence" in src.VOCAL_RANGE_SQL
    assert "artist_vocal_range`" in src.ARTIST_VOCAL_RANGE_SQL


def test_vocal_range_rows_filled_with_rough_fallback():
    rows = src.fetch_catalog_rows(fake_bq())
    songs = {s.song_key: s for s in rows.songs}
    sunny = songs[song_key("Alpha Band", "Sunny Day")]
    assert (sunny.vr_p50, sunny.vr_tier, sunny.vr_duet) == (60.0, "measured", 0)
    quiet = songs[song_key("Alpha Band", "Quiet Night")]
    assert (quiet.vr_p50, quiet.vr_tier, quiet.vr_octave_conf, quiet.vr_duet) == (58.0, "rough", None, None)
    assert (rows.artists[0].vr_p50, rows.artists[0].vr_basis) == (58.0, "songs")


def test_missing_vocal_range_tables_warn_and_leave_vr_null(caplog):
    from google.api_core.exceptions import NotFound

    with caplog.at_level("INFO"):
        rows = src.fetch_catalog_rows(fake_bq(vr_error=NotFound("Table song_vocal_range not found")))
    assert all(s.vr_p50 is None and s.vr_tier is None for s in rows.songs)
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1 and "vocal" in warnings[0].getMessage().lower()
    assert not [r for r in caplog.records if r.levelname == "ERROR"]
