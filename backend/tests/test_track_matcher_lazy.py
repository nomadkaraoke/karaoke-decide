"""TrackMatcher builds its BigQuery catalog service lazily, once, even under thread contention."""

import threading
from unittest.mock import patch

from backend.services import track_matcher
from backend.services.track_matcher import TrackMatcher


def test_catalog_service_created_once_under_contention() -> None:
    created: list[object] = []
    gate = threading.Barrier(8)

    def fake_factory() -> object:
        created.append(object())
        return created[-1]

    matcher = TrackMatcher()
    results: list[object] = []

    def worker() -> None:
        gate.wait()
        results.append(matcher.catalog_service)

    with patch.object(track_matcher, "BigQueryCatalogService", fake_factory):
        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert len(created) == 1
    assert all(r is created[0] for r in results)
