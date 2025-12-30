"""Tests for text normalization utilities."""


from karaoke_decide.utils.text import generate_song_id, normalize_artist, normalize_title


class TestNormalizeArtist:
    """Tests for normalize_artist function."""

    def test_lowercase(self) -> None:
        assert normalize_artist("QUEEN") == "queen"

    def test_removes_the_prefix(self) -> None:
        assert normalize_artist("The Beatles") == "beatles"

    def test_strips_whitespace(self) -> None:
        assert normalize_artist("  Queen  ") == "queen"

    def test_collapses_multiple_spaces(self) -> None:
        # Note: "The" removal only applies to prefix, not middle of name
        assert normalize_artist("Panic  At   The  Disco") == "panic at the disco"

    def test_handles_unicode(self) -> None:
        assert normalize_artist("Björk") == "bjork"


class TestNormalizeTitle:
    """Tests for normalize_title function."""

    def test_lowercase(self) -> None:
        assert normalize_title("Bohemian Rhapsody") == "bohemian rhapsody"

    def test_removes_parenthetical(self) -> None:
        assert normalize_title("Song Title (Radio Edit)") == "song title"
        assert normalize_title("Song Title (Remastered 2011)") == "song title"

    def test_removes_brackets(self) -> None:
        assert normalize_title("Song Title [Live]") == "song title"

    def test_removes_remastered_suffix(self) -> None:
        assert normalize_title("Song Title - Remastered") == "song title"

    def test_strips_whitespace(self) -> None:
        assert normalize_title("  Song Title  ") == "song title"


class TestGenerateSongId:
    """Tests for generate_song_id function."""

    def test_basic_id(self) -> None:
        assert generate_song_id("Queen", "Bohemian Rhapsody") == "queen-bohemian-rhapsody"

    def test_handles_special_characters(self) -> None:
        # slugify converts "/" to "-"
        assert generate_song_id("AC/DC", "Back in Black") == "ac-dc-back-in-black"

    def test_handles_the_prefix(self) -> None:
        assert generate_song_id("The Beatles", "Hey Jude") == "beatles-hey-jude"

    def test_handles_parenthetical(self) -> None:
        song_id = generate_song_id("Nirvana", "Smells Like Teen Spirit (Remastered)")
        assert song_id == "nirvana-smells-like-teen-spirit"
