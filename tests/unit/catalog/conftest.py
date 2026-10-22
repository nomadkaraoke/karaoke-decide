"""Factories for catalog.db builder tests (invented data only)."""

from typing import Any

import pytest

from karaoke_decide.catalog.build import ArtistRow, CatalogRows, KnRow, SimilarRow, SongRow


def _song(n: int, **kw: Any) -> SongRow:
    base: dict[str, Any] = {
        "song_key": f"artist {n}|song {n}",
        "artist": f"Artist {n}",
        "title": f"Song {n}",
        "artist_key": f"artist {n}",
        "brand_count": n,
        "explicit": 0,
        "duration_ms": 200_000,
        "year": 1990 + n,
        "decade": "1990s",
        "lb_listeners": 1000 * n,
        "spotify_pop": 50,
        "energy": 0.8,
        "valence": 0.7,
        "mood": "happy",
        "lead_gender": "male",
        "genres": ("rock", "indie"),
    }
    base.update(kw)
    return SongRow(**base)


@pytest.fixture
def make_rows():
    def factory(**overrides: Any) -> CatalogRows:
        fields: dict[str, Any] = {
            "songs": [_song(1), _song(2, genres=("pop",)), _song(3, genres=())],
            "artists": [ArtistRow("artist 1", "Artist 1", genres=("rock",)), ArtistRow("artist 2", "Artist 2")],
            "similar": [SimilarRow("artist 1", "artist 2", 0.7, "test")],
            "kn": [KnRow(1, "artist 1|song 1", "Artist 1", "Song 1", "CC,KV", 2, "artist 1:song 1")],
            "genre_map": {"alt rock": "indie"},
        }
        fields.update(overrides)
        return CatalogRows(**fields)

    return factory


@pytest.fixture
def song():
    return _song
