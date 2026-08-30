"""Tests for the LRCLIB lyrics client.

HTTP is mocked by patching ``httpx.AsyncClient`` (matching the repo's
``test_lastfm.py`` convention) — respx 0.21 is incompatible with httpx 0.28.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from karaoke_decide.core.exceptions import ExternalServiceError
from karaoke_decide.services.lrclib import LrclibClient


def _client() -> LrclibClient:
    return LrclibClient("test-agent", timeout=5.0, max_retries=2)


def _patch_get(responses):
    """Patch httpx.AsyncClient.get to yield the given responses in order."""
    mock_get = AsyncMock(side_effect=responses)
    patcher = patch("httpx.AsyncClient")
    mock_client = patcher.start()
    mock_client.return_value.__aenter__.return_value.get = mock_get
    return patcher, mock_get


def _resp(status, json_data=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_data
    return r


class TestBestLyrics:
    async def test_matches_artist_and_title(self):
        patcher, _ = _patch_get(
            [
                _resp(
                    200,
                    [
                        {
                            "artistName": "Pendulum",
                            "trackName": "Slam",
                            "plainLyrics": "long lyrics here",
                            "syncedLyrics": "",
                            "instrumental": False,
                        }
                    ],
                )
            ]
        )
        try:
            result = await _client().best_lyrics("Pendulum", "Slam")
        finally:
            patcher.stop()
        assert result is not None
        assert result["plain"] == "long lyrics here"

    async def test_rejects_wrong_artist(self):
        patcher, _ = _patch_get(
            [
                _resp(
                    200,
                    [
                        {
                            "artistName": "Someone Else",
                            "trackName": "Slam",
                            "plainLyrics": "x",
                            "instrumental": False,
                        }
                    ],
                )
            ]
        )
        try:
            assert await _client().best_lyrics("Pendulum", "Slam") is None
        finally:
            patcher.stop()

    async def test_prefers_longest_text(self):
        patcher, _ = _patch_get(
            [
                _resp(
                    200,
                    [
                        {"artistName": "A", "trackName": "B", "plainLyrics": "short"},
                        {
                            "artistName": "A",
                            "trackName": "B",
                            "plainLyrics": "much longer text",
                        },
                    ],
                )
            ]
        )
        try:
            result = await _client().best_lyrics("A", "B")
        finally:
            patcher.stop()
        assert result["plain"] == "much longer text"

    async def test_404_returns_none(self):
        patcher, _ = _patch_get([_resp(404)])
        try:
            assert await _client().best_lyrics("A", "B") is None
        finally:
            patcher.stop()

    async def test_retries_5xx_then_raises(self, monkeypatch):
        monkeypatch.setattr(
            "karaoke_decide.services.lrclib.asyncio.sleep", AsyncMock()
        )
        patcher, mock_get = _patch_get([_resp(503), _resp(503)])
        try:
            with pytest.raises(ExternalServiceError):
                await _client().search("A", "B")
        finally:
            patcher.stop()
        assert mock_get.await_count == 2
