"""v2 candidate sources: filters reach every source, no-av fallback, outside-catalog songs, quiz top-up."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from backend.services.recs_v2 import candidates as cmod
from backend.services.recs_v2.candidates import gather_candidates
from backend.services.recs_v2.context import ArtistSignal, UserContextV2
from backend.services.recs_v2.filters import RecFilters
from karaoke_decide.catalog.build import CatalogRows, SongRow, build_catalog_db
from karaoke_decide.catalog.reader import CatalogReader


def _rock_artists(reader: CatalogReader, n: int) -> dict[str, ArtistSignal]:
    rows = reader.conn().execute(
        "SELECT artist_key, name FROM artists WHERE genres_json LIKE '%rock%' ORDER BY artist_key LIMIT ?", (n,)
    )
    return {r[0]: ArtistSignal(0.9, "quiz", r[1]) for r in rows}


def _warm(reader: CatalogReader) -> UserContextV2:
    """A user that exercises every source: known songs, artists (+ similar), quiz, crowd, make it."""
    artists = _rock_artists(reader, 8)
    known = [r[0] for r in reader.conn().execute("SELECT song_key FROM songs ORDER BY song_key LIMIT 40")]
    return UserContextV2(
        user_id="u",
        artists=artists,
        known_songs=dict.fromkeys(known, 1.0),
        known_without_karaoke=[("nowhere|ghost song", "Nowhere", "Ghost Song")],
        genres={"rock", "pop"},
        decades={"1980s", "1990s"},
        energy_pref="high",
    )


def test_filters_apply_to_every_source(reader: CatalogReader) -> None:
    ctx = _warm(reader)
    unfiltered = gather_candidates(reader, ctx, RecFilters())
    assert {c.decade for c in unfiltered} - {"1980s", None}  # the filter has something to remove
    f = RecFilters(decade=["1980s"], explicit="exclude", has_karaoke="yes")
    got = gather_candidates(reader, ctx, f)
    assert got
    bad = [c.song_key for c in got if c.decade != "1980s" or c.explicit or not c.has_karaoke]
    assert bad == []
    assert "nowhere|ghost song" not in {c.song_key for c in got}  # no facets, so never in a filtered list


def test_no_availability_falls_back_to_in_kn(reader_no_av: CatalogReader) -> None:
    ctx = _warm(reader_no_av)
    got = gather_candidates(reader_no_av, ctx, RecFilters())
    in_kn = dict(reader_no_av.conn().execute("SELECT song_key, in_kn FROM songs"))
    in_catalog = [c for c in got if c.song_key in in_kn]
    assert {c.has_karaoke for c in in_catalog} == {True, False}
    for c in in_catalog:
        assert c.has_karaoke == bool(in_kn[c.song_key])
        assert (c.community, c.commercial, c.karafun) == (False, c.has_karaoke, False)
    make = gather_candidates(reader_no_av, ctx, RecFilters(has_karaoke="make"))
    assert make and all(not in_kn.get(c.song_key) for c in make if c.song_key in in_kn)


def test_known_song_outside_catalog_joins_unfiltered_requests(reader: CatalogReader) -> None:
    ctx = _warm(reader)
    by_key = {c.song_key: c for c in gather_candidates(reader, ctx, RecFilters())}
    ghost = by_key["nowhere|ghost song"]
    assert (ghost.artist, ghost.title, ghost.artist_key, ghost.has_karaoke) == (
        "Nowhere",
        "Ghost Song",
        "nowhere",
        False,
    )
    ctx.hidden = {"nowhere|ghost song"}
    assert "nowhere|ghost song" not in {c.song_key for c in gather_candidates(reader, ctx, RecFilters())}


def _song(key: str, genre: str, decade: int | None, energy: float | None, pop: int) -> SongRow:
    artist, title = key.split("|")
    return SongRow(
        song_key=key,
        artist=artist,
        title=title,
        artist_key=artist,
        decade=f"{decade}s" if decade else None,
        year=decade,
        energy=energy,
        spotify_pop=pop,  # no LB listeners, so popularity == spotify_pop (all < 70: no crowd pleasers)
        genres=(genre,),
        in_kn=1,
    )


@pytest.fixture
def quiz_reader(tmp_path: Path) -> Iterator[CatalogReader]:
    songs = [
        _song("a|exact low pop", "rock", 1990, 0.9, 10),
        _song("b|exact", "rock", 1990, 0.8, 20),
        _song("c|wrong decade", "rock", 1970, 0.9, 60),
        _song("d|null decade", "rock", None, 0.9, 50),
        _song("e|null energy", "rock", 1990, None, 40),
        _song("f|low energy", "rock", 1990, 0.1, 30),
        _song("g|pop exact", "pop", 1990, 0.9, 65),
    ]
    build_catalog_db(CatalogRows(songs=songs), tmp_path / "catalog.db", run="test")
    r = CatalogReader(tmp_path / "catalog.db", None)
    yield r
    r.close()


QUIZ_CTX = UserContextV2(user_id="cold", genres={"rock"}, decades={"1990s"}, energy_pref="high")


def test_quiz_tops_up_from_genre_including_null_facets(quiz_reader: CatalogReader) -> None:
    keys = {c.song_key for c in gather_candidates(quiz_reader, QUIZ_CTX, RecFilters())}
    # every rock song, including NULL decade / NULL energy ones; never the pop song
    assert keys == {"a|exact low pop", "b|exact", "c|wrong decade", "d|null decade", "e|null energy", "f|low energy"}


def test_quiz_strict_slice_comes_first(quiz_reader: CatalogReader, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cmod, "QUIZ_SONGS", 3)
    keys = {c.song_key for c in gather_candidates(quiz_reader, QUIZ_CTX, RecFilters())}
    # both exact matches despite low popularity, then the most popular of the rest
    assert keys == {"a|exact low pop", "b|exact", "c|wrong decade"}
