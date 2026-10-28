"""GET /api/my/get-made + POST|DELETE /api/my/get-made/{key}/dismiss (spec 6.9).

The user's own songs with no karaoke version (saved, sung incl. uncatalogued, imported,
voice-session "yes"), ranked by recs weight, topped up with the recs ``make_it`` shelf to 30.
Real fixture catalog.db + availability.db; in-memory Firestore; a fake recs v2 service.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from typing import Any
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import (
    get_catalog_store_dep,
    get_current_user,
    get_firestore,
    get_recommendation_service_v2_dep,
)
from backend.main import app
from backend.services.get_made_service import DISMISSED_COLLECTION, MAX_ITEMS, dismissed_doc_id
from backend.services.recs_v2.service import (
    RecFacets,
    RecKaraoke,
    RecommendationsV2Response,
    RecSection,
    RecSong,
    RecVoice,
)
from backend.services.user_deletion import USER_DATA_STORES
from backend.tests import test_user_data_registry_guard as guard
from backend.tests.recs_v2.conftest import catalog_dir, reader  # noqa: F401  (fixtures)
from backend.tests.test_fakes import MemoryFirestore
from karaoke_decide.catalog.reader import CatalogReader
from karaoke_decide.core.models import User
from karaoke_decide.song_search.text_normalize import song_key

USER = "user_getmade"
OTHER = "user_other"


def fixture_key(a: int, n: int) -> str:
    """A fixture catalog song with no karaoke version (``in_kn = 0``, not in availability.db)."""
    return song_key(f"Fixture Artist {a:02d}", f"Fixture Song {a:02d}-{n:02d}")


def fixture_names(a: int, n: int) -> tuple[str, str]:
    return f"Fixture Artist {a:02d}", f"Fixture Song {a:02d}-{n:02d}"


KARAOKE_KEY = song_key("Toto", "Africa")  # a KaraokeNerds fixture song


class Store:
    run = "fixture-run"

    def __init__(self, catalog: CatalogReader) -> None:
        self.reader = catalog

    def require(self) -> CatalogReader:
        return self.reader


class FakeRecs:
    """``recommend`` returns the given sections; ``fail`` makes it raise."""

    def __init__(self) -> None:
        self.sections: list[RecSection] = []
        self.fail = False
        self.calls: list[str] = []

    async def recommend(self, user: User, f: Any, sort: str = "best", cursor: str | None = None) -> Any:
        self.calls.append(user.id)
        if self.fail:
            raise RuntimeError("recs down")
        return RecommendationsV2Response(
            sections=self.sections,
            filters_applied={},
            next_cursor=None,
            has_profile=True,
            catalog_run="fixture-run",
            voice=RecVoice(has_profile=False, method=None),
        )


def rec_song(key: str, artist: str, title: str, score: float, *, karaoke: bool = False) -> RecSong:
    return RecSong(
        song_key=key,
        artist=artist,
        title=title,
        reason="Make it",
        reason_type="make_it",
        reason_artist=None,
        score=score,
        has_karaoke_version=karaoke,
        karaoke=RecKaraoke(community=False, commercial=False, karafun=False, brand_count=0),
        facets=RecFacets(
            decade=None,
            genres=[],
            mood=None,
            energy=None,
            lead_gender=None,
            duration_ms=None,
            popularity=None,
            is_classic=False,
            explicit=False,
        ),
    )


def section(sid: str, songs: list[RecSong]) -> RecSection:
    return RecSection(id=sid, title_key=f"recs.section.{sid}.title", reason_key="", title_params={}, songs=songs)


def put_song(fs: MemoryFirestore, key: str, artist: str, title: str, user: str = USER, **fields: Any) -> None:
    fs.col("user_songs")[f"{user}:{hashlib.sha1(key.encode()).hexdigest()[:12]}"] = {  # noqa: S324
        "user_id": user,
        "artist": artist,
        "title": title,
        "song_key": key,
        **fields,
    }


def put_fixture(fs: MemoryFirestore, a: int, n: int, user: str = USER, **fields: Any) -> str:
    key = fixture_key(a, n)
    put_song(fs, key, *fixture_names(a, n), user=user, **fields)
    return key


@pytest.fixture
def fs() -> MemoryFirestore:
    return MemoryFirestore()


@pytest.fixture
def recs() -> FakeRecs:
    return FakeRecs()


@pytest.fixture
def client(fs: MemoryFirestore, recs: FakeRecs, reader: CatalogReader) -> Iterator[TestClient]:  # noqa: F811
    app.dependency_overrides[get_firestore] = lambda: fs
    app.dependency_overrides[get_catalog_store_dep] = lambda: Store(reader)
    app.dependency_overrides[get_recommendation_service_v2_dep] = lambda: recs
    app.dependency_overrides[get_current_user] = lambda: User(id=USER, email="singer@example.com")
    yield TestClient(app)
    app.dependency_overrides.clear()


def items(client: TestClient) -> list[dict[str, Any]]:
    r = client.get("/api/my/get-made")
    assert r.status_code == 200, r.text
    return r.json()["items"]


def dismiss_url(key: str) -> str:
    return f"/api/my/get-made/{quote(key, safe='')}/dismiss"


def test_get_made_lists_loved_no_karaoke(client: TestClient, fs: MemoryFirestore) -> None:
    loved = put_fixture(fs, 1, 1, source="manual", loved=True)
    imported = put_fixture(fs, 1, 2, source="spotify", sources=["spotify"])  # saved track: 0.9
    sung = put_fixture(fs, 1, 3, source="karaoke_log", times_sung=1)  # sung once, no feel: 0.8
    voice = put_fixture(fs, 1, 4, source="voice_session", sources=["voice_session"], session_would_sing="yes")
    put_fixture(fs, 1, 5, source="quiz", sources=["quiz"])  # quiz-seeded: not one of the user's songs
    put_fixture(fs, 1, 6, source="voice_session", sources=["voice_session"], session_feel="fine")  # no "yes"
    put_fixture(fs, 1, 7, user=OTHER, source="manual", loved=True)  # someone else's

    got = items(client)
    assert [(i["key"], i["why"], i["weight"]) for i in got] == [
        (loved, "saved", 1.0),
        (voice, "voice", 1.0),
        (imported, "imported", 0.9),
        (sung, "sung", 0.8),
    ]
    first = got[0]
    assert first["song_key"] == loved
    assert (first["artist"], first["title"]) == fixture_names(1, 1)


def test_get_made_why_prefers_saved_then_sung_and_merges_rows(client: TestClient, fs: MemoryFirestore) -> None:
    key = put_fixture(fs, 2, 1, source="spotify", sources=["spotify"], times_sung=2)
    fs.col("user_songs")[f"{USER}:dup"] = {"user_id": USER, "song_key": key, "source": "manual", "loved": True}
    got = items(client)
    assert [(i["key"], i["why"], i["weight"]) for i in got] == [(key, "saved", 1.0)]
    assert (got[0]["artist"], got[0]["title"]) == fixture_names(2, 1)


def test_get_made_excludes_has_karaoke(client: TestClient, fs: MemoryFirestore, reader: CatalogReader) -> None:  # noqa: F811
    put_song(fs, KARAOKE_KEY, "Toto", "Africa", source="manual", loved=True)
    kn_id = reader.kn_ids_for_song_keys([KARAOKE_KEY])[KARAOKE_KEY]
    fs.col("user_songs")[f"{USER}:{kn_id}"] = {
        "user_id": USER,
        "song_id": str(kn_id),
        "artist": "Toto",
        "title": "Africa",
        "source": "spotify",
        "times_sung": 3,
    }
    keep = put_fixture(fs, 3, 1, source="manual", loved=True)
    assert [i["key"] for i in items(client)] == [keep]


def test_get_made_includes_sung_unresolved(client: TestClient, fs: MemoryFirestore) -> None:
    norm = song_key("Nobody Band", "Unknown Tune")
    fs.col("user_karaoke_unresolved")[f"{USER}:abc"] = {
        "user_id": USER,
        "artist": "Nobody Band",
        "title": "Unknown Tune",
        "norm_key": norm,
        "times_sung": 2,
    }
    fs.col("user_karaoke_unresolved")[f"{USER}:zero"] = {
        "user_id": USER,
        "artist": "Gone Band",
        "title": "Undone",
        "norm_key": song_key("Gone Band", "Undone"),
        "times_sung": 0,
    }
    fs.col("user_karaoke_unresolved")[f"{OTHER}:abc"] = {
        "user_id": OTHER,
        "artist": "Their Band",
        "title": "Their Tune",
        "norm_key": song_key("Their Band", "Their Tune"),
        "times_sung": 5,
    }
    # an uncatalogued loved row (no catalog song): a "u:" key and no song_key
    fs.col("user_songs")[f"{USER}:x"] = {
        "user_id": USER,
        "artist": "Invented Duo",
        "title": "Made Up",
        "source": "manual",
        "loved": True,
    }
    got = items(client)
    assert [(i["key"], i["song_key"], i["why"], i["weight"]) for i in got] == [  # tied weights: by key
        ("u:" + song_key("Invented Duo", "Made Up"), None, "saved", 1.0),
        ("u:" + norm, None, "sung", 1.0),
    ]
    by_key = {i["key"]: i for i in got}
    assert (by_key["u:" + norm]["artist"], by_key["u:" + norm]["title"]) == ("Nobody Band", "Unknown Tune")


def test_get_made_excludes_dismissed_and_survey_no(client: TestClient, fs: MemoryFirestore, recs: FakeRecs) -> None:
    loved = put_fixture(fs, 4, 1, source="manual", loved=True)
    sung = put_fixture(fs, 4, 2, source="karaoke_log", times_sung=1)
    put_fixture(fs, 4, 3, source="manual", loved=True, times_sung=2, sung_feel_latest="bad")  # survey_no wins
    put_fixture(fs, 4, 4, source="manual", loved=True, session_would_sing="no")  # session "no"
    hidden = put_fixture(fs, 4, 5, source="manual", loved=True)
    fs.col("user_hidden_songs")[f"{USER}:h"] = {"user_id": USER, "song_key": hidden}
    rec_key = fixture_key(5, 1)
    recs.sections = [section("make_it", [rec_song(rec_key, *fixture_names(5, 1), 0.5)])]
    assert [i["key"] for i in items(client)] == [loved, sung, rec_key]

    assert client.post(dismiss_url(loved)).status_code == 204
    assert client.post(dismiss_url(rec_key)).status_code == 204
    assert [i["key"] for i in items(client)] == [sung]

    assert client.delete(dismiss_url(loved)).status_code == 204
    assert [i["key"] for i in items(client)] == [loved, sung]


def test_get_made_topped_up_from_make_it_and_capped_30(client: TestClient, fs: MemoryFirestore, recs: FakeRecs) -> None:
    own = [put_fixture(fs, 6 + i // 10, i % 10 + 1, source="manual", loved=True) for i in range(25)]
    own_dup = own[0]
    recs.sections = [
        section("artists_you_love", [rec_song(fixture_key(12, 1), *fixture_names(12, 1), 0.9)]),  # not make_it
        section(
            "make_it",
            [
                rec_song(own_dup, *fixture_names(6, 1), 0.8),  # already listed as the user's own
                rec_song(KARAOKE_KEY, "Toto", "Africa", 0.7, karaoke=True),  # has karaoke: never
                *[rec_song(fixture_key(13, n), *fixture_names(13, n), 0.6 - n / 100) for n in range(1, 10)],
            ],
        ),
    ]
    got = items(client)
    assert len(got) == MAX_ITEMS == 30
    assert [i["key"] for i in got[:25]] == sorted(own)  # tied weights: by key
    assert all(i["why"] == "saved" for i in got[:25])
    tail = got[25:]
    assert [i["key"] for i in tail] == [fixture_key(13, n) for n in range(1, 6)]
    assert all(i["why"] == "recommended" for i in tail)
    assert tail[0]["song_key"] == tail[0]["key"] and tail[0]["weight"] == 0.59


def test_get_made_degrades_when_recs_fail(client: TestClient, fs: MemoryFirestore, recs: FakeRecs) -> None:
    recs.fail = True
    key = put_fixture(fs, 14, 1, source="manual", loved=True)
    assert [i["key"] for i in items(client)] == [key]


def test_dismiss_roundtrip_and_privacy(client: TestClient, fs: MemoryFirestore) -> None:
    key = put_fixture(fs, 15, 1, source="manual", loved=True)
    odd = "u:ac/dc|back #1 %20"  # '/', '#' and a literal '%20' survive the raw-path decode
    assert client.post(dismiss_url(odd)).status_code == 204
    doc = fs.col(DISMISSED_COLLECTION)[dismissed_doc_id(USER, odd)]
    assert doc["user_id"] == USER and doc["key"] == odd and doc["created_at"]
    assert dismissed_doc_id(USER, odd) == f"{USER}:{hashlib.sha1(odd.encode()).hexdigest()[:24]}"  # noqa: S324
    assert client.post(dismiss_url(odd)).status_code == 204  # idempotent
    assert len(fs.col(DISMISSED_COLLECTION)) == 1

    # another user's dismissal of the same song doesn't hide it for this user
    fs.col(DISMISSED_COLLECTION)[dismissed_doc_id(OTHER, key)] = {"user_id": OTHER, "key": key, "created_at": "x"}
    r = client.get("/api/my/get-made")
    assert [i["key"] for i in r.json()["items"]] == [key]
    assert "user_id" not in r.text and "email_hash" not in r.text and USER not in r.text

    r = client.delete(dismiss_url(odd))
    assert r.status_code == 204 and r.content == b""
    assert dismissed_doc_id(USER, odd) not in fs.col(DISMISSED_COLLECTION)
    assert client.delete(dismiss_url(odd)).status_code == 204  # idempotent


def test_get_made_requires_auth() -> None:
    app.dependency_overrides.clear()
    c = TestClient(app)
    assert c.get("/api/my/get-made").status_code in (401, 403)
    assert c.post(dismiss_url("a|b")).status_code in (401, 403)


def test_registry_guard_passes() -> None:
    stores = {s.collection: s for s in USER_DATA_STORES}
    assert stores[DISMISSED_COLLECTION].match == "user_id_field"
    assert stores[DISMISSED_COLLECTION].field == "user_id"
    assert DISMISSED_COLLECTION in guard._all_collections()
    guard.test_guard_every_collection_classified()
