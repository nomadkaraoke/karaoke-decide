"""ListenBrainz service: artist lookups come from catalog.db, never musicbrainz.org."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from backend.config import BackendSettings
from backend.services.catalog_store import CatalogUnavailable
from backend.services.listenbrainz_service import ListenBrainzService


class _Reader:
    def artist_mbid(self, name: str) -> str | None:
        return "mbid-muse" if name.lower() == "muse" else None


class _Store:
    def __init__(self, reader: _Reader | None) -> None:
        self._reader = reader

    def require(self) -> _Reader:
        if self._reader is None:
            raise CatalogUnavailable("warming_up")
        return self._reader


def _service(reader: _Reader | None) -> ListenBrainzService:
    firestore = MagicMock()
    firestore.get_document = AsyncMock(return_value=None)
    firestore.set_document = AsyncMock(return_value=None)
    return ListenBrainzService(
        BackendSettings(environment="development", google_cloud_project="test"),
        firestore,
        catalog_store=_Store(reader),  # type: ignore[arg-type]
    )


@pytest.fixture
def urls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []

    async def fake_get(self: Any, url: str, **kwargs: Any) -> httpx.Response:
        seen.append(url)
        if "musicbrainz.org" in url:
            raise AssertionError(f"live MusicBrainz call: {url}")
        return httpx.Response(
            200,
            json=[{"artist_mbid": "mbid-other", "name": "Coldplay", "score": 0.7}],
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    return seen


@pytest.mark.asyncio
async def test_no_musicbrainz_http(urls: list[str]) -> None:
    svc = _service(_Reader())
    similar = await svc.get_similar_artists_by_name("Muse")
    assert [s["name"] for s in similar] == ["Coldplay"]
    assert urls and not any("musicbrainz.org" in u for u in urls)
    assert not hasattr(ListenBrainzService, "MUSICBRAINZ_API_URL")


@pytest.mark.asyncio
async def test_unknown_artist_makes_no_http_call(urls: list[str]) -> None:
    assert await _service(_Reader()).get_similar_artists_by_name("Nobody Known") == []
    assert urls == []


@pytest.mark.asyncio
async def test_catalog_warming_up_returns_none(urls: list[str]) -> None:
    svc = _service(None)
    assert await svc.search_artist_mbid("Muse") is None
    assert await svc.get_similar_artists_by_name("Muse") == []
    assert urls == []
