"""CatalogReader: read-only catalog.db queries with availability.db ATTACHed as ``av``."""

from __future__ import annotations

import gzip
import shutil
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from karaoke_decide.catalog import fixtures
from karaoke_decide.catalog.build import ArtistRow, CatalogRows, SimilarRow, SongRow, build_catalog_db
from karaoke_decide.catalog.reader import CatalogReader, KnMatch
from karaoke_decide.song_search.availability import build_availability_db
from karaoke_decide.song_search.build_song_id import build as build_song_id

SONG_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "song_search"


@pytest.fixture(scope="module")
def catalog_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return fixtures.build(tmp_path_factory.mktemp("catalog"))


@pytest.fixture(scope="module")
def availability_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("availability")
    build_song_id([SONG_FIXTURES / "songs_fixture.tsv"], d / "song_id.db", run="20261018-000000")
    for name in ("full", "community"):
        with open(SONG_FIXTURES / f"kn_{name}_fixture.json", "rb") as f, gzip.open(d / f"{name}.json.gz", "wb") as g:
            shutil.copyfileobj(f, g)
    build_availability_db(
        d / "full.json.gz", d / "community.json.gz", d / "song_id.db", d / "availability.db", run="20261018-000000"
    )
    return d / "availability.db"


@pytest.fixture
def reader(catalog_db: Path, availability_db: Path) -> Iterator[CatalogReader]:
    r = CatalogReader(catalog_db, availability_db)
    yield r
    r.close()


def test_reader_attach_availability(reader: CatalogReader) -> None:
    assert reader.has_availability
    (count,) = reader.conn().execute("SELECT COUNT(*) FROM av.songs").fetchone()
    assert count > 0
    # catalog.db songs join availability by song_key.
    (joined,) = reader.conn().execute("SELECT COUNT(*) FROM songs s JOIN av.songs a USING (song_key)").fetchone()
    assert joined > 0


def test_reader_without_availability(catalog_db: Path) -> None:
    r = CatalogReader(catalog_db, None)
    assert not r.has_availability
    assert r.conn().execute("SELECT COUNT(*) FROM songs").fetchone()[0] > 0
    with pytest.raises(sqlite3.OperationalError):
        r.conn().execute("SELECT COUNT(*) FROM av.songs")
    r.close()


def test_conn_is_read_only(reader: CatalogReader) -> None:
    with pytest.raises(sqlite3.OperationalError):
        reader.conn().execute("DELETE FROM songs")
    with pytest.raises(sqlite3.OperationalError):
        reader.conn().execute("DELETE FROM av.songs")


def test_conn_is_thread_local(reader: CatalogReader) -> None:
    main = reader.conn()
    assert reader.conn() is main
    other: list[sqlite3.Connection] = []
    t = threading.Thread(target=lambda: other.append(reader.conn()))
    t.start()
    t.join()
    assert other and other[0] is not main
    reader.close()


def test_missing_catalog_fails_fast(tmp_path: Path) -> None:
    with pytest.raises(sqlite3.Error):
        CatalogReader(tmp_path / "nope.db", None)


def test_match_kn_uses_match_key(reader: CatalogReader) -> None:
    hit = reader.match_kn("Toto", "Africa")
    assert hit == KnMatch(kn_id=1, artist="Toto", title="Africa", brands="CC,KV", brand_count=2)
    # Same normalization as the legacy in-memory lookup: case, feat. and remaster tags.
    assert reader.match_kn("TOTO feat. Someone", "Africa (Remastered 2011)") == hit
    assert reader.match_kn("Toto", "No Such Song") is None
    assert reader.match_kn("", "") is None


def test_kn_song_keys(reader: CatalogReader) -> None:
    keys = reader.kn_song_keys([1, 2, 999_999])
    assert keys == {1: "toto|africa", 2: "toto|hold the line"}
    assert reader.kn_song_keys([]) == {}


def test_kn_ids_for_song_keys(reader: CatalogReader) -> None:
    ids = reader.kn_ids_for_song_keys(["toto|africa", "toto|hold the line", "nobody|nothing"])
    assert ids == {"toto|africa": 1, "toto|hold the line": 2}
    assert reader.kn_ids_for_song_keys([]) == {}


def test_similar_limits_per_artist(reader: CatalogReader) -> None:
    (key,) = reader.conn().execute("SELECT artist_key FROM artist_similar LIMIT 1").fetchone()
    rows = reader.similar([key, key], limit_per=1)
    assert len(rows) == 1 and rows[0][0] == key
    all_rows = reader.similar([key])
    assert len(all_rows) >= 1
    assert [r[2] for r in all_rows] == sorted((r[2] for r in all_rows), reverse=True)
    assert reader.similar([]) == []


def test_top_songs_for_artists(reader: CatalogReader) -> None:
    rows = reader.top_songs_for_artists(["fixture artist 01", "fixture artist 02"], per_artist=3)
    by_artist: dict[str, list[int]] = {}
    for r in rows:
        by_artist.setdefault(r["artist_key"], []).append(r["popularity"])
    assert set(by_artist) == {"fixture artist 01", "fixture artist 02"}
    for pops in by_artist.values():
        assert len(pops) == 3
        assert pops == sorted(pops, reverse=True)
    assert reader.top_songs_for_artists([], per_artist=3) == []


