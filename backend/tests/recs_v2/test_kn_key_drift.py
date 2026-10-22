"""catalog.db (BigQuery KN) and availability.db (KN exports) spell some titles differently.

Prod bug (catalog run 20261022-015849): "Party In The Usa" / "I M Good (Blue)" are in_kn = 1 in
catalog.db but their song_key is not in availability.db (which has "Party In The U.S.A." etc.), so
recs v2 called them "no karaoke version yet" and showed them in make_it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from backend.services.recs_v2.candidates import gather_candidates
from backend.services.recs_v2.context import UserContextV2, build_context
from backend.services.recs_v2.filters import RecFilters
from backend.tests.recs_v2.test_context import FakeFeedback, FakeFirestore, _song
from karaoke_decide.catalog.build import CatalogRows, KnRow, SongRow, build_catalog_db
from karaoke_decide.catalog.keys import match_key
from karaoke_decide.catalog.reader import CatalogReader
from karaoke_decide.song_search import availability
from karaoke_decide.song_search.text_normalize import song_key

DRIFT = song_key("Miley Cyrus", "Party In The Usa")  # catalog.db spelling, absent from availability.db
SPELLED = song_key("Miley Cyrus", "Party In The U.S.A.")  # availability.db spelling
UNMADE = song_key("Some Band", "Truly Unmade")


def _row(artist: str, title: str, in_kn: int) -> SongRow:
    return SongRow(
        song_key=song_key(artist, title), artist=artist, title=title, artist_key=song_key(artist, "").rstrip("|"),
        spotify_pop=40, genres=("pop",), in_kn=in_kn,
    )  # fmt: skip


@pytest.fixture
def drift_reader(tmp_path: Path) -> Iterator[CatalogReader]:
    songs = [_row("Miley Cyrus", "Party In The Usa", 1), _row("Some Band", "Truly Unmade", 0)]
    kn = [KnRow(1, DRIFT, "Miley Cyrus", "Party In The Usa", "SF", 1, match_key("Miley Cyrus", "Party In The Usa"))]
    build_catalog_db(CatalogRows(songs=songs, kn=kn), tmp_path / "catalog.db", run="t")
    av = sqlite3.connect(tmp_path / "availability.db")
    av.executescript(availability._SCHEMA)
    av.execute("INSERT INTO songs VALUES (?,?,?,?,?)", (SPELLED, "Miley Cyrus", "Party In The U.S.A.", 90, 1))
    av.execute("INSERT INTO versions VALUES (?,?,?,?,?,?)", (SPELLED, "SF", "Sunfly", "commercial", None, 1))
    av.commit()
    av.close()
    r = CatalogReader(tmp_path / "catalog.db", tmp_path / "availability.db")
    yield r
    r.close()


def _ctx() -> UserContextV2:
    return UserContextV2(user_id="u", genres={"pop"}, decades=set())


def test_in_kn_song_missing_from_availability_still_has_karaoke(drift_reader: CatalogReader) -> None:
    by_key = {c.song_key: c for c in gather_candidates(drift_reader, _ctx(), RecFilters())}
    assert by_key[DRIFT].has_karaoke and by_key[DRIFT].commercial
    assert not by_key[UNMADE].has_karaoke


def test_has_karaoke_filters_follow_in_kn_when_availability_misses(drift_reader: CatalogReader) -> None:
    yes = {c.song_key for c in gather_candidates(drift_reader, _ctx(), RecFilters(has_karaoke="yes"))}
    make = {c.song_key for c in gather_candidates(drift_reader, _ctx(), RecFilters(has_karaoke="make"))}
    assert yes == {DRIFT}
    assert make == {UNMADE}


async def test_known_song_with_availability_spelling_is_not_unmade(drift_reader: CatalogReader) -> None:
    # Spotify says "Party In The U.S.A.": match_key("...u s a") misses KN's "...usa", but the
    # availability index holds exactly that spelling, so the user already has a karaoke version.
    fs = FakeFirestore(user_songs=[_song("spotify:x", "Miley Cyrus", "Party In The U.S.A.", source="spotify", rank=1)])
    ctx = await build_context(fs, drift_reader, FakeFeedback(), "user_abc")  # type: ignore[arg-type]
    assert SPELLED in ctx.known_songs
    assert ctx.known_without_karaoke == []


async def test_known_song_matching_in_kn_catalog_song_is_not_unmade(drift_reader: CatalogReader) -> None:
    fs = FakeFirestore(user_songs=[_song("spotify:x", "Miley Cyrus", "Party in the USA", source="spotify", rank=1)])
    ctx = await build_context(fs, drift_reader, FakeFeedback(), "user_abc")  # type: ignore[arg-type]
    assert ctx.known_without_karaoke == []
