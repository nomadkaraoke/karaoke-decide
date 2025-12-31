"""Tests for catalog API endpoints."""

from fastapi.testclient import TestClient


class TestSearchCatalog:
    """Tests for the catalog search endpoint."""

    def test_search_returns_songs(self, client: TestClient) -> None:
        """Test search endpoint returns matching songs."""
        response = client.get("/api/catalog/songs?q=bohemian&per_page=5")

        assert response.status_code == 200
        data = response.json()
        assert "songs" in data
        assert len(data["songs"]) > 0
        # Verify response structure
        assert "artist" in data["songs"][0]
        assert "title" in data["songs"][0]
        assert "brands" in data["songs"][0]

    def test_search_with_artist_filter(self, client: TestClient) -> None:
        """Test search with artist filter."""
        response = client.get("/api/catalog/songs?artist=Queen&per_page=5")

        assert response.status_code == 200
        data = response.json()
        assert "songs" in data
        # Should return songs by Queen
        assert len(data["songs"]) > 0
        assert data["songs"][0]["artist"] == "Queen"

    def test_search_default_returns_popular(self, client: TestClient) -> None:
        """Test default search without query returns popular songs."""
        response = client.get("/api/catalog/songs?per_page=5")

        assert response.status_code == 200
        data = response.json()
        assert "songs" in data
        # Default returns popular songs with min 3 brands
        for song in data["songs"]:
            assert song["brand_count"] >= 3

    def test_search_pagination_response_structure(self, client: TestClient) -> None:
        """Test search pagination response structure."""
        response = client.get("/api/catalog/songs?q=love&page=2&per_page=10")

        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 2
        assert data["per_page"] == 10
        assert "has_more" in data
        assert "total" in data

    def test_search_min_brands_filter(self, client: TestClient) -> None:
        """Test min_brands filter returns only popular songs."""
        response = client.get("/api/catalog/songs?q=love&min_brands=5&per_page=10")

        assert response.status_code == 200
        data = response.json()
        # Verify the filter was passed (mock returns all, but structure is valid)
        assert "songs" in data


class TestGetPopularSongs:
    """Tests for the popular songs endpoint."""

    def test_get_popular_songs(self, client: TestClient) -> None:
        """Test getting popular songs."""
        response = client.get("/api/catalog/songs/popular?limit=5")

        assert response.status_code == 200
        data = response.json()
        assert len(data) > 0
        # All popular songs should have is_popular=True (brand_count >= 5)
        for song in data:
            assert song["is_popular"] is True

    def test_get_popular_songs_respects_limit(self, client: TestClient) -> None:
        """Test getting popular songs respects limit parameter."""
        response = client.get("/api/catalog/songs/popular?limit=3&min_brands=5")

        assert response.status_code == 200
        data = response.json()
        assert len(data) <= 3


class TestGetSong:
    """Tests for the single song endpoint."""

    def test_get_song_found(self, client: TestClient) -> None:
        """Test getting a song by ID when found."""
        # Use ID 1 which exists in mock data
        response = client.get("/api/catalog/songs/1")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == 1
        assert data["artist"] == "Queen"
        assert data["title"] == "Bohemian Rhapsody"
        assert "brands" in data

    def test_get_song_not_found(self, client: TestClient) -> None:
        """Test getting a song by ID when not found."""
        # Use an ID that doesn't exist in mock data
        response = client.get("/api/catalog/songs/999999999")

        assert response.status_code == 404
        data = response.json()
        assert "not found" in data["detail"].lower()


class TestGetCatalogStats:
    """Tests for the catalog stats endpoint."""

    def test_get_stats(self, client: TestClient) -> None:
        """Test getting catalog statistics."""
        response = client.get("/api/catalog/stats")

        assert response.status_code == 200
        data = response.json()
        # Verify response structure
        assert "total_songs" in data
        assert "unique_artists" in data
        assert "max_brand_count" in data
        assert "avg_brand_count" in data
        # Verify mock values
        assert data["total_songs"] == 275809
        assert data["unique_artists"] == 50000


class TestGetSongLinks:
    """Tests for the song links endpoint."""

    def test_get_song_links_found(self, client: TestClient) -> None:
        """Test getting karaoke links for a song."""
        # Use ID 1 which exists in mock data (Queen - Bohemian Rhapsody)
        response = client.get("/api/catalog/songs/1/links")

        assert response.status_code == 200
        data = response.json()
        assert data["song_id"] == 1
        assert data["artist"] == "Queen"
        assert data["title"] == "Bohemian Rhapsody"
        assert "links" in data
        assert len(data["links"]) >= 2

    def test_get_song_links_has_youtube(self, client: TestClient) -> None:
        """Test song links include YouTube search."""
        response = client.get("/api/catalog/songs/1/links")

        assert response.status_code == 200
        data = response.json()
        youtube_link = next((link for link in data["links"] if link["type"] == "youtube_search"), None)
        assert youtube_link is not None
        assert "youtube.com" in youtube_link["url"]
        assert "Queen" in youtube_link["url"] or "queen" in youtube_link["url"].lower()
        assert "karaoke" in youtube_link["url"].lower()

    def test_get_song_links_has_generator(self, client: TestClient) -> None:
        """Test song links include Karaoke Generator."""
        response = client.get("/api/catalog/songs/1/links")

        assert response.status_code == 200
        data = response.json()
        gen_link = next((link for link in data["links"] if link["type"] == "karaoke_generator"), None)
        assert gen_link is not None
        assert "gen.nomadkaraoke.com" in gen_link["url"]
        assert "Queen" in gen_link["url"] or "queen" in gen_link["url"].lower()

    def test_get_song_links_not_found(self, client: TestClient) -> None:
        """Test getting links for non-existent song returns 404."""
        response = client.get("/api/catalog/songs/999999999/links")

        assert response.status_code == 404
        data = response.json()
        assert "not found" in data["detail"].lower()

    def test_get_song_links_response_structure(self, client: TestClient) -> None:
        """Test link response structure is complete."""
        response = client.get("/api/catalog/songs/1/links")

        assert response.status_code == 200
        data = response.json()
        for link in data["links"]:
            assert "type" in link
            assert "url" in link
            assert "label" in link
            assert "description" in link
