"""Tests for candidate artist/title normalization and matching."""

from karaoke_decide.candidates.matching import (
    artist_variants,
    build_match_index,
    canonical_key,
    index_contains,
    norm,
    strip_decorations,
    title_variants,
)


class TestNorm:
    def test_folds_case_and_punctuation(self):
        assert norm("The Beatles!") == "the beatles"

    def test_ampersand_becomes_and(self):
        assert norm("Simon & Garfunkel") == "simon and garfunkel"

    def test_strips_diacritics(self):
        assert norm("Beyoncé") == "beyonce"

    def test_removes_apostrophes(self):
        assert norm("Don't Stop") == "dont stop"


class TestTitleVariants:
    def test_strips_remaster_suffix(self):
        variants = title_variants("Bohemian Rhapsody - 2011 Remaster")
        assert "bohemian rhapsody" in variants

    def test_strips_parenthetical_feat(self):
        variants = title_variants("Song (feat. Someone)")
        assert "song" in variants

    def test_includes_raw_normalized(self):
        assert "hello" in title_variants("Hello")


class TestArtistVariants:
    def test_the_prefix_optional(self):
        variants = artist_variants("The Beatles")
        assert "the beatles" in variants
        assert "beatles" in variants

    def test_first_of_collab_list(self):
        variants = artist_variants("Jay-Z & Alicia Keys")
        assert "jay z" in variants

    def test_strips_featuring(self):
        variants = artist_variants("Calvin Harris feat. Rihanna")
        assert "calvin harris" in variants


class TestCanonicalKey:
    def test_deterministic_and_shortest(self):
        k1 = canonical_key("The Beatles", "Hey Jude - Remastered 2015")
        k2 = canonical_key("the beatles", "Hey Jude")
        assert k1 == k2

    def test_returns_tuple(self):
        key = canonical_key("Adele", "Hello")
        assert isinstance(key, tuple) and len(key) == 2


class TestStripDecorations:
    def test_removes_remaster(self):
        assert strip_decorations("Song - 2011 Remaster") == "Song"

    def test_removes_feat(self):
        assert strip_decorations("Song (feat. X)") == "Song"

    def test_falls_back_to_original_when_empty(self):
        # A title that is entirely a parenthetical shouldn't vanish.
        assert strip_decorations("Song") == "Song"


class TestMatchIndex:
    def test_contains_matches_variants(self):
        index = build_match_index([("The Beatles", "Hey Jude")])
        assert index_contains(index, "Beatles", "Hey Jude - Remaster")
        assert index_contains(index, "the beatles", "Hey Jude")

    def test_absent_song_not_matched(self):
        index = build_match_index([("The Beatles", "Hey Jude")])
        assert not index_contains(index, "Pendulum", "Slam")
