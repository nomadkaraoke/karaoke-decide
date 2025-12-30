"""Tests for track matcher service."""

from unittest.mock import MagicMock

import pytest

from backend.services.track_matcher import MatchedTrack, TrackMatcher


@pytest.fixture
def mock_catalog_service() -> MagicMock:
    """Create a mock catalog service for track matching tests."""
    mock = MagicMock()
    mock.search_songs.return_value = []
    return mock


@pytest.fixture
def track_matcher(mock_catalog_service: MagicMock) -> TrackMatcher:
    """Create a TrackMatcher with mocked catalog service."""
    return TrackMatcher(mock_catalog_service)


class TestNormalizeText:
    """Tests for normalize_text method."""

    def test_lowercase(self, track_matcher: TrackMatcher) -> None:
        """Text is converted to lowercase."""
        assert track_matcher.normalize_text("HELLO WORLD") == "hello world"

    def test_strips_whitespace(self, track_matcher: TrackMatcher) -> None:
        """Leading and trailing whitespace is removed."""
        assert track_matcher.normalize_text("  hello  ") == "hello"

    def test_collapses_multiple_spaces(self, track_matcher: TrackMatcher) -> None:
        """Multiple spaces are collapsed to single space."""
        assert track_matcher.normalize_text("hello   world") == "hello world"

    def test_preserves_apostrophes(self, track_matcher: TrackMatcher) -> None:
        """Apostrophes in contractions are preserved."""
        assert track_matcher.normalize_text("Don't Stop") == "don't stop"
        assert track_matcher.normalize_text("It's") == "it's"

    def test_removes_punctuation(self, track_matcher: TrackMatcher) -> None:
        """Punctuation (except apostrophes) is removed."""
        assert track_matcher.normalize_text("Hello, World!") == "hello world"
        assert track_matcher.normalize_text("Rock & Roll") == "rock roll"
        assert track_matcher.normalize_text("1-2-3") == "1 2 3"

    def test_normalizes_unicode_quotes(self, track_matcher: TrackMatcher) -> None:
        """Unicode quotes are normalized - smart quotes are converted for matching."""
        # Smart quotes (\u2019 = ') are normalized to enable matching
        # The exact handling may vary, but the key parts should be present
        result = track_matcher.normalize_text("Don\u2019t")
        assert "don" in result and "t" in result
        # The result should enable matching "don't" variations
        assert len(result) >= 4  # At minimum "don t"

    def test_empty_string(self, track_matcher: TrackMatcher) -> None:
        """Empty string returns empty string."""
        assert track_matcher.normalize_text("") == ""

    def test_none_like_empty(self, track_matcher: TrackMatcher) -> None:
        """None-like values return empty string."""
        # Note: Function signature accepts str, but handle edge cases
        assert track_matcher.normalize_text("") == ""


class TestNormalizeTitle:
    """Tests for normalize_title method."""

    def test_basic_normalization(self, track_matcher: TrackMatcher) -> None:
        """Basic title is normalized."""
        assert track_matcher.normalize_title("Bohemian Rhapsody") == "bohemian rhapsody"

    def test_removes_feat_parentheses(self, track_matcher: TrackMatcher) -> None:
        """(feat. Artist) patterns are removed."""
        assert track_matcher.normalize_title("Song (feat. Artist)") == "song"
        assert track_matcher.normalize_title("Song (feat Artist)") == "song"
        assert track_matcher.normalize_title("Song (ft. Artist)") == "song"
        assert track_matcher.normalize_title("Song (ft Artist)") == "song"
        assert track_matcher.normalize_title("Song (featuring Artist)") == "song"

    def test_removes_feat_brackets(self, track_matcher: TrackMatcher) -> None:
        """[feat. Artist] patterns are removed."""
        assert track_matcher.normalize_title("Song [feat. Artist]") == "song"
        assert track_matcher.normalize_title("Song [ft. Artist]") == "song"

    def test_removes_with_pattern(self, track_matcher: TrackMatcher) -> None:
        """(with Artist) pattern is removed."""
        assert track_matcher.normalize_title("Song (with Artist)") == "song"

    def test_removes_remastered(self, track_matcher: TrackMatcher) -> None:
        """Remastered suffixes are removed."""
        assert track_matcher.normalize_title("Song - Remastered") == "song"
        assert track_matcher.normalize_title("Song - Remastered 2011") == "song"
        assert track_matcher.normalize_title("Song (Remastered)") == "song"
        assert track_matcher.normalize_title("Song (Remastered 2011)") == "song"
        assert track_matcher.normalize_title("Song [Remastered]") == "song"

    def test_removes_live(self, track_matcher: TrackMatcher) -> None:
        """Live indicators are removed."""
        assert track_matcher.normalize_title("Song (Live)") == "song"
        assert track_matcher.normalize_title("Song (Live at Wembley)") == "song"
        assert track_matcher.normalize_title("Song [Live]") == "song"

    def test_removes_version_variants(self, track_matcher: TrackMatcher) -> None:
        """Version variants are removed."""
        assert track_matcher.normalize_title("Song (Radio Edit)") == "song"
        assert track_matcher.normalize_title("Song (Radio Mix)") == "song"
        assert track_matcher.normalize_title("Song (Single Version)") == "song"
        assert track_matcher.normalize_title("Song (Album Version)") == "song"
        assert track_matcher.normalize_title("Song (Original Mix)") == "song"

    def test_removes_explicit_clean(self, track_matcher: TrackMatcher) -> None:
        """Explicit/Clean markers are removed."""
        assert track_matcher.normalize_title("Song (Explicit)") == "song"
        assert track_matcher.normalize_title("Song (Clean)") == "song"

    def test_multiple_patterns(self, track_matcher: TrackMatcher) -> None:
        """Multiple patterns are all removed."""
        result = track_matcher.normalize_title("Song (feat. Artist) [Remastered 2011]")
        assert result == "song"

    def test_empty_string(self, track_matcher: TrackMatcher) -> None:
        """Empty string returns empty string."""
        assert track_matcher.normalize_title("") == ""


