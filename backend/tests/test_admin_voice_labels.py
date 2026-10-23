"""POST /api/admin/voice/labels/import: access, matching, enqueue, idempotence."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_auth_service_dep, get_firestore, get_optional_user, get_settings
from backend.api.routes.admin_voice import LABELS_COLLECTION, get_voice_catalog, label_doc_id
from backend.config import BackendSettings
from backend.main import app
from backend.services.auth_service import AuthService
from backend.tests.test_karaoke_log_service import MemoryFirestore
from karaoke_decide.catalog import fixtures
from karaoke_decide.catalog.reader import CatalogReader
from karaoke_decide.voice_pool.queue import FETCH_COLLECTION, pool_id

TOKEN = "script-token-1"
EMAIL = "andrew@example.com"
USER_ID = "user_andrew"
URL = "/api/admin/voice/labels/import"
# In fixtures.build's catalog.db: community (adele|hello) and commercial-only (muse|starlight).
COMMUNITY = {"artist": "Adele", "title": "Hello", "good": "y", "notes": "good if warm"}
COMMERCIAL = {"artist": "Muse", "title": "Starlight", "good": "n", "notes": ""}
UNKNOWN = {"artist": "Nobody Known", "title": "Invented Tune", "good": "maybe", "notes": "needs practice"}


class Fs(MemoryFirestore):
    async def create_document(self, collection: str, doc_id: str, data: dict[str, Any]) -> bool:
        col = self.cols.setdefault(collection, {})
        if doc_id in col:
            return False
        col[doc_id] = dict(data)
        return True


@pytest.fixture
def reader(tmp_path: Path) -> Iterator[CatalogReader]:
    catalog = fixtures.build(tmp_path)
    av = tmp_path / "availability.db"
    db = sqlite3.connect(av)
    db.executescript(
        """
        CREATE TABLE songs(song_key TEXT PRIMARY KEY, artist TEXT, title TEXT, pop INT, version_count INT);
        CREATE TABLE versions(song_key TEXT, brand_code TEXT, brand_name TEXT, kind TEXT, youtube_url TEXT, rank INT);
        INSERT INTO songs VALUES ('adele|hello','Adele','Hello',1,2), ('muse|starlight','Muse','Starlight',1,1);
        INSERT INTO versions VALUES ('adele|hello','AB','Brand A','commercial',NULL,2),
                                    ('adele|hello','FB','Funbox','community','https://youtu.be/x',1),
                                    ('muse|starlight','CB','Brand C','commercial',NULL,1);
        """
    )
    db.commit()
    db.close()
    r = CatalogReader(catalog, av)
    yield r
    r.close()


@pytest.fixture
def fs() -> Fs:
    fs = Fs()
    fs.cols["decide_users"] = {
        hashlib.sha256(EMAIL.encode()).hexdigest(): {
            "user_id": USER_ID,
            "email": EMAIL,
            "is_guest": False,
            "created_at": "2026-10-01T00:00:00+00:00",
            "updated_at": "2026-10-01T00:00:00+00:00",
        }
    }
    return fs


@pytest.fixture
def client(fs: Fs, reader: CatalogReader) -> Iterator[TestClient]:
    settings = BackendSettings(
        environment="development",
        google_cloud_project="test-project",
        jwt_secret="test-jwt-secret-key-for-testing-only",
        admin_tokens=f" {TOKEN} , ,other-token",  # type: ignore[arg-type]  # env-style string, split by the validator
    )
    auth = AuthService(settings, fs, email_service=MagicMock())  # type: ignore[arg-type]
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_firestore] = lambda: fs
    app.dependency_overrides[get_auth_service_dep] = lambda: auth
    app.dependency_overrides[get_voice_catalog] = lambda: reader
    app.dependency_overrides[get_optional_user] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


def _body(*rows: dict[str, Any], **over: Any) -> dict[str, Any]:
    return {"email": EMAIL, "rows": list(rows), **over}


def _tok(token: str = TOKEN) -> dict[str, str]:
    return {"X-Admin-Token": token}


def test_admin_tokens_parsed_comma_separated() -> None:
    s = BackendSettings(admin_tokens=" a , ,b")  # type: ignore[arg-type]  # the validator splits env-style strings
    assert s.admin_tokens == ["a", "b"]


def test_import_requires_admin(client: TestClient) -> None:
    from karaoke_decide.core.models import User

    assert client.post(URL, json=_body(COMMUNITY)).status_code == 403
    assert client.post(URL, json=_body(COMMUNITY), headers=_tok("wrong")).status_code == 403
    app.dependency_overrides[get_optional_user] = lambda: User(id="plain", email="p@example.com")
    assert client.post(URL, json=_body(COMMUNITY)).status_code == 403
    app.dependency_overrides[get_optional_user] = lambda: User(id="adm", email="a@example.com", is_admin=True)
    assert client.post(URL, json=_body(COMMUNITY)).status_code == 200
    app.dependency_overrides[get_optional_user] = lambda: None
    assert client.post(URL, json=_body(COMMUNITY), headers=_tok()).status_code == 200


def test_import_matches_and_enqueues(client: TestClient, fs: Fs) -> None:
    res = client.post(URL, json=_body(COMMUNITY, COMMERCIAL, UNKNOWN), headers=_tok())
    assert res.status_code == 200
    assert res.json() == {
        "imported": 3,
        "matched": 2,
        "unmatched": [{"artist": "Nobody Known", "title": "Invented Tune"}],
        "enqueued": 1,
    }
    doc = fs.cols[LABELS_COLLECTION][label_doc_id(USER_ID, "adele|hello")]
    assert doc["good"] == "yes" and doc["tags"] == ["good_if_warm"] and doc["source"] == "sheet" and doc["matched"]
    assert doc["user_id"] == USER_ID
    unknown = fs.cols[LABELS_COLLECTION][label_doc_id(USER_ID, "nobody known|invented tune")]
    assert unknown["matched"] is False and unknown["good"] == "maybe" and unknown["tags"] == ["needs_practice"]
    queued = fs.cols[FETCH_COLLECTION]
    assert list(queued) == [pool_id("adele|hello")]
    q = queued[pool_id("adele|hello")]
    assert (q["source"], q["priority"], q["status"], q["spotify_id"], q["attempts"]) == (
        "andrew_sheet",
        0,
        "queued",
        None,
        0,
    )


def test_import_without_enqueue(client: TestClient, fs: Fs) -> None:
    res = client.post(URL, json=_body(COMMUNITY, enqueue=False), headers=_tok())
    assert res.json()["enqueued"] == 0
    assert FETCH_COLLECTION not in fs.cols


def test_import_is_idempotent(client: TestClient, fs: Fs) -> None:
    rows = (COMMUNITY, COMMERCIAL, UNKNOWN)
    assert client.post(URL, json=_body(*rows), headers=_tok()).json()["enqueued"] == 1
    again = client.post(URL, json=_body(*rows), headers=_tok()).json()
    assert again["enqueued"] == 0 and again["imported"] == 3
    assert len(fs.cols[LABELS_COLLECTION]) == 3
    assert len(fs.cols[FETCH_COLLECTION]) == 1
