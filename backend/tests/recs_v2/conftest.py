"""recs v2 fixtures: the fixture catalog.db plus the search fixture availability.db."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from karaoke_decide.catalog import fixtures
from karaoke_decide.catalog.reader import CatalogReader


@pytest.fixture(scope="session")
def catalog_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("recs_v2_catalog")
    fixtures.build(d)
    return d


@pytest.fixture
def reader(catalog_dir: Path, index_dir: Path) -> Iterator[CatalogReader]:
    r = CatalogReader(catalog_dir / "catalog.db", index_dir / "availability.db")
    yield r
    r.close()


@pytest.fixture
def reader_no_av(catalog_dir: Path) -> Iterator[CatalogReader]:
    r = CatalogReader(catalog_dir / "catalog.db", None)
    yield r
    r.close()