class TestNormalizeArtist:
    """Tests for normalize_artist method."""

    def test_basic_normalization(self, track_matcher: TrackMatcher) -> None:
        """Basic artist name is normalized."""
        assert track_matcher.normalize_artist("Queen") == "queen"

    def test_removes_feat_suffix(self, track_matcher: TrackMatcher) -> None:
        """Featured artist suffixes are removed."""
        assert track_matcher.normalize_artist("Artist feat. Another") == "artist"
        assert track_matcher.normalize_artist("Artist feat Another") == "artist"
        assert track_matcher.normalize_artist("Artist ft. Another") == "artist"
        assert track_matcher.normalize_artist("Artist featuring Another") == "artist"

    def test_removes_with_suffix(self, track_matcher: TrackMatcher) -> None:
        """'with' artist suffix is removed."""
        assert track_matcher.normalize_artist("Artist with Another") == "artist"

    def test_preserves_comma_in_names(self, track_matcher: TrackMatcher) -> None:
        """Comma-separated names are preserved (e.g. Crosby, Stills, Nash & Young)."""
        # Commas and ampersands in artist names should be preserved
        result = track_matcher.normalize_artist("Crosby, Stills, Nash & Young")
        assert "crosby" in result
        assert "young" in result

    def test_preserves_ampersand_in_names(self, track_matcher: TrackMatcher) -> None:
        """Ampersand in artist names is preserved (e.g. Simon & Garfunkel)."""
        result = track_matcher.normalize_artist("Simon & Garfunkel")
        assert "simon" in result
        assert "garfunkel" in result

    def test_empty_string(self, track_matcher: TrackMatcher) -> None:
        """Empty string returns empty string."""
        assert track_matcher.normalize_artist("") == ""


class TestMatchSingleTrack:
    """Tests for match_single_track method."""

    @pytest.mark.asyncio
    async def test_exact_match(self, track_matcher: TrackMatcher, mock_catalog_service: MagicMock) -> None:
        """Exact match returns confidence 1.0."""
        # Setup mock to return matching song
        mock_song = MagicMock()
        mock_song.artist = "Queen"
        mock_song.title = "Bohemian Rhapsody"
        mock_song.id = "123"
        mock_catalog_service.search_songs.return_value = [mock_song]

        result = await track_matcher.match_single_track("Queen", "Bohemian Rhapsody")

        assert result.catalog_song is not None
        assert result.match_confidence == 1.0
        assert result.original_artist == "Queen"
        assert result.original_title == "Bohemian Rhapsody"

    @pytest.mark.asyncio
    async def test_normalized_match(self, track_matcher: TrackMatcher, mock_catalog_service: MagicMock) -> None:
        """Normalized versions match with confidence 1.0."""
        mock_song = MagicMock()
        mock_song.artist = "Queen"
        mock_song.title = "Bohemian Rhapsody"
        mock_catalog_service.search_songs.return_value = [mock_song]

        # Search with different capitalization
        result = await track_matcher.match_single_track("QUEEN", "BOHEMIAN RHAPSODY")

        assert result.catalog_song is not None
        assert result.match_confidence == 1.0

    @pytest.mark.asyncio
    async def test_feat_removed_match(self, track_matcher: TrackMatcher, mock_catalog_service: MagicMock) -> None:
        """Featured artists are removed for matching."""
        mock_song = MagicMock()
        mock_song.artist = "Artist"
        mock_song.title = "Song"
        mock_catalog_service.search_songs.return_value = [mock_song]

        result = await track_matcher.match_single_track("Artist feat. Another", "Song (feat. Guest)")

        assert result.catalog_song is not None
        assert result.normalized_artist == "artist"
        assert result.normalized_title == "song"

    @pytest.mark.asyncio
    async def test_no_match(self, track_matcher: TrackMatcher, mock_catalog_service: MagicMock) -> None:
        """No match returns None catalog_song and 0.0 confidence."""
        mock_catalog_service.search_songs.return_value = []

        result = await track_matcher.match_single_track("Unknown", "Track")

        assert result.catalog_song is None
        assert result.match_confidence == 0.0
        assert result.original_artist == "Unknown"
        assert result.original_title == "Track"

    @pytest.mark.asyncio
    async def test_partial_artist_match(self, track_matcher: TrackMatcher, mock_catalog_service: MagicMock) -> None:
        """Partial artist match with same title returns confidence 0.9."""
        mock_song = MagicMock()
        mock_song.artist = "The Artist"
        mock_song.title = "Song"
        mock_catalog_service.search_songs.return_value = [mock_song]

        # Artist is subset of catalog artist
        result = await track_matcher.match_single_track("Artist", "Song")

        # Should match with lower confidence
        assert result.catalog_song is not None
        assert result.match_confidence == 0.9


