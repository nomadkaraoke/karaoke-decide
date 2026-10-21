from karaoke_decide.catalog.keys import artist_key, match_key, song_key
from karaoke_decide.song_search import text_normalize


def test_match_key_matches_legacy_catalog_lookup_make_key() -> None:
    # Value captured from CatalogLookup()._make_key before the refactor.
    assert match_key("Guns N' Roses", "Sweet Child O' Mine") == "guns n roses:sweet child o mine"
    assert match_key("Artist feat. Other", "Song (Live)") == "artist:song"


def test_catalog_lookup_delegates_to_match_key() -> None:
    from backend.services.catalog_lookup import CatalogLookup

    assert CatalogLookup()._make_key("Simon & Garfunkel", "Song (Remastered 2011)") == match_key(
        "Simon & Garfunkel", "Song (Remastered 2011)"
    )


def test_artist_key_is_identity_normalize() -> None:
    assert artist_key("Beyoncé feat. Someone") == text_normalize.identity_normalize("Beyoncé feat. Someone")
    assert artist_key(None) == ""


def test_song_key_is_reexported() -> None:
    assert song_key is text_normalize.song_key
