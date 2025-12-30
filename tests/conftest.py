"""Shared test fixtures for Karaoke Decide."""

from unittest.mock import MagicMock, patch

import pytest

from karaoke_decide.services.bigquery_catalog import SongResult


@pytest.fixture
def mock_bigquery_client():
    """Mock BigQuery client for testing."""
    with patch("karaoke_decide.services.bigquery_catalog.bigquery.Client") as mock_client:
        yield mock_client


@pytest.fixture
def sample_song_results():
    """Sample SongResult objects for testing."""
    return [
        SongResult(
            id=1,
            artist="Queen",
            title="Bohemian Rhapsody",
            brands="karafun,karaoke-version,singa",
            brand_count=3,
        ),
        SongResult(
            id=2,
            artist="Journey",
            title="Don't Stop Believin'",
            brands="karafun,karaoke-version,singa,karaoke-nerds,lucky-voice",
            brand_count=5,
        ),
        SongResult(
            id=3,
            artist="Adele",
            title="Rolling in the Deep",
            brands="karafun,karaoke-version",
            brand_count=2,
        ),
    ]


@pytest.fixture
def mock_bigquery_results(sample_song_results):
    """Create mock BigQuery query results."""

    def create_mock_row(song: SongResult):
        row = MagicMock()
        row.id = song.id
        row.artist = song.artist
        row.title = song.title
        row.brands = song.brands
        row.brand_count = song.brand_count
        return row

    return [create_mock_row(s) for s in sample_song_results]
