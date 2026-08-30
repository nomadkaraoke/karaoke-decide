"""Tests for the flacfetch sourceability client.

HTTP is mocked by patching ``httpx.AsyncClient`` (matching the repo's
``test_lastfm.py`` convention) — respx 0.21 is incompatible with httpx 0.28.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from karaoke_decide.core.exceptions import ExternalServiceError
from karaoke_decide.services.flacfetch import FlacfetchClient

BASE = "https://flac.example.com"


def _client() -> FlacfetchClient:
    return FlacfetchClient(BASE, "key", timeout=5.0, max_retries=2)


def _result(**kw):
    base = {
        "provider": "RED",
        "is_lossless": True,
        "seeders": 100,
        "match_score": 1.0,
        "release_type": "Album",
        "year": 2010,
        "quality_data": {"format": "FLAC", "bit_depth": 16, "sample_rate": 44100},
    }
    base.update(kw)
    return base


def _resp(status, json_data=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_data
    return r


def _patch_post(responses):
    mock_post = AsyncMock(side_effect=responses)
    patcher = patch("httpx.AsyncClient")
    mock_client = patcher.start()
    mock_client.return_value.__aenter__.return_value.post = mock_post
    return patcher, mock_post


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    monkeypatch.setattr("karaoke_decide.services.flacfetch.asyncio.sleep", AsyncMock())


class TestBestFlac:
    def test_picks_high_quality_flac(self):
        hit = FlacfetchClient.best_flac([_result()])
        assert hit is not None
        assert hit.provider == "RED" and hit.seeders == 100

    def test_rejects_lossy(self):
        assert FlacfetchClient.best_flac([_result(is_lossless=False)]) is None

    def test_rejects_non_red_ops(self):
        assert FlacfetchClient.best_flac([_result(provider="YouTube")]) is None

    def test_rejects_low_seeders(self):
        assert FlacfetchClient.best_flac([_result(seeders=0)], min_seeders=1) is None

    def test_rejects_low_match_score(self):
        assert FlacfetchClient.best_flac([_result(match_score=0.5)]) is None

    def test_ranks_by_match_then_seeders(self):
        results = [
            _result(seeders=10, match_score=1.0),
            _result(seeders=500, match_score=0.9),
        ]
        hit = FlacfetchClient.best_flac(results)
        assert hit.seeders == 10  # higher match_score wins


class TestSearch:
    async def test_success(self):
        patcher, _ = _patch_post([_resp(200, {"results": [_result()]})])
        try:
            results = await _client().search("Pendulum", "Slam")
        finally:
            patcher.stop()
        assert len(results) == 1

    async def test_404_is_empty(self):
        patcher, _ = _patch_post([_resp(404)])
        try:
            assert await _client().search("X", "Y") == []
        finally:
            patcher.stop()

    async def test_retries_then_succeeds(self):
        patcher, mock_post = _patch_post([_resp(503), _resp(200, {"results": []})])
        try:
            assert await _client().search("X", "Y") == []
        finally:
            patcher.stop()
        assert mock_post.await_count == 2

    async def test_persistent_error_raises(self):
        patcher, _ = _patch_post([_resp(503), _resp(503)])
        try:
            with pytest.raises(ExternalServiceError):
                await _client().search("X", "Y")
        finally:
            patcher.stop()
