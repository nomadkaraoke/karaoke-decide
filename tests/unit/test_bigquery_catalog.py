"""Tests for BigQuery catalog service."""

from unittest.mock import MagicMock, patch

from karaoke_decide.services.bigquery_catalog import (
    ArtistMetadata,
    ArtistSearchResult,
    BigQueryCatalogService,
    SongResult,
)


class TestSongResult:
    """Tests for SongResult dataclass."""

    def test_create_song_result(self) -> None:
        """Test creating a SongResult."""
        song = SongResult(
            id=1,
            artist="Queen",
            title="Bohemian Rhapsody",
            brands="karafun,singa",
            brand_count=2,
        )
        assert song.id == 1
        assert song.artist == "Queen"
        assert song.title == "Bohemian Rhapsody"
        assert song.brands == "karafun,singa"
        assert song.brand_count == 2

    def test_song_result_brand_list(self) -> None:
        """Test that brands can be split into list."""
        song = SongResult(
            id=1,
            artist="Queen",
            title="Bohemian Rhapsody",
            brands="karafun,singa,lucky-voice",
            brand_count=3,
        )
        brand_list = song.brands.split(",")
        assert len(brand_list) == 3
        assert "karafun" in brand_list


class TestBigQueryCatalogService:
    """Tests for BigQueryCatalogService."""

    @patch("karaoke_decide.services.bigquery_catalog.bigquery.Client")
    def test_init_with_default_client(self, mock_client_class: MagicMock) -> None:
        """Test service initialization with default client."""
        service = BigQueryCatalogService()
        mock_client_class.assert_called_once_with(project="nomadkaraoke")
        assert service.client == mock_client_class.return_value

    @patch("karaoke_decide.services.bigquery_catalog.bigquery.Client")
    def test_init_with_custom_client(self, mock_client_class: MagicMock) -> None:
        """Test service initialization with custom client."""
        custom_client = MagicMock()
        service = BigQueryCatalogService(client=custom_client)
        assert service.client == custom_client
        mock_client_class.assert_not_called()

    @patch("karaoke_decide.services.bigquery_catalog.bigquery.Client")
    def test_search_songs(self, mock_client_class: MagicMock) -> None:
        """Test searching songs by query."""
        mock_client = mock_client_class.return_value
        mock_row = MagicMock()
        mock_row.id = 1
        mock_row.artist = "Queen"
        mock_row.title = "Bohemian Rhapsody"
        mock_row.brands = "karafun,singa"
        mock_row.brand_count = 2
        mock_client.query.return_value.result.return_value = [mock_row]

        service = BigQueryCatalogService()
        results = service.search_songs("bohemian")

        assert len(results) == 1
        assert results[0].artist == "Queen"
        assert results[0].title == "Bohemian Rhapsody"
        mock_client.query.assert_called_once()

    @patch("karaoke_decide.services.bigquery_catalog.bigquery.Client")
    def test_search_songs_with_pagination(self, mock_client_class: MagicMock) -> None:
        """Test searching songs with limit and offset."""
        mock_client = mock_client_class.return_value
        mock_client.query.return_value.result.return_value = []

        service = BigQueryCatalogService()
        service.search_songs("queen", limit=10, offset=20)

        mock_client.query.assert_called_once()
        call_args = mock_client.query.call_args
        config = call_args[1]["job_config"]
        params = {p.name: p.value for p in config.query_parameters}
        assert params["limit"] == 10
        assert params["offset"] == 20

    @patch("karaoke_decide.services.bigquery_catalog.bigquery.Client")
    def test_search_songs_with_min_brands(self, mock_client_class: MagicMock) -> None:
        """Test filtering by minimum brand count."""
        mock_client = mock_client_class.return_value
        mock_client.query.return_value.result.return_value = []

        service = BigQueryCatalogService()
        service.search_songs("queen", min_brands=3)

        call_args = mock_client.query.call_args
        config = call_args[1]["job_config"]
        params = {p.name: p.value for p in config.query_parameters}
        assert params["min_brands"] == 3

    @patch("karaoke_decide.services.bigquery_catalog.bigquery.Client")
    def test_get_song_by_id_found(self, mock_client_class: MagicMock) -> None:
        """Test getting a song by ID when found."""
        mock_client = mock_client_class.return_value
        mock_row = MagicMock()
        mock_row.id = 42
        mock_row.artist = "Journey"
        mock_row.title = "Don't Stop Believin'"
        mock_row.brands = "karafun,singa,lucky-voice"
        mock_row.brand_count = 3
        mock_client.query.return_value.result.return_value = [mock_row]

        service = BigQueryCatalogService()
        result = service.get_song_by_id(42)

        assert result is not None
        assert result.id == 42
        assert result.artist == "Journey"

    @patch("karaoke_decide.services.bigquery_catalog.bigquery.Client")
    def test_get_song_by_id_not_found(self, mock_client_class: MagicMock) -> None:
        """Test getting a song by ID when not found."""
        mock_client = mock_client_class.return_value
        mock_client.query.return_value.result.return_value = []

        service = BigQueryCatalogService()
        result = service.get_song_by_id(99999)

        assert result is None

    @patch("karaoke_decide.services.bigquery_catalog.bigquery.Client")
    def test_get_popular_songs(self, mock_client_class: MagicMock) -> None:
        """Test getting popular songs."""
        mock_client = mock_client_class.return_value
        mock_rows = []
        for i in range(3):
            row = MagicMock()
            row.id = i
            row.artist = f"Artist {i}"
            row.title = f"Song {i}"
            row.brands = "a,b,c,d,e"
            row.brand_count = 5
            mock_rows.append(row)
        mock_client.query.return_value.result.return_value = mock_rows

        service = BigQueryCatalogService()
        results = service.get_popular_songs(limit=3, min_brands=5)

        assert len(results) == 3
        for result in results:
            assert result.brand_count >= 5

    @patch("karaoke_decide.services.bigquery_catalog.bigquery.Client")
    def test_get_songs_by_artist(self, mock_client_class: MagicMock) -> None:
        """Test getting all songs by an artist."""
        mock_client = mock_client_class.return_value
        mock_row = MagicMock()
        mock_row.id = 1
        mock_row.artist = "Queen"
        mock_row.title = "We Are The Champions"
        mock_row.brands = "karafun"
        mock_row.brand_count = 1
        mock_client.query.return_value.result.return_value = [mock_row]

        service = BigQueryCatalogService()
        results = service.get_songs_by_artist("Queen")

        assert len(results) == 1
        assert results[0].artist == "Queen"

    @patch("karaoke_decide.services.bigquery_catalog.bigquery.Client")
    def test_count_songs(self, mock_client_class: MagicMock) -> None:
        """Test counting total songs."""
        mock_client = mock_client_class.return_value
        mock_result = MagicMock()
        mock_result.count = 275809
        mock_client.query.return_value.result.return_value = [mock_result]

        service = BigQueryCatalogService()
        count = service.count_songs()

        assert count == 275809

    @patch("karaoke_decide.services.bigquery_catalog.bigquery.Client")
    def test_get_stats(self, mock_client_class: MagicMock) -> None:
        """Test getting catalog statistics."""
        mock_client = mock_client_class.return_value
        mock_result = MagicMock()
        mock_result.total_songs = 275809
        mock_result.unique_artists = 50000
        mock_result.max_brand_count = 10
        mock_result.avg_brand_count = 2.5678
        mock_client.query.return_value.result.return_value = [mock_result]

        service = BigQueryCatalogService()
        stats = service.get_stats()

        assert stats["total_songs"] == 275809
        assert stats["unique_artists"] == 50000
        assert stats["max_brand_count"] == 10
        assert stats["avg_brand_count"] == 2.57  # Rounded to 2 decimal places

    @patch("karaoke_decide.services.bigquery_catalog.bigquery.Client")
    def test_lookup_artist_by_name_found(self, mock_client_class: MagicMock) -> None:
        """Test looking up an artist by name when found."""
        mock_client = mock_client_class.return_value
        mock_row = MagicMock()
        mock_row.artist_id = "0oSGxfWSnnOXhD2fKuz2Gy"
        mock_row.artist_name = "Queen"
        mock_row.popularity = 88
        mock_row.genres = ["rock", "classic rock"]
        mock_client.query.return_value.result.return_value = [mock_row]

        service = BigQueryCatalogService()
        result = service.lookup_artist_by_name("Queen")

        assert result is not None
        assert isinstance(result, ArtistMetadata)
        assert result.artist_id == "0oSGxfWSnnOXhD2fKuz2Gy"
        assert result.artist_name == "Queen"
        assert result.popularity == 88
        assert result.genres == ["rock", "classic rock"]

    @patch("karaoke_decide.services.bigquery_catalog.bigquery.Client")
    def test_lookup_artist_by_name_not_found(self, mock_client_class: MagicMock) -> None:
        """Test looking up an artist by name when not found."""
        mock_client = mock_client_class.return_value
        mock_client.query.return_value.result.return_value = []

        service = BigQueryCatalogService()
        result = service.lookup_artist_by_name("NonexistentArtist123")

        assert result is None

    @patch("karaoke_decide.services.bigquery_catalog.bigquery.Client")
    def test_lookup_artist_by_name_empty_input(self, mock_client_class: MagicMock) -> None:
        """Test looking up with empty or None input."""
        service = BigQueryCatalogService()

        assert service.lookup_artist_by_name("") is None
        assert service.lookup_artist_by_name(None) is None  # type: ignore

    @patch("karaoke_decide.services.bigquery_catalog.bigquery.Client")
    def test_batch_lookup_artists_by_name(self, mock_client_class: MagicMock) -> None:
        """Test batch lookup of artists by name."""
        mock_client = mock_client_class.return_value

        mock_row1 = MagicMock()
        mock_row1.artist_id = "id1"
        mock_row1.artist_name = "Queen"
        mock_row1.normalized_name = "queen"
        mock_row1.popularity = 88
        mock_row1.genres = ["rock"]

        mock_row2 = MagicMock()
        mock_row2.artist_id = "id2"
        mock_row2.artist_name = "Radiohead"
        mock_row2.normalized_name = "radiohead"
        mock_row2.popularity = 82
        mock_row2.genres = ["alternative rock"]

        mock_client.query.return_value.result.return_value = [mock_row1, mock_row2]

        service = BigQueryCatalogService()
        results = service.batch_lookup_artists_by_name(["Queen", "Radiohead"])

        assert len(results) == 2
        assert "queen" in results
        assert "radiohead" in results
        assert results["queen"].artist_name == "Queen"
        assert results["radiohead"].artist_name == "Radiohead"

    @patch("karaoke_decide.services.bigquery_catalog.bigquery.Client")
    def test_batch_lookup_artists_empty_input(self, mock_client_class: MagicMock) -> None:
        """Test batch lookup with empty list."""
        service = BigQueryCatalogService()
        results = service.batch_lookup_artists_by_name([])
        assert results == {}

    @patch("karaoke_decide.services.bigquery_catalog.bigquery.Client")
    def test_search_artists(self, mock_client_class: MagicMock) -> None:
        """Test artist search for autocomplete."""
        mock_client = mock_client_class.return_value

        mock_row1 = MagicMock()
        mock_row1.artist_id = "id1"
        mock_row1.artist_name = "Queen"
        mock_row1.popularity = 88
        mock_row1.genres = ["rock", "classic rock"]

        mock_row2 = MagicMock()
        mock_row2.artist_id = "id2"
        mock_row2.artist_name = "Queens of the Stone Age"
        mock_row2.popularity = 75
        mock_row2.genres = ["alternative rock"]

        mock_client.query.return_value.result.return_value = [mock_row1, mock_row2]

        service = BigQueryCatalogService()
        results = service.search_artists("queen")

        assert len(results) == 2
        assert all(isinstance(r, ArtistSearchResult) for r in results)
        assert results[0].artist_name == "Queen"
        assert results[0].popularity == 88

    @patch("karaoke_decide.services.bigquery_catalog.bigquery.Client")
    def test_search_artists_short_query(self, mock_client_class: MagicMock) -> None:
        """Test artist search with too short query."""
        service = BigQueryCatalogService()

        # Single character should return empty
        results = service.search_artists("q")
        assert results == []

        # Empty string should return empty
        results = service.search_artists("")
        assert results == []


