import sqlite3

from karaoke_decide.catalog import fixtures
from karaoke_decide.catalog.genres import GENRE_IDS
from karaoke_decide.catalog.moods import ENERGY_BANDS, MOODS

DECADES = [f"{d}s" for d in range(1950, 2030, 10)]


def _distinct(conn, column):
    return {r[0] for r in conn.execute(f"SELECT DISTINCT {column} FROM songs WHERE in_kn = 1")}


def test_fixture_build_covers_every_filter_value(tmp_path):
    path = fixtures.build(tmp_path)
    assert path == tmp_path / "catalog.db"
    conn = sqlite3.connect(path)
    genres = {
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT genre_id FROM song_genres g JOIN songs s USING (song_key) WHERE s.in_kn = 1"
        )
    }
    assert set(GENRE_IDS[:-1]) <= genres
    assert set(MOODS) <= _distinct(conn, "mood")
    for band, (lo, hi) in ENERGY_BANDS.items():
        n = conn.execute(
            "SELECT COUNT(*) FROM songs WHERE in_kn = 1 AND energy >= ? AND energy < ?", (lo, hi)
        ).fetchone()[0]
        assert n >= 1, band
    assert set(DECADES) <= _distinct(conn, "decade")
    assert {"male", "female", "mixed"} <= _distinct(conn, "lead_gender")
    q = "SELECT COUNT(*) FROM songs WHERE in_kn = 1 AND "
    assert conn.execute(q + "duration_ms < 180000").fetchone()[0] >= 1
    assert conn.execute(q + "duration_ms BETWEEN 180000 AND 300000").fetchone()[0] >= 1
    assert conn.execute(q + "duration_ms > 300000").fetchone()[0] >= 1
    assert {0, 1} <= _distinct(conn, "explicit")
    assert {0, 1} <= _distinct(conn, "is_classic")
    assert path.stat().st_size < 5_000_000


def test_fixture_contents_and_pins(tmp_path):
    conn = sqlite3.connect(fixtures.build(tmp_path))
    conn.row_factory = sqlite3.Row
    muse = conn.execute("SELECT * FROM songs WHERE song_key = 'muse|starlight'").fetchone()
    assert muse["decade"] == "2000s" and 2000 <= muse["year"] <= 2009
    assert muse["energy"] >= 0.7 and muse["lead_gender"] == "male"
    assert {r[0] for r in conn.execute("SELECT genre_id FROM song_genres WHERE song_key = 'muse|starlight'")} == {
        "rock",
        "indie",
    }
    adele = conn.execute("SELECT * FROM songs WHERE song_key = 'adele|hello'").fetchone()
    assert adele["decade"] == "2010s" and adele["lead_gender"] == "female"
    assert adele["mood"] == "chill" and adele["energy"] < 0.55
    assert [r[0] for r in conn.execute("SELECT genre_id FROM song_genres WHERE song_key = 'adele|hello'")] == ["pop"]

    assert conn.execute("SELECT COUNT(*) FROM songs WHERE in_kn = 0").fetchone()[0] == 200
    assert (
        conn.execute("SELECT COUNT(*) FROM songs WHERE in_kn = 0 AND artist NOT LIKE 'Fixture Artist %'").fetchone()[0]
        == 0
    )
    assert conn.execute("SELECT COUNT(DISTINCT artist) FROM songs WHERE in_kn = 0").fetchone()[0] == 20
    ids = [r[0] for r in conn.execute("SELECT kn_id FROM kn_song_map ORDER BY kn_id")]
    assert ids == list(range(1, len(ids) + 1)) and len(ids) == 408
    assert conn.execute("SELECT kn_id FROM kn_song_map WHERE artist = 'Toto' AND title = 'Africa'").fetchone()[0] == 1
    assert conn.execute("SELECT brand_count FROM kn_song_map WHERE kn_id = 1").fetchone()[0] == 2


def test_fixture_similarity_shares_primary_genre(tmp_path):
    conn = sqlite3.connect(fixtures.build(tmp_path))
    rows = conn.execute(
        "SELECT a.genres_json, b.genres_json, s.score FROM artist_similar s "
        "JOIN artists a ON a.artist_key = s.artist_key JOIN artists b ON b.artist_key = s.similar_key"
    ).fetchall()
    assert rows
    for ga, gb, score in rows:
        assert ga.split(",")[0] == gb.split(",")[0]
        assert 0.5 <= score <= 0.9


def test_fixture_build_is_deterministic(tmp_path):
    def dump(d):
        conn = sqlite3.connect(fixtures.build(d))
        return conn.execute("SELECT * FROM songs ORDER BY song_key").fetchall()

    assert dump(tmp_path / "a") == dump(tmp_path / "b")


def test_fixture_catalog_carries_vocal_ranges(tmp_path):
    conn = sqlite3.connect(fixtures.build(tmp_path))
    try:

        def vr(key):
            return conn.execute(
                "SELECT vr_p10, vr_p50, vr_p90, vr_tier, vr_octave_conf, vr_duet FROM songs WHERE song_key = ?", (key,)
            ).fetchone()

        assert vr("muse|starlight") == (51.0, 57.3, 63.0, "measured", None, 0)
        assert vr("muse|uprising")[3:] == ("notes", 0.92, 0)
        assert vr("adele|hello")[3:] == ("estimated", None, 0)
        assert vr("brandy and monica|the boy is mine")[5] == 1
        assert vr("taylor swift|shake it off")[3:] == ("rough", None, None)
        assert vr("lady gaga|shallow") == (None,) * 6
        assert conn.execute("SELECT vr_basis FROM artists WHERE artist_key = 'taylor swift'").fetchone() == ("gender",)
    finally:
        conn.close()
