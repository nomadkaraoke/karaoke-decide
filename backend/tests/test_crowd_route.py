"""GET /api/songs/{song_key}/crowd: thresholded crowd singability for one song."""

from __future__ import annotations

from collections.abc import Iterator
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_current_user, get_firestore
from backend.main import app
from backend.services.crowd_stats import CROWD_COLLECTION
from backend.tests.test_fakes import MemoryFirestore
from karaoke_decide.core.models import User

KEY = "guns n roses|sweet child #1 %20"  # '|', '#', a literal '%20': the raw-path decode keeps it exact
SLASH_KEY = "ac/dc|back in black"  # a legacy key with '/': never a Firestore doc id, so no figure


@pytest.fixture
def fs() -> MemoryFirestore:
    m = MemoryFirestore()
    m.col("decide_users")["guest_u1"] = {"user_id": "guest_u1", "voice_profile": {"voice_type": "baritone"}}
    m.col(CROWD_COLLECTION)[KEY] = {
        "total": {"right": 7, "too_low": 0, "too_high": 3},
        "by_bucket": {"baritone": {"right": 2, "too_low": 0, "too_high": 3}},
        "crowd_pleaser_yes": 4,
        "crowd_pleaser_answered": 5,
    }
    return m


@pytest.fixture
def client(fs: MemoryFirestore) -> Iterator[TestClient]:
    app.dependency_overrides[get_firestore] = lambda: fs
    yield TestClient(app)
    app.dependency_overrides.clear()


def _url(key: str) -> str:
    return f"/api/songs/{quote(key, safe='')}/crowd"


def test_crowd_route_auth_and_slash_key(client: TestClient) -> None:
    assert client.get(_url(KEY)).status_code == 401

    app.dependency_overrides[get_current_user] = lambda: User(id="guest_u1", is_guest=True)
    r = client.get(_url(KEY))
    assert r.status_code == 200
    assert r.json() == {
        "singers": 10,
        "comfortable_share": 0.7,
        "too_high_share": 0.3,
        "too_low_share": 0.0,
        "bucket": "baritone",
        "bucket_singers": 5,
        "bucket_comfortable_share": 0.4,
        "crowd_pleaser_share": 0.8,
    }
    r = client.get(_url(SLASH_KEY))
    assert r.status_code == 200 and set(r.json().values()) == {None}


def test_crowd_route_verified_user_bucket_by_query(client: TestClient, fs: MemoryFirestore) -> None:
    fs.col("decide_users")["emailhash"] = {"user_id": "u2", "voice_profile": {"voice_type": "tenor"}}
    app.dependency_overrides[get_current_user] = lambda: User(id="u2", is_guest=False)
    body = client.get(_url(KEY)).json()
    assert body["singers"] == 10
    assert body["bucket"] is None  # tenor has no votes


def test_crowd_route_unknown_song_all_null(client: TestClient) -> None:
    app.dependency_overrides[get_current_user] = lambda: User(id="guest_u1", is_guest=True)
    r = client.get(_url("nobody|nothing"))
    assert r.status_code == 200
    assert set(r.json().values()) == {None}
