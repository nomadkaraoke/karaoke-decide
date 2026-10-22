"""Main API cold start: no KaraokeNerds preload, no heavy client imports, early catalog load, /api/ready."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import get_backend_settings
from backend.services import catalog_store
from backend.tests.test_catalog_store import FakeClient
from karaoke_decide.catalog import fixtures

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def fresh_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """A new store singleton, an empty fake GCS and fresh settings for each test."""
    monkeypatch.setattr(catalog_store, "_store", None)
    monkeypatch.setattr(catalog_store, "_storage_client", lambda: FakeClient({}))
    for var in ("CATALOG_LOCAL_DIR", "SONG_INDEX_LOCAL_DIR"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CATALOG_DIR", str(tmp_path / "dl"))
    get_backend_settings.cache_clear()
    yield
    get_backend_settings.cache_clear()


def test_startup_without_catalog(fresh_store: None) -> None:
    from backend.main import app

    with TestClient(app) as client:
        ready = client.get("/api/ready")
        assert ready.status_code == 200
        assert ready.json() == {"ok": True, "catalog": False, "availability": False, "run": None}
        assert client.get("/api/health").status_code == 200


def test_startup_with_local_catalog(
    fresh_store: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    index_dir: Path,
) -> None:
    fixtures.build(tmp_path)
    monkeypatch.setenv("CATALOG_LOCAL_DIR", str(tmp_path))
    monkeypatch.setenv("SONG_INDEX_LOCAL_DIR", str(index_dir))
    get_backend_settings.cache_clear()
    from backend.main import app

    with TestClient(app) as client:
        assert client.get("/api/ready").json() == {"ok": True, "catalog": True, "availability": True, "run": "fixture"}


def test_lifespan_survives_load_that_overruns(fresh_store: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Even if ``load`` itself overran its own budget, the lifespan's outer timeout lets the app start."""
    import time

    from backend import main

    monkeypatch.setenv("CATALOG_LOAD_TIMEOUT_S", "0.1")
    get_backend_settings.cache_clear()
    monkeypatch.setattr(main, "LIFESPAN_SLACK_S", 0.2)
    monkeypatch.setattr(main, "PRELOAD_MODULES", ())  # this test times the catalog bound only
    monkeypatch.setattr(catalog_store.CatalogStore, "load", lambda self, settings, **kw: time.sleep(1))

    started = time.monotonic()
    with TestClient(main.app) as client:
        assert time.monotonic() - started < 0.9
        assert client.get("/api/ready").json()["catalog"] is False


# Client libraries importing backend.main must not pull in (cold start): the services import
# them lazily (karaoke_decide.core.lazy_import) and the lifespan preloads them off the critical
# path. Measured in the production image: they were ~1/3 of the app's import time.
HEAVY_MODULES = (
    "google.cloud.bigquery",
    "google.cloud.storage",
    "google.cloud.firestore",
    "google.cloud.firestore_v1",
    "google.cloud.tasks_v2",
    "google.api_core",
    "grpc",
    "ytmusicapi",
    "jose.jwt",
    "cryptography",
)


def _imported_after(module: str) -> set[str]:
    code = f"import json, sys; import {module}; print(json.dumps(sorted(sys.modules)))"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO, check=True)
    return set(json.loads(out.stdout.strip().splitlines()[-1]))


def test_main_import_does_not_import_heavy_client_libraries() -> None:
    assert not {m for m in HEAVY_MODULES if m in _imported_after("backend.main")}


def test_main_preloads_what_the_services_import_lazily() -> None:
    from backend import main

    assert set(main.PRELOAD_MODULES) <= {
        "google.cloud.firestore",
        "google.api_core.exceptions",
        "jose.jwt",
        "google.cloud.tasks_v2",
        "google.protobuf.timestamp_pb2",
    }
    assert {"google.cloud.firestore", "jose.jwt"} <= set(main.PRELOAD_MODULES)


def test_serve_starts_the_catalog_load_before_importing_the_app(tmp_path: Path, index_dir: Path) -> None:
    """backend.serve (the Dockerfile CMD) kicks off the download, then imports backend.main."""
    fixtures.build(tmp_path)
    code = """
import sys
from backend.services import catalog_store
seen = []
original = catalog_store.CatalogStore.start_load
def spy(self, settings):
    seen.append("backend.main" in sys.modules)
    original(self, settings)
catalog_store.CatalogStore.start_load = spy
import backend.serve
from backend.config import get_backend_settings
store = catalog_store.get_catalog_store()
store.load_at_startup(get_backend_settings())
print(seen, store.status, backend.serve.app is sys.modules["backend.main"].app)
"""
    env = {**os.environ, "CATALOG_LOCAL_DIR": str(tmp_path), "SONG_INDEX_LOCAL_DIR": str(index_dir)}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO, env=env, check=True)
    assert out.stdout.strip().splitlines()[-1] == "[False] ready True", out.stderr


def test_dockerfile_runs_the_serve_entrypoint() -> None:
    dockerfile = (REPO / "Dockerfile").read_text()
    assert 'CMD ["uvicorn", "backend.serve:app"' in dockerfile
    # Bytecode is compiled at build time: appuser can't write __pycache__ to site-packages.
    assert dockerfile.count("python -m compileall") == 2


def test_main_does_not_preload_karaokenerds() -> None:
    source = (REPO / "backend" / "main.py").read_text()
    assert "load_from_bigquery" not in source
    assert "BigQueryCatalogService" not in source


def test_test_support_mount_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    """Task C1's router mounts only with decide_test_api on, never in production."""
    from types import SimpleNamespace

    from fastapi import APIRouter, FastAPI

    from backend import main

    router = APIRouter()

    @router.get("/ping")
    def ping() -> dict[str, bool]:
        return {"ok": True}

    imported: list[str] = []

    def fake_import(name: str) -> SimpleNamespace:
        imported.append(name)
        return SimpleNamespace(router=router)

    monkeypatch.setattr(main.importlib, "import_module", fake_import)

    def paths(settings: SimpleNamespace) -> set[str]:
        app = FastAPI()
        main._mount_test_support(app, settings)  # type: ignore[arg-type]
        return {getattr(r, "path", "") for r in app.routes}

    assert "/api/test/ping" not in paths(SimpleNamespace(decide_test_api=False, is_production=False))
    assert "/api/test/ping" not in paths(SimpleNamespace(decide_test_api=True, is_production=True))
    assert "/api/test/ping" in paths(SimpleNamespace(decide_test_api=True, is_production=False))
    assert imported == ["backend.api.routes.test_support"]


def test_catalog_pointer_name_matches_publisher() -> None:
    from karaoke_decide.catalog import publish

    assert catalog_store.CATALOG_LATEST == publish.CATALOG_LATEST
