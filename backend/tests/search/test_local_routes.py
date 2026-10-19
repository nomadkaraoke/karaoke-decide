"""The ``/api/search/_local`` routes exist only for the local stack (SONG_INDEX_LOCAL_DIR)."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.search.app import create_search_app

URL = "/api/search/_local/offline.db.gz"


def test_local_offline_file_served_when_local_dir_set(client: TestClient, index_dir: Path) -> None:
    resp = client.get(URL)
    assert resp.status_code == 200
    assert resp.content == (index_dir / "offline.db.gz").read_bytes()
    assert resp.headers["content-type"] == "application/gzip"
    assert gzip.decompress(resp.content).startswith(b"SQLite format 3")


def test_local_offline_file_matches_manifest(client: TestClient) -> None:
    manifest = client.get("/api/search/offline-index").json()
    assert len(client.get(URL).content) == manifest["bytes"]


def test_local_route_absent_without_local_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SONG_INDEX_LOCAL_DIR", raising=False)
    monkeypatch.setenv("SONG_INDEX_RUN", "unused")  # the download would fail; health still answers
    app = create_search_app()
    assert not any("_local" in getattr(r, "path", "") for r in app.routes)
    with TestClient(app) as c:
        assert c.get(URL).status_code == 404


def test_local_route_404_when_dir_has_no_offline_file(
    tmp_path: Path, index_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("song_id.db", "availability.db"):
        (tmp_path / name).write_bytes((index_dir / name).read_bytes())
    (tmp_path / "manifest.json").write_text(json.dumps({"run": "x"}))
    monkeypatch.setenv("SONG_INDEX_LOCAL_DIR", str(tmp_path))
    with TestClient(create_search_app()) as c:
        assert c.get(URL).status_code == 404