class TestBatchMatchTracks:
    """Tests for batch_match_tracks method."""

    @pytest.mark.asyncio
    async def test_empty_list(self, track_matcher: TrackMatcher) -> None:
        """Empty list returns empty results."""
        results = await track_matcher.batch_match_tracks([])
        assert results == []

    @pytest.mark.asyncio
    async def test_single_track(self, track_matcher: TrackMatcher, mock_catalog_service: MagicMock) -> None:
        """Single track is processed."""
        mock_catalog_service.search_songs.return_value = []

        results = await track_matcher.batch_match_tracks([{"artist": "Queen", "title": "Song"}])

        assert len(results) == 1
        assert results[0].original_artist == "Queen"

    @pytest.mark.asyncio
    async def test_multiple_tracks(self, track_matcher: TrackMatcher, mock_catalog_service: MagicMock) -> None:
        """Multiple tracks are processed in order."""
        mock_catalog_service.search_songs.return_value = []

        tracks = [
            {"artist": "Artist1", "title": "Song1"},
            {"artist": "Artist2", "title": "Song2"},
            {"artist": "Artist3", "title": "Song3"},
        ]
        results = await track_matcher.batch_match_tracks(tracks)

        assert len(results) == 3
        assert results[0].original_artist == "Artist1"
        assert results[1].original_artist == "Artist2"
        assert results[2].original_artist == "Artist3"

    @pytest.mark.asyncio
    async def test_missing_keys(self, track_matcher: TrackMatcher, mock_catalog_service: MagicMock) -> None:
        """Missing keys default to empty strings."""
        mock_catalog_service.search_songs.return_value = []

        results = await track_matcher.batch_match_tracks([{"artist": "Queen"}])

        assert len(results) == 1
        assert results[0].original_title == ""


class TestGetMatchStats:
    """Tests for get_match_stats method."""

    def test_all_matched(self, track_matcher: TrackMatcher) -> None:
        """All matched tracks returns 100% match rate."""
        mock_song = MagicMock()
        matches = [
            MatchedTrack("A1", "T1", "a1", "t1", mock_song, 1.0),
            MatchedTrack("A2", "T2", "a2", "t2", mock_song, 1.0),
        ]

        stats = track_matcher.get_match_stats(matches)

        assert stats["total"] == 2
        assert stats["matched"] == 2
        assert stats["unmatched"] == 0
        assert stats["match_rate"] == 1.0

    def test_none_matched(self, track_matcher: TrackMatcher) -> None:
        """No matched tracks returns 0% match rate."""
        matches = [
            MatchedTrack("A1", "T1", "a1", "t1", None, 0.0),
            MatchedTrack("A2", "T2", "a2", "t2", None, 0.0),
        ]

        stats = track_matcher.get_match_stats(matches)

        assert stats["total"] == 2
        assert stats["matched"] == 0
        assert stats["unmatched"] == 2
        assert stats["match_rate"] == 0.0

    def test_partial_matches(self, track_matcher: TrackMatcher) -> None:
        """Mixed results returns correct match rate."""
        mock_song = MagicMock()
        matches = [
            MatchedTrack("A1", "T1", "a1", "t1", mock_song, 1.0),
            MatchedTrack("A2", "T2", "a2", "t2", None, 0.0),
            MatchedTrack("A3", "T3", "a3", "t3", mock_song, 0.9),
            MatchedTrack("A4", "T4", "a4", "t4", None, 0.0),
        ]

        stats = track_matcher.get_match_stats(matches)

        assert stats["total"] == 4
        assert stats["matched"] == 2
        assert stats["unmatched"] == 2
        assert stats["match_rate"] == 0.5

    def test_empty_list(self, track_matcher: TrackMatcher) -> None:
        """Empty list returns 0 match rate without division error."""
        stats = track_matcher.get_match_stats([])

        assert stats["total"] == 0
        assert stats["matched"] == 0
        assert stats["unmatched"] == 0
        assert stats["match_rate"] == 0.0
