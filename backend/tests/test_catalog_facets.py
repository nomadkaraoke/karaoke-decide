"""song_facets / song_titles over a tiny catalog.db (+ availability.db) built from the real DDL."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from backend.services.catalog_facets import SongFacets, song_facets, song_titles
from karaoke_decide.catalog.moods import MOODS
from karaoke_decide.catalog.reader import CatalogReader
from karaoke_decide.catalog.schema import DDL

# Invented songs only.
LOUD = "the lanterns|paper harbour"  # high energy, community + karafun
SOFT = "moss and ember|slow tide"  # low energy, commercial only, no vr
KN_ONLY = "old cartwheel|brass lane"  # in_kn, not in availability.db (commercial fallback)
NO_KARAOKE = "ghost choir|nothing here"  # in catalog, no karaoke version


def build_catalog(tmp: Path, with_availability: bool = True) -> CatalogReader:
    cat = tmp / "catalog.db"
    db = sqlite3.connect(cat)
    db.executescript(DDL)
    db.execute("INSERT INTO meta VALUES ('run', 'test')")
    rows = [
        (LOUD, "The Lanterns", "Paper Harbour", 0.85, 0.9, "happy", 1, "comfortable"),
        (SOFT, "Moss and Ember", "Slow Tide", 0.2, 0.3, "sad", 1, None),
        (KN_ONLY, "Old Cartwheel", "Brass Lane", 0.5, 0.5, "chill", 1, "stretch"),
        (NO_KARAOKE, "Ghost Choir", "Nothing Here", None, None, None, 0, None),
    ]
    for key, artist, title, energy, valence, mood, in_kn, tier in rows:
        db.execute(
            "INSERT INTO songs (song_key, artist, title, energy, valence, mood, in_kn, vr_tier) VALUES (?,?,?,?,?,?,?,?)",
            (key, artist, title, energy, valence, mood, in_kn, tier),
        )
    db.commit()
    db.close()
    av_path = None
    if with_availability:
        av_path = tmp / "availability.db"
        av = sqlite3.connect(av_path)
        av.executescript(
            """
            CREATE TABLE songs(song_key TEXT PRIMARY KEY);
            CREATE TABLE versions(song_key TEXT, brand_code TEXT, kind TEXT);
            CREATE TABLE song_libraries(song_key TEXT, library_code TEXT, PRIMARY KEY (song_key, library_code));
            INSERT INTO songs VALUES ('the lanterns|paper harbour'), ('moss and ember|slow tide');
            INSERT INTO versions VALUES ('the lanterns|paper harbour', 'CMN', 'community'),
                ('the lanterns|paper harbour', 'KV', 'commercial'),
                ('moss and ember|slow tide', 'SF', 'commercial');
            INSERT INTO song_libraries VALUES ('the lanterns|paper harbour', 'KV');
            """
        )
        av.commit()
        av.close()
    return CatalogReader(cat, av_path)


def test_facets_with_availability(tmp_path: Path) -> None:
    reader = build_catalog(tmp_path)
    f = song_facets(reader, [LOUD, SOFT, KN_ONLY, NO_KARAOKE, "missing|song"])
    assert set(f) == {LOUD, SOFT, KN_ONLY, NO_KARAOKE}  # uncatalogued keys omitted
    assert f[LOUD] == SongFacets(
        mood="happy", energy_band="high", has_karaoke=True, community=True, karafun=True, vr_tier="comfortable"
    )
    assert f[LOUD].mood in MOODS
    assert (f[SOFT].energy_band, f[SOFT].has_karaoke, f[SOFT].community, f[SOFT].karafun) == ("low", True, False, False)
    assert (f[KN_ONLY].has_karaoke, f[KN_ONLY].community) == (True, False)  # in_kn fallback
    assert f[NO_KARAOKE] == SongFacets()


def test_facets_without_availability_db(tmp_path: Path) -> None:
    reader = build_catalog(tmp_path, with_availability=False)
    f = song_facets(reader, [LOUD, NO_KARAOKE])
    assert (f[LOUD].has_karaoke, f[LOUD].community, f[LOUD].karafun) == (True, False, False)
    assert f[NO_KARAOKE].has_karaoke is False


def test_facets_chunks_over_500_keys(tmp_path: Path) -> None:
    reader = build_catalog(tmp_path)
    keys = [f"nobody|song {i}" for i in range(1200)] + [LOUD]
    assert set(song_facets(reader, keys)) == {LOUD}


def test_song_titles(tmp_path: Path) -> None:
    reader = build_catalog(tmp_path)
    assert song_titles(reader, [LOUD, "missing|song"]) == {LOUD: ("The Lanterns", "Paper Harbour")}