class TestArtistDataclasses:
    """Tests for Artist dataclasses."""

    def test_artist_metadata(self) -> None:
        """Test ArtistMetadata dataclass."""
        metadata = ArtistMetadata(
            artist_id="123",
            artist_name="Queen",
            popularity=88,
            genres=["rock", "classic rock"],
        )
        assert metadata.artist_id == "123"
        assert metadata.artist_name == "Queen"
        assert metadata.popularity == 88
        assert len(metadata.genres) == 2

    def test_artist_search_result(self) -> None:
        """Test ArtistSearchResult dataclass."""
        result = ArtistSearchResult(
            artist_id="456",
            artist_name="Radiohead",
            popularity=82,
            genres=["alternative rock", "art rock"],
        )
        assert result.artist_id == "456"
        assert result.artist_name == "Radiohead"
        assert result.popularity == 82
        assert result.genres[0] == "alternative rock"


class TestNormalization:
    """Tests for text normalization."""

    def test_normalize_for_matching_basic(self) -> None:
        """Test basic normalization."""
        from karaoke_decide.services.bigquery_catalog import _normalize_for_matching

        assert _normalize_for_matching("Green Day") == "green day"
        assert _normalize_for_matching("RADIOHEAD") == "radiohead"

    def test_normalize_for_matching_punctuation(self) -> None:
        """Test normalization removes punctuation."""
        from karaoke_decide.services.bigquery_catalog import _normalize_for_matching

        assert _normalize_for_matching("AC/DC") == "ac dc"
        assert _normalize_for_matching("Guns N' Roses") == "guns n roses"

    def test_normalize_for_matching_empty(self) -> None:
        """Test normalization handles empty input."""
        from karaoke_decide.services.bigquery_catalog import _normalize_for_matching

        assert _normalize_for_matching("") == ""
        assert _normalize_for_matching(None) == ""  # type: ignore[arg-type]

    def test_normalize_for_matching_public_method(self) -> None:
        """Test public normalize_for_matching method."""
        service = BigQueryCatalogService.__new__(BigQueryCatalogService)
        assert service.normalize_for_matching("Green Day") == "green day"


