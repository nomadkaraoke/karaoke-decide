"""Search-service fixtures: the A2/A3 fixture indexes built once into a tmp dir."""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from karaoke_decide.song_search.availability import build_availability_db
from karaoke_decide.song_search.build_song_id import build as build_song_id

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "song_search"
RUN_ID = "20261018-000000-0123456789abcdef"


def _gz(src: Path, dst: Path) -> Path:
    with open(src, "rb") as f, gzip.open(dst, "wb") as g:
        shutil.copyfileobj(f, g)
    return dst


def file_entry(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


@pytest.fixture(scope="session")
def index_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """song_id.db + availability.db + offline.db.gz + manifest.json, as the build job publishes them."""
    d = tmp_path_factory.mktemp("song-index")
    build_song_id([FIXTURES / "songs_fixture.tsv"], d / "song_id.db", run="20261018-000000")
    build_availability_db(
        _gz(FIXTURES / "kn_full_fixture.json", d / "full.json.gz"),
        _gz(FIXTURES / "kn_community_fixture.json", d / "community.json.gz"),
        d / "song_id.db",
        d / "availability.db",
        run="20261018-000000",
    )
    (d / "full.json.gz").unlink()
    (d / "community.json.gz").unlink()
    _gz(FIXTURES / "offline_fixture.db", d / "offline.db.gz")
    manifest = {
        "run": "20261018-000000",
        "token": "0123456789abcdef",
        "schema": 3,
        "normalizer": 3,
        "offline_schema": 1,
        "files": {name: file_entry(d / name) for name in ("song_id.db", "availability.db", "offline.db.gz")},
    }
    (d / "manifest.json").write_text(json.dumps(manifest))
    return d


@pytest.fixture
def client(index_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from backend.search.app import create_search_app

    monkeypatch.setenv("SONG_INDEX_LOCAL_DIR", str(index_dir))
    monkeypatch.delenv("SONG_INDEX_RUN", raising=False)
    with TestClient(create_search_app()) as c:
        yield c
