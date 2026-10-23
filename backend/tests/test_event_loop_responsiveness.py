"""The API's event loop stays free while sync / import work runs on the same instance.

Sync (Cloud Tasks -> ``/internal/sync/process``) and the import routes run on the API service, so
anything blocking they do on the event loop stalls every other request on that instance. These
tests run the heavy paths against slow fakes (a blocking BigQuery round trip, thousands of tracks
of CPU work) and check that a concurrent ``/api/health`` still answers within 200 ms.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from backend.services.track_matcher import TrackMatcher
from karaoke_decide.services.bigquery_catalog import SongResult

BQ_ROUND_TRIP_S = 1.0  # a blocking BigQuery query, like the 11 s artist lookup seen in prod
HEALTH_BUDGET_S = 0.2
LEAD_S = 0.05


def _tracks(n: int) -> list[dict[str, Any]]:
    """``n`` distinct synthetic tracks with the suffixes the normaliser strips."""
    return [
        {
            "artist": f"Fixture Artist {i % 900} feat. Guest {i % 7}",
            "title": f"Fixture Song {i} (Remastered 2011) [Live]",
            "playcount": 1 + i % 40,
        }
        for i in range(n)
    ]


async def _health_latency_during(work: Callable[[], Coroutine[Any, Any, Any]]) -> tuple[float, Any]:
    """Start ``work``, then time a ``GET /api/health`` sent while it runs; return (seconds, result)."""
    from backend.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        started = time.perf_counter()
        task = asyncio.create_task(work())
        await asyncio.sleep(LEAD_S)  # let the work get going, then probe
        response = await client.get("/api/health")
        # Anything the work blocks the loop for delays the wake-up or the probe itself.
        elapsed = time.perf_counter() - started - LEAD_S
        probed_while_running = not task.done()
    assert response.status_code == 200
    result = await task
    assert elapsed >= HEALTH_BUDGET_S or probed_while_running, "the work finished before the probe; make it slower"
    return elapsed, result


def _slow_bigquery(result: Any) -> Callable[..., Any]:
    """A stand-in for a blocking BigQuery call that returns ``result``."""

    def call(*args: Any, **kwargs: Any) -> Any:
        time.sleep(BQ_ROUND_TRIP_S)
        return result

    return call


def _slow_bq_matcher() -> TrackMatcher:
    """A matcher whose catalog.db isn't loaded, so it falls back to a (blocking) BigQuery query."""
    catalog = MagicMock()

    def batch_match_tracks(pairs: list[tuple[str, str]]) -> dict[tuple[str, str], SongResult]:
        time.sleep(BQ_ROUND_TRIP_S)
        a, t = pairs[0]
        return {(a, t): SongResult(id=1, artist=a, title=t, brands="x", brand_count=1)}

    catalog.batch_match_tracks.side_effect = batch_match_tracks
    return TrackMatcher(catalog, catalog_lookup=None)


async def test_health_answers_while_5000_tracks_match_via_bigquery() -> None:
    matcher = _slow_bq_matcher()
    tracks = _tracks(5000)

    elapsed, matched = await _health_latency_during(lambda: matcher.batch_match_tracks(tracks))

    assert elapsed < HEALTH_BUDGET_S, f"/api/health took {elapsed:.3f}s during a 5,000-track match"
    assert len(matched) == 5000
    assert sum(1 for m in matched if m.catalog_song is not None) == 1


async def test_health_answers_while_tracks_match_via_catalog_db() -> None:
    lookup = MagicMock()
    lookup.store.status = "ready"

    def match(artist: str, title: str) -> None:
        time.sleep(0.0002)  # one indexed SQLite lookup
        return None

    lookup.match.side_effect = match
    matcher = TrackMatcher(MagicMock(), catalog_lookup=lookup)

    elapsed, matched = await _health_latency_during(lambda: matcher.batch_match_tracks(_tracks(5000)))

    assert elapsed < HEALTH_BUDGET_S
    assert len(matched) == 5000


async def test_match_single_track_search_runs_off_the_loop() -> None:
    catalog = MagicMock()
    catalog.search_songs.side_effect = _slow_bigquery([])
    matcher = TrackMatcher(catalog, catalog_lookup=None)

    elapsed, result = await _health_latency_during(lambda: matcher.match_single_track("Fixture Artist", "Song"))

    assert elapsed < HEALTH_BUDGET_S
    assert result.catalog_song is None


async def test_health_answers_during_lastfm_artist_metadata_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Last.fm artist step's BigQuery metadata lookup (11 s for 995 artists in prod)."""
    from backend.services.sync_service import SyncService

    firestore = MagicMock()
    firestore.set_document = AsyncMock()
    lastfm = MagicMock()
    lastfm.get_all_top_artists = AsyncMock(
        return_value=[{"name": f"Fixture Artist {i}", "playcount": "3", "rank": i + 1} for i in range(1000)]
    )
    service = SyncService(MagicMock(), firestore, MagicMock(), lastfm_client=lastfm, track_matcher=MagicMock())
    monkeypatch.setattr(service, "_get_spotify_metadata_for_artists", _slow_bigquery({}))

    elapsed, stored = await _health_latency_during(
        lambda: service._fetch_and_store_lastfm_artists("fixture-user", "fixture-user")
    )

    assert elapsed < HEALTH_BUDGET_S
    assert stored == 1000


async def test_health_answers_during_a_20000_track_export_chunk() -> None:
    """A full Spotify export chunk: route-side aggregation plus ingest's matching and row building."""
    from backend.api.routes.imports_export import SpotifyExportRequest, _ingest_chunk
    from backend.services.import_ingest_service import ImportIngestService

    body = SpotifyExportRequest.model_validate(
        {
            "generated_at": "2026-10-01",
            "chunk_index": 0,
            "chunk_count": 1,
            "kind": "extended",
            "tracks": [
                {
                    "artist": t["artist"],
                    "title": t["title"],
                    "ms_played": 60000,
                    "plays": 2,
                    "first_ts": 1,
                    "last_ts": 2,
                }
                for t in _tracks(20000)
            ],
            "liked": [{"artist": f"Fixture Artist {i}", "title": f"Liked {i}"} for i in range(5000)],
            "playlists": [],
        }
    )
    db = MagicMock()
    db.get_document = AsyncMock(return_value=None)
    db.get_many = AsyncMock(return_value={})
    db.batch_set = AsyncMock()
    db.transform_document_atomically = AsyncMock(side_effect=lambda c, i, fn: fn(None))
    ingest = ImportIngestService(db, track_matcher=_slow_bq_matcher(), reader=lambda: None)

    elapsed, response = await _health_latency_during(
        lambda: _ingest_chunk(ingest, "fixture-user", "spotify_export", "extended:2026-10-01", body)
    )

    assert elapsed < HEALTH_BUDGET_S, f"/api/health took {elapsed:.3f}s during a 20,000-track chunk"
    assert response.songs_added == 25000


async def test_catalog_search_route_runs_bigquery_off_the_loop(mock_catalog_service: MagicMock) -> None:
    """Public catalog routes still query BigQuery synchronously: they must not hold the loop."""
    from unittest.mock import patch

    from backend.main import app

    mock_catalog_service.search_songs.side_effect = _slow_bigquery([])

    async def search() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            return await c.get("/api/catalog/songs", params={"q": "fixture"})

    with patch("backend.api.routes.catalog.get_catalog_service", return_value=mock_catalog_service):
        elapsed, response = await _health_latency_during(search)

    assert elapsed < HEALTH_BUDGET_S
    assert response.status_code == 200
