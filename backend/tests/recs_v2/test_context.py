"""v2 user context: sources, weights (max wins), kn mapping, exclusions, paging."""

from __future__ import annotations

from typing import Any

import pytest

from backend.services.recs_v2 import weights as w
from backend.services.recs_v2.context import UserContextV2, build_context
from karaoke_decide.catalog.keys import artist_key
from karaoke_decide.catalog.reader import CatalogReader
from karaoke_decide.song_search.text_normalize import song_key

UID = "user_abc"


class FakeFirestore:
    """In-memory stand-in covering the equality filters / limit / offset build_context uses."""

    def __init__(self, **collections: list[dict[str, Any]]) -> None:
        self.cols = collections
        self.song_queries: list[tuple[int | None, int | None]] = []

    async def get_document(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        return next((d for d in self.cols.get(collection, []) if d.get("id") == doc_id), None)

    async def query_documents(
        self,
        collection: str,
        filters: list[tuple[str, str, Any]] | None = None,
        order_by: str | None = None,
        order_direction: str = "ASCENDING",
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[dict[str, Any]]:
        if collection == "user_songs":
            self.song_queries.append((limit, offset))
        rows = [d for d in self.cols.get(collection, []) if all(d.get(f) == v for f, _, v in (filters or []))]
        rows = rows[offset or 0 :]
        return rows[:limit] if limit else rows


class FakeFeedback:
    def __init__(self, hidden: set[str] | None = None) -> None:
        self.hidden = hidden or set()

    async def hidden_keys(self, user_id: str) -> set[str]:
        return self.hidden


def _song(song_id: str, artist: str, title: str, source: str = "quiz", **extra: Any) -> dict[str, Any]:
    return {"user_id": UID, "song_id": song_id, "source": source, "artist": artist, "title": title, **extra}


def _artist(name: str, source: str, rank: int | None = None) -> dict[str, Any]:
    return {"user_id": UID, "artist_name": name, "source": source, "rank": rank}


async def _ctx(reader: CatalogReader, fs: FakeFirestore, hidden: set[str] | None = None) -> UserContextV2:
    return await build_context(fs, reader, FakeFeedback(hidden), UID)  # type: ignore[arg-type]


async def test_max_across_sources(reader: CatalogReader) -> None:
    fs = FakeFirestore(
        decide_users=[
            {
                "id": "h",
                "user_id": UID,
                "quiz_artist_affinities": [{"artist_name": "Toto", "affinity": "love"}],
                "quiz_manual_artists": [{"artist_name": "Weezer"}],
            }
        ],
        user_artists=[_artist("Toto", "spotify", 100), _artist("Weezer", "lastfm", 1), _artist("Abba", "manual")],
        user_songs=[],
    )
    ctx = await _ctx(reader, fs)
    assert ctx.artists[artist_key("Toto")].weight == pytest.approx(w.QUIZ_LOVE)  # 0.9 beats 0.5 (rank 100)
    assert ctx.artists[artist_key("Toto")].source == "quiz"
    assert ctx.artists[artist_key("Weezer")].weight == pytest.approx(w.artist_top(1))  # 0.995 beats 0.8
    assert ctx.artists[artist_key("Abba")].weight == w.ARTIST_MANUAL


def test_weight_table() -> None:
    assert w.artist_top(0) == 1.0 and w.artist_top(100) == 0.5 and w.artist_top(500) == 0.5
    assert w.artist_from_songs(2) == pytest.approx(0.6) and w.artist_from_songs(50) == 0.9
    assert [w.lastfm_song(n) for n in (12, 10, 5, 3, 2, 1, None)] == [1.0, 1.0, 0.7, 0.7, 0.4, 0.4, 0.4]
    assert w.song_weight({"source": "manual"}) == 1.0
    assert w.song_weight({"source": "spotify", "rank": 3}) == 0.8
    assert w.song_weight({"source": "spotify", "rank": None}) == 0.9
    assert w.song_weight({"source": "youtube_music"}) == 0.8
    assert w.song_weight({"source": "quiz"}) == w.song_weight({"source": "listenbrainz"}) == 0.6


async def test_artists_of_known_songs(reader: CatalogReader) -> None:
    fs = FakeFirestore(
        user_songs=[_song(f"spotify:{i}", "Some Band", f"Tune {i}") for i in range(3)],
    )
    ctx = await _ctx(reader, fs)
    sig = ctx.artists[artist_key("Some Band")]
    assert sig.weight == pytest.approx(0.7) and sig.source == "songs" and sig.name == "Some Band"


async def test_excluded_artist_removed(reader: CatalogReader) -> None:
    fs = FakeFirestore(
        decide_users=[{"id": "h", "user_id": UID, "excluded_artists": ["toto"]}],
        user_artists=[_artist("Toto", "spotify", 1), _artist("Weezer", "spotify", 2)],
        user_songs=[_song("1", "Toto", "Africa")],
    )
    ctx = await _ctx(reader, fs)
    assert artist_key("Toto") not in ctx.artists and artist_key("Weezer") in ctx.artists
    assert ctx.known_songs  # excluded artist removes the artist signal, not the known song


async def test_kn_id_maps_to_song_key(reader: CatalogReader) -> None:
    fs = FakeFirestore(user_songs=[_song("1", "", "", source="manual")])  # kn_id 1 = Toto - Africa
    ctx = await _ctx(reader, fs)
    assert ctx.known_songs == {song_key("Toto", "Africa"): w.SONG_MANUAL}
    assert ctx.known_without_karaoke == []


async def test_spotify_id_song_maps_by_artist_title(reader: CatalogReader) -> None:
    fs = FakeFirestore(
        user_songs=[_song("spotify:xyz", "Weezer", "Buddy Holly", source="spotify", rank=4)],
    )
    ctx = await _ctx(reader, fs)
    assert ctx.known_songs == {song_key("Weezer", "Buddy Holly"): w.SONG_SPOTIFY_TOP}
    assert ctx.known_without_karaoke == []  # the catalog has a karaoke version


async def test_no_karaoke_known_song_listed(reader: CatalogReader) -> None:
    fs = FakeFirestore(user_songs=[_song("lastfm:a:b", "Nobody Band", "Unmade Song", source="lastfm", playcount=12)])
    ctx = await _ctx(reader, fs)
    k = song_key("Nobody Band", "Unmade Song")
    assert ctx.known_songs == {k: 1.0}
    assert ctx.known_without_karaoke == [(k, "Nobody Band", "Unmade Song")]


async def test_hidden_loaded(reader: CatalogReader) -> None:
    ctx = await _ctx(reader, FakeFirestore(), hidden={"a|b", "c|d"})
    assert ctx.hidden == {"a|b", "c|d"}


async def test_cold_and_has_profile(reader: CatalogReader) -> None:
    fs = FakeFirestore(
        decide_users=[
            {
                "id": "h",
                "user_id": UID,
                "quiz_genres": ["Rock"],
                "quiz_decade_pref": "1990s",
                "quiz_energy_pref": "high",
                "quiz_crowd_pleaser_pref": "hits",
            }
        ]
    )
    ctx = await _ctx(reader, fs)
    assert ctx.has_profile and ctx.is_cold
    assert ctx.genres == {"rock"} and ctx.decades == {"1990s"}
    assert ctx.energy_pref == "high" and ctx.crowd_pref == "hits"
    empty = await _ctx(reader, FakeFirestore())
    assert not empty.has_profile and empty.is_cold


async def test_guest_doc_by_id(reader: CatalogReader) -> None:
    fs = FakeFirestore(decide_users=[{"id": "guest_1", "quiz_genres": ["pop"]}])
    ctx = await build_context(fs, reader, FakeFeedback(), "guest_1")  # type: ignore[arg-type]
    assert ctx.genres == {"pop"}


async def test_over_1000_user_songs_all_read(reader: CatalogReader) -> None:
    fs = FakeFirestore(user_songs=[_song(f"spotify:{i}", "Band", f"Tune {i}") for i in range(2300)])
    ctx = await _ctx(reader, fs)
    assert len(ctx.known_songs) == 2300
    assert fs.song_queries == [(1000, 0), (1000, 1000), (1000, 2000)]