def test_artist_mbid(tmp_path: Path, make_rows) -> None:  # type: ignore[no-untyped-def]
    db = tmp_path / "catalog.db"
    build_catalog_db(make_rows(artists=[ArtistRow("artist 1", "Artist 1", mbid="mbid-1")]), db, "r")
    r = CatalogReader(db, None)
    assert r.artist_mbid("Artist 1") == "mbid-1"
    assert r.artist_mbid("ARTIST 1") == "mbid-1"
    assert r.artist_mbid("Nobody") is None
    r.close()


def _artist_catalog(tmp_path: Path) -> CatalogReader:
    """Invented artists: popularity Alpha > Beta > Gamma > Delta; Echo has no songs."""

    def songs(key: str, name: str, decade: str, n: int, brands: int) -> list[SongRow]:
        return [
            SongRow(f"{key}|tune {i}", name, f"Tune {i}", key, brand_count=brands, decade=decade, lb_listeners=i)
            for i in range(1, n + 1)
        ]

    rows = CatalogRows(
        songs=[
            *songs("alpha", "Alpha", "1980s", 3, 2),
            *songs("beta band", "Beta Band", "1990s", 2, 1),
            *songs("gamma ray", "Gamma Ray", "1980s", 1, 4),
            *songs("delta", "Delta", "2000s", 1, 1),
            SongRow("delta|old one", "Delta", "Old One", "delta", decade="1980s", lb_listeners=1),
            SongRow("delta|older one", "Delta", "Older One", "delta", decade="1980s", lb_listeners=1),
        ],
        artists=[
            ArtistRow("alpha", "Alpha", mbid="mbid-a", popularity=90, genres=("rock",)),
            ArtistRow("beta band", "Beta Band", popularity=80, genres=("pop",)),
            ArtistRow("gamma ray", "Gamma Ray", popularity=70, genres=("metal", "rock")),
            ArtistRow("delta", "Delta", popularity=60, genres=("pop",)),
            ArtistRow("echo", "Echo", popularity=99, genres=("rock",)),
        ],
        similar=[SimilarRow("alpha", "gamma ray", 0.9, "test")],
        kn=[],
        genre_map={},
    )
    db = tmp_path / "catalog.db"
    build_catalog_db(rows, db, "r")
    return CatalogReader(db, None)


def test_popular_artist_keys_filters_and_orders(tmp_path: Path) -> None:
    r = _artist_catalog(tmp_path)
    # Echo is the most popular artist but has no songs, so it never appears.
    assert r.popular_artist_keys(limit=10) == ["alpha", "beta band", "gamma ray", "delta"]
    assert r.popular_artist_keys(limit=2) == ["alpha", "beta band"]
    assert r.popular_artist_keys(limit=10, genres=["rock"]) == ["alpha", "gamma ray"]
    assert r.popular_artist_keys(limit=10, decades=["1980s"]) == ["alpha", "gamma ray", "delta"]
    assert r.popular_artist_keys(limit=10, genres=["pop"], decades=["1980s"]) == ["delta"]
    assert r.popular_artist_keys(limit=2, exclude_keys=["alpha"]) == ["beta band", "gamma ray"]
    r.close()


def test_artist_summaries(tmp_path: Path) -> None:
    r = _artist_catalog(tmp_path)
    got = r.artist_summaries(["delta", "alpha", "nobody"])
    assert list(got) == ["delta", "alpha"]
    alpha = got["alpha"]
    assert (alpha.name, alpha.mbid, alpha.popularity, alpha.genres) == ("Alpha", "mbid-a", 90, ("rock",))
    assert (alpha.song_count, alpha.brand_total, alpha.primary_decade) == (3, 6, "1980s")
    assert len(alpha.top_songs) == 3 and set(alpha.top_songs) == {"Tune 1", "Tune 2", "Tune 3"}
    # Two 1980s songs beat one 2000s song.
    assert got["delta"].primary_decade == "1980s"
    assert r.artist_summaries([]) == {}
    r.close()


def test_search_artists_ranks_exact_prefix_word_substring(tmp_path: Path) -> None:
    r = _artist_catalog(tmp_path)
    assert [a.name for a in r.search_artists("alpha")] == ["Alpha"]
    assert [a.name for a in r.search_artists("BAND")] == ["Beta Band"]  # word prefix, case-folded
    assert [a.name for a in r.search_artists("ta")] == ["Beta Band", "Delta"]  # substring, by popularity
    assert [a.name for a in r.search_artists("e")] == ["Echo", "Beta Band", "Delta"]  # prefix tier first
    assert [a.name for a in r.search_artists("a", limit=1)] == ["Alpha"]
    assert r.search_artists("%") == [] and r.search_artists("_") == [] and r.search_artists("  ") == []
    r.close()