class TestMBIDLookups:
    """Tests for MBID lookup methods."""

    @patch("karaoke_decide.services.bigquery_catalog.bigquery.Client")
    def test_lookup_mbids_by_spotify_ids_empty(self, mock_client_class: MagicMock) -> None:
        """Test bulk MBID lookup with empty input."""
        service = BigQueryCatalogService()
        result = service.lookup_mbids_by_spotify_ids([])
        assert result == {}
        # Should not make any queries
        mock_client_class.return_value.query.assert_not_called()

    @patch("karaoke_decide.services.bigquery_catalog.bigquery.Client")
    def test_lookup_mbids_by_spotify_ids_success(self, mock_client_class: MagicMock) -> None:
        """Test bulk MBID lookup with results."""
        mock_client = mock_client_class.return_value
        mock_row = MagicMock()
        mock_row.spotify_artist_id = "4Z8W4fKeB5YxbusRsdQVPb"
        mock_row.artist_mbid = "a74b1b7f-71a5-4011-9441-d0b5e4122711"
        mock_client.query.return_value.result.return_value = [mock_row]

        service = BigQueryCatalogService()
        result = service.lookup_mbids_by_spotify_ids(["4Z8W4fKeB5YxbusRsdQVPb"])

        assert result == {"4Z8W4fKeB5YxbusRsdQVPb": "a74b1b7f-71a5-4011-9441-d0b5e4122711"}
        mock_client.query.assert_called_once()

    @patch("karaoke_decide.services.bigquery_catalog.bigquery.Client")
    def test_lookup_mbid_by_spotify_id_uses_bulk(self, mock_client_class: MagicMock) -> None:
        """Test single MBID lookup delegates to bulk method."""
        mock_client = mock_client_class.return_value
        mock_row = MagicMock()
        mock_row.spotify_artist_id = "test123"
        mock_row.artist_mbid = "mbid456"
        mock_client.query.return_value.result.return_value = [mock_row]

        service = BigQueryCatalogService()
        result = service.lookup_mbid_by_spotify_id("test123")

        assert result == "mbid456"

    @patch("karaoke_decide.services.bigquery_catalog.bigquery.Client")
    def test_lookup_mbid_by_spotify_id_not_found(self, mock_client_class: MagicMock) -> None:
        """Test single MBID lookup when not found."""
        mock_client = mock_client_class.return_value
        mock_client.query.return_value.result.return_value = []

        service = BigQueryCatalogService()
        result = service.lookup_mbid_by_spotify_id("unknown")

        assert result is None

    @patch("karaoke_decide.services.bigquery_catalog.bigquery.Client")
    def test_lookup_mbids_by_names_empty(self, mock_client_class: MagicMock) -> None:
        """Test name lookup with empty input."""
        service = BigQueryCatalogService()
        result = service.lookup_mbids_by_names([])
        assert result == {}

    @patch("karaoke_decide.services.bigquery_catalog.bigquery.Client")
    def test_lookup_mbids_by_names_success(self, mock_client_class: MagicMock) -> None:
        """Test name lookup with results."""
        mock_client = mock_client_class.return_value
        mock_row = MagicMock()
        mock_row.name_normalized = "radiohead"
        mock_row.artist_mbid = "a74b1b7f-71a5-4011-9441-d0b5e4122711"
        mock_client.query.return_value.result.return_value = [mock_row]

        service = BigQueryCatalogService()
        result = service.lookup_mbids_by_names(["Radiohead"])

        assert result == {"radiohead": "a74b1b7f-71a5-4011-9441-d0b5e4122711"}
        mock_client.query.assert_called_once()

    @patch("karaoke_decide.services.bigquery_catalog.bigquery.Client")
    def test_get_artist_by_mbid_found(self, mock_client_class: MagicMock) -> None:
        """Test getting artist by MBID when found."""
        mock_client = mock_client_class.return_value
        mock_row = MagicMock()
        mock_row.artist_mbid = "a74b1b7f-71a5-4011-9441-d0b5e4122711"
        mock_row.artist_name = "Radiohead"
        mock_row.disambiguation = "UK rock band"
        mock_row.artist_type = "Group"
        mock_row.spotify_artist_id = "4Z8W4fKeB5YxbusRsdQVPb"
        mock_row.popularity = 80
        mock_row.spotify_genres = ["alternative rock", "art rock"]
        mock_row.tags = ["rock", "alternative"]
        mock_client.query.return_value.result.return_value = [mock_row]

        service = BigQueryCatalogService()
        result = service.get_artist_by_mbid("a74b1b7f-71a5-4011-9441-d0b5e4122711")

        assert result is not None
        assert result.artist_mbid == "a74b1b7f-71a5-4011-9441-d0b5e4122711"
        assert result.artist_name == "Radiohead"
        assert result.disambiguation == "UK rock band"
        assert result.popularity == 80

    @patch("karaoke_decide.services.bigquery_catalog.bigquery.Client")
    def test_get_artist_by_mbid_not_found(self, mock_client_class: MagicMock) -> None:
        """Test getting artist by MBID when not found."""
        mock_client = mock_client_class.return_value
        mock_client.query.return_value.result.return_value = []

        service = BigQueryCatalogService()
        result = service.get_artist_by_mbid("unknown-mbid")

        assert result is None

    @patch("karaoke_decide.services.bigquery_catalog.bigquery.Client")
    def test_get_artist_by_mbid_handles_exception(self, mock_client_class: MagicMock) -> None:
        """Test that exception is handled gracefully."""
        mock_client = mock_client_class.return_value
        mock_client.query.side_effect = Exception("BigQuery error")

        service = BigQueryCatalogService()
        result = service.get_artist_by_mbid("test-mbid")

        assert result is None

    @patch("karaoke_decide.services.bigquery_catalog.bigquery.Client")
    def test_get_artist_by_mbid_null_fields(self, mock_client_class: MagicMock) -> None:
        """Test getting artist with null optional fields."""
        mock_client = mock_client_class.return_value
        mock_row = MagicMock()
        mock_row.artist_mbid = "a74b1b7f-71a5-4011-9441-d0b5e4122711"
        mock_row.artist_name = "Unknown Artist"
        mock_row.disambiguation = None
        mock_row.artist_type = None
        mock_row.spotify_artist_id = None
        mock_row.popularity = None  # Should default to 50
        mock_row.spotify_genres = None
        mock_row.tags = None
        mock_client.query.return_value.result.return_value = [mock_row]

        service = BigQueryCatalogService()
        result = service.get_artist_by_mbid("a74b1b7f-71a5-4011-9441-d0b5e4122711")

        assert result is not None
        assert result.popularity == 50  # Default
        assert result.tags == []  # Empty list
        assert result.spotify_genres is None

    @patch("karaoke_decide.services.bigquery_catalog.bigquery.Client")
    def test_lookup_mbids_by_spotify_ids_deduplicates(self, mock_client_class: MagicMock) -> None:
        """Test that duplicate Spotify IDs are deduplicated."""
        mock_client = mock_client_class.return_value
        mock_client.query.return_value.result.return_value = []

        service = BigQueryCatalogService()
        # Pass duplicates
        service.lookup_mbids_by_spotify_ids(["id1", "id1", "id2", "id2"])

        # Verify the query was called with deduplicated list
        mock_client.query.assert_called_once()
        call_args = mock_client.query.call_args
        config = call_args[1]["job_config"]
        # ArrayQueryParameter uses .values not .value
        params = {p.name: p.values for p in config.query_parameters}
        assert len(params["spotify_ids"]) == 2

    @patch("karaoke_decide.services.bigquery_catalog.bigquery.Client")
    def test_lookup_mbids_by_names_handles_exception(self, mock_client_class: MagicMock) -> None:
        """Test that exception is handled gracefully."""
        mock_client = mock_client_class.return_value
        mock_client.query.side_effect = Exception("BigQuery error")

        service = BigQueryCatalogService()
        result = service.lookup_mbids_by_names(["Radiohead"])

        assert result == {}

    @patch("karaoke_decide.services.bigquery_catalog.bigquery.Client")
    def test_lookup_mbids_by_spotify_ids_handles_exception(self, mock_client_class: MagicMock) -> None:
        """Test that exception is handled gracefully."""
        mock_client = mock_client_class.return_value
        mock_client.query.side_effect = Exception("BigQuery error")

        service = BigQueryCatalogService()
        result = service.lookup_mbids_by_spotify_ids(["spotify123"])

        assert result == {}
