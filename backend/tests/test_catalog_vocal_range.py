"""GET /api/catalog/songs/{song_key}/vocal-range (public) on the fixture catalog.db."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_catalog_store_dep
from backend.main import app
from backend.services.catalog_store import CatalogUnavailable
from karaoke_decide.catalog import fixtures
from karaoke_decide.catalog.reader import CatalogReader


class _Store:
    def __init__(self, reader: CatalogReader | None) -> None:
        self._reader = reader

    def require(self) -> CatalogReader:
        if self._reader is None:
            raise CatalogUnavailable("warming_up")
        return self._reader


@pytest.fixture(scope="module")
def fixture_reader(tmp_path_factory: pytest.TempPathFactory) -> Iterator[CatalogReader]:
    d: Path = tmp_path_factory.mktemp("vr_catalog")
    fixtures.build(d)
    r = CatalogReader(d / "catalog.db", None)
    yield r
    r.close()


@pytest.fixture
def client() -> Iterator[Any]:
    def make(reader: CatalogReader | None) -> TestClient:
        app.dependency_overrides[get_catalog_store_dep] = lambda: _Store(reader)
        return TestClient(app)

    yield make
    app.dependency_overrides.clear()


def _url(key: str) -> str:
    return f"/api/catalog/songs/{key}/vocal-range"


def test_vocal_range_endpoint(client: Any, fixture_reader: CatalogReader) -> None:
    c = client(fixture_reader)

    hit = c.get(_url("muse|uprising"))
    assert hit.status_code == 200
    assert hit.json() == {
        "song_key": "muse|uprising",
        "range": {
            "low": 48.0,
            "high": 70.0,
            "p10": 52.0,
            "p50": 59.0,
            "p90": 66.0,
            "tier": "notes",
            "octave_confidence": 0.92,
            "is_duet": False,
        },
    }

    duet = c.get(_url("brandy and monica|the boy is mine")).json()["range"]
    assert duet["is_duet"] is True

    rough = c.get(_url("muse|hysteria")).json()  # no own range: Muse's artist range
    assert rough["range"] == {
        "low": 47.6,
        "high": 68.0,
        "p10": 51.5,
        "p50": 58.1,
        "p90": 64.5,
        "tier": "rough",
        "octave_confidence": None,
        "is_duet": None,
    }

    unknown = c.get(_url("nobody|nothing"))
    assert unknown.status_code == 200 and unknown.json() == {"song_key": "nobody|nothing", "range": None}

    no_data = fixture_reader.conn().execute("SELECT song_key FROM songs WHERE vr_tier IS NULL LIMIT 1").fetchone()[0]
    assert c.get(_url(no_data)).json() == {"song_key": no_data, "range": None}


def test_vocal_range_url_encoded_key(client: Any, fixture_reader: CatalogReader) -> None:
    r = client(fixture_reader).get("/api/catalog/songs/muse%7Cstarlight/vocal-range")
    assert r.status_code == 200 and r.json()["range"]["tier"] == "measured"


def test_vocal_range_warming_up(client: Any) -> None:
    r = client(None).get(_url("muse|starlight"))
    assert r.status_code == 503
    assert r.json() == {"detail": "warming_up"}
    assert r.headers["retry-after"] == "30"


def test_vocal_range_is_public(client: Any, fixture_reader: CatalogReader) -> None:
    # No auth override and no Authorization header: still 200.
    assert client(fixture_reader).get(_url("muse|starlight")).status_code == 200


def test_reader_vocal_range(fixture_reader: CatalogReader) -> None:
    assert fixture_reader.has_vocal_range is True
    vr = fixture_reader.vocal_range("muse|starlight")
    assert vr is not None and vr.tier == "measured" and vr.is_duet is False
    assert fixture_reader.vocal_range("") is None


def test_vocal_range_endpoint_on_schema1_catalog(client: Any, fixture_reader: CatalogReader, tmp_path: Path) -> None:
    import shutil
    import sqlite3

    path = tmp_path / "catalog_v1.db"
    shutil.copy(fixture_reader.catalog_path, path)
    conn = sqlite3.connect(path)
    for col in ("vr_low", "vr_high", "vr_p10", "vr_p50", "vr_p90", "vr_tier", "vr_octave_conf", "vr_duet"):
        conn.execute(f"ALTER TABLE songs DROP COLUMN {col}")
    conn.commit()
    conn.close()
    old = CatalogReader(path, None)
    try:
        r = client(old).get(_url("muse|starlight"))
        assert r.status_code == 200 and r.json() == {"song_key": "muse|starlight", "range": None}
    finally:
        old.close()


def test_has_vocal_range_needs_every_column(fixture_reader: CatalogReader, tmp_path: Path) -> None:
    import shutil
    import sqlite3

    for missing in ("vr_octave_conf", "vr_duet", "vr_tier"):
        path = tmp_path / f"partial_{missing}.db"
        shutil.copy(fixture_reader.catalog_path, path)
        conn = sqlite3.connect(path)
        conn.execute(f"ALTER TABLE songs DROP COLUMN {missing}")
        conn.commit()
        conn.close()
        r = CatalogReader(path, None)
        try:
            assert r.has_vocal_range is False, missing
            assert r.vocal_range("muse|starlight") is None
        finally:
            r.close()
