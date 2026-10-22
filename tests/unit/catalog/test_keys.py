from karaoke_decide.catalog.keys import artist_key, match_key, song_key
from karaoke_decide.song_search import text_normalize


def test_match_key_matches_legacy_catalog_lookup_make_key() -> None:
    # Value captured from CatalogLookup()._make_key before the refactor.
    assert match_key("Guns N' Roses", "Sweet Child O' Mine") == "guns n roses:sweet child o mine"
    assert match_key("Artist feat. Other", "Song (Live)") == "artist:song"


def test_artist_key_is_identity_normalize() -> None:
    assert artist_key("Beyoncé feat. Someone") == text_normalize.identity_normalize("Beyoncé feat. Someone")
    assert artist_key(None) == ""


def test_song_key_is_reexported() -> None:
    assert song_key is text_normalize.song_key


def test_loose_keys_ignore_punctuation_spacing_and_apostrophes() -> None:
    from karaoke_decide.catalog.keys import loose_keys

    # KN spells it "U.S.A." or "Usa"; Spotify/MB the other way. Same song.
    assert loose_keys("Miley Cyrus", "Party In The U.S.A.") & loose_keys("Miley Cyrus", "Party In The Usa")
    assert loose_keys("David Guetta & Bebe Rexha", "I M Good (Blue)") & loose_keys(
        "David Guetta and Bebe Rexha", "I'm Good (Blue)"
    )


KNOWN = frozenset(
    {"david guetta", "bebe rexha", "simon", "garfunkel", "simon and garfunkel", "earth", "wind", "fire"}
) | {"earth wind and fire"}


def test_loose_keys_primary_artist_only_for_real_multi_artist_credits() -> None:
    from karaoke_decide.catalog.keys import loose_keys

    assert loose_keys("David Guetta & Bebe Rexha", "I'm Good", KNOWN) & loose_keys("David Guetta", "I'm Good", KNOWN)
    # Acts whose name contains "and"/"&"/"," are not collapsed to their first word, even when the
    # first word is itself a known artist.
    assert not loose_keys("Simon and Garfunkel", "The Boxer", KNOWN) & loose_keys("Simon", "The Boxer", KNOWN)
    assert not loose_keys("Earth, Wind & Fire", "September", KNOWN) & loose_keys("Earth", "September", KNOWN)
    # Without a known-artist set nothing is collapsed.
    assert not loose_keys("David Guetta & Bebe Rexha", "I'm Good") & loose_keys("David Guetta", "I'm Good")


def test_loose_keys_bracket_free_title_pins_what_is_stripped() -> None:
    from karaoke_decide.catalog.keys import loose_keys

    # Every bracketed qualifier is dropped (the song, not the version, is what has a karaoke track)...
    assert loose_keys("A", "Song (Live)") & loose_keys("A", "Song")
    assert loose_keys("A", "Song [From The Vault]") & loose_keys("A", "Song")
    # ...but the unbracketed title is kept too, so a bracket-only title is not erased...
    assert loose_keys("A", "(Untitled)") == frozenset({"a:untitled"})
    # ...and unbracketed qualifiers still separate songs.
    assert not loose_keys("A", "Song - Part Two") & loose_keys("A", "Song")
    assert loose_keys("Taylor Swift", "22 (Taylor's Version)") & loose_keys("Taylor Swift", "22")
    assert loose_keys("Metallica", "The Unforgiven") & loose_keys("Metallica", "Unforgiven")


def test_loose_keys_do_not_merge_different_songs_or_artists() -> None:
    from karaoke_decide.catalog.keys import loose_keys

    assert not loose_keys("Maroon 5", "One More Night") & loose_keys("BTS", "One More Night")
    assert not loose_keys("AC/DC", "Thunderstruck", KNOWN) & loose_keys("AC", "Thunderstruck", KNOWN)
    assert not loose_keys("Muse", "Hysteria") & loose_keys("Muse", "Uprising")


def test_loose_keys_empty_when_a_side_is_blank() -> None:
    from karaoke_decide.catalog.keys import loose_keys

    assert loose_keys("", "Song") == frozenset()
    assert loose_keys("Artist", "  ") == frozenset()
    assert loose_keys(None, None) == frozenset()
