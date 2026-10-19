"""Search service HTTP API over the fixture indexes (SONG_INDEX_LOCAL_DIR)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from karaoke_decide.song_search.text_normalize import song_key

from .conftest import RUN_ID

STARLIGHT = song_key("Muse", "Starlight")


def _search(client: TestClient, q: str, **params: str | int) -> dict[str, Any]:
    r = client.get("/api/search", params={"q": q, **params}, headers={"X-Device-Id": "dev-1"})
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


# --- /api/search -------------------------------------------------------------


def test_confident_typo_is_content_and_identified_group_first(client: TestClient) -> None:
    body = _search(client, "starlite muse")
    ident = body["identification"]
    assert ident["status"] == "confident"
    assert ident["kind"] == "content"
    assert ident["best"]["artist"] == "Muse"
    assert ident["best"]["title"] == "Starlight"
    assert ident["best"]["song_key"] == STARLIGHT
    assert ident["best"]["karaoke"] is True
    first = body["groups"][0]
    assert first["song_key"] == STARLIGHT
    assert {v["kind"] for v in first["versions"]} == {"community", "commercial"}
    # Disc-only commercial versions have no link.
    assert any(v["youtube_url"] is None for v in first["versions"])
    assert body["index"]["run"] == RUN_ID
    assert isinstance(body["index"]["built_at"], int)
    assert body["sid"]


def test_groups_are_unique_by_song_key(client: TestClient) -> None:
    keys = [g["song_key"] for g in _search(client, "starlite muse")["groups"]]
    assert len(keys) == len(set(keys))


def test_confident_song_without_karaoke_has_no_group(client: TestClient) -> None:
    body = _search(client, "rihanna push up on me")
    ident = body["identification"]
    assert ident["status"] == "confident"
    assert ident["best"]["karaoke"] is False
    best_key = ident["best"]["song_key"]
    assert best_key == song_key("Rihanna", "Push Up On Me")
    assert all(g["song_key"] != best_key for g in body["groups"])


def test_machu_picchu_strokes_confident_without_karaoke(client: TestClient) -> None:
    body = _search(client, "machu picchu strokes")
    ident = body["identification"]
    assert ident["status"] == "confident"
    assert ident["best"]["song_key"] == song_key("The Strokes", "Machu Picchu")
    assert ident["best"]["karaoke"] is False
    assert all(g["song_key"] != ident["best"]["song_key"] for g in body["groups"])


def test_generic_title_gives_candidates(client: TestClient) -> None:
    ident = _search(client, "baby")["identification"]
    assert ident["status"] == "candidates"
    assert ident["kind"] is None  # only meaningful when confident
    assert 2 <= len(ident["candidates"]) <= 5
    for c in ident["candidates"]:
        assert c["song_key"] == song_key(c["artist"], c["title"])
        assert set(c) == {"artist", "title", "song_key", "popularity", "karaoke"}


@pytest.mark.parametrize(
    "q",
    ["karaoke", "x", "🎤🎤", "a" * 250, "מה קורה", "東京 フラッシュ", "   ", ""],
)
def test_review_focus_1_inputs_are_none(client: TestClient, q: str) -> None:
    body = _search(client, q)
    assert body["identification"] == {"status": "none", "kind": None, "best": None, "candidates": []}
    assert body["groups"] == []


def test_one_character_query_skips_the_index(client: TestClient) -> None:
    # R8: under 2 characters (after strip) the app shows popular songs instead.
    body = _search(client, " a ")
    assert body["identification"]["status"] == "none"
    assert body["groups"] == []


def test_limit_clamps(client: TestClient) -> None:
    assert len(_search(client, "baby", limit=-5)["groups"]) == 1
    assert len(_search(client, "baby", limit=2)["groups"]) <= 2
    assert len(_search(client, "starlight", limit=10_000)["groups"]) <= 60


def test_versions_capped_in_search_but_not_on_song_page(client: TestClient) -> None:
    group = next(g for g in _search(client, "starlight express")["groups"] if g["version_count"] > 12)
    assert len(group["versions"]) == 12
    detail = client.get("/api/search/song", params={"key": group["song_key"]}).json()
    assert len(detail["versions"]) == group["version_count"]


def test_sid_round_trips(client: TestClient) -> None:
    assert _search(client, "baby", sid="abc123")["sid"] == "abc123"


def test_search_logs_one_structured_line(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="search_events"):
        _search(client, "starlite muse", sid="s-1")
    recs = [r for r in caplog.records if r.name == "search_events"]
    assert len(recs) == 1
    fields = recs[0].json_fields  # type: ignore[attr-defined]
    assert fields["type"] == "search"
    assert fields["sid"] == "s-1"
    assert fields["device"] == "dev-1"
    assert fields["q"] == "starlite muse"
    assert fields["status"] == "confident"
    assert fields["kind"] == "content"
    assert fields["group_count"] >= 1
    assert isinstance(fields["ms"], int | float)


def test_search_survives_logging_failure(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> None:
        raise RuntimeError("log sink down")

    monkeypatch.setattr(logging.getLogger("search_events"), "info", boom)
    assert _search(client, "baby")["identification"]["status"] == "candidates"


def test_identify_crash_degrades_to_none(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from karaoke_decide.song_search.identify import SongIdentifier

    def boom(self: object, q: str, limit: int = 5) -> dict:
        raise RuntimeError("bad index page")

    monkeypatch.setattr(SongIdentifier, "identify", boom)
    body = _search(client, "starlight")
    assert body["identification"]["status"] == "none"
    assert body["groups"]  # availability still answers


# --- popular / song ------------------------------------------------------------


def test_popular(client: TestClient) -> None:
    r = client.get("/api/search/popular", params={"limit": 5})
    assert r.status_code == 200
    groups = r.json()
    assert len(groups) == 5
    assert groups[0]["song_key"] == song_key("Billie Eilish", "Birds of a Feather")
    assert all(len(g["versions"]) <= 12 for g in groups)
    assert len(client.get("/api/search/popular", params={"limit": -1}).json()) == 1


def test_song_detail(client: TestClient) -> None:
    r = client.get("/api/search/song", params={"key": STARLIGHT})
    assert r.status_code == 200
    body = r.json()
    assert body["song"] == {
        "artist": "Muse",
        "title": "Starlight",
        "song_key": STARLIGHT,
        "popularity": 72,
        "karaoke": True,
    }
    assert sorted(v["brand_code"] for v in body["versions"]) == ["CC", "KV", "NOMAD", "SC", "SF"]


def test_song_unknown_is_404(client: TestClient) -> None:
    assert client.get("/api/search/song", params={"key": "unknown"}).status_code == 404


# --- events -------------------------------------------------------------------------


def test_event_post_logs_structured_record(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="search_events"):
        r = client.post(
            "/api/search/event",
            json={"sid": "s-9", "type": "pick_candidate", "data": {"index": 2, "song_key": STARLIGHT}},
            headers={"X-Device-Id": "dev-7"},
        )
    assert r.status_code == 204
    assert r.content == b""
    recs = [r for r in caplog.records if r.name == "search_events"]
    assert len(recs) == 1
    fields = recs[0].json_fields  # type: ignore[attr-defined]
    assert fields["type"] == "pick_candidate"
    assert fields["sid"] == "s-9"
    assert fields["device"] == "dev-7"
    assert fields["data"] == {"index": 2, "song_key": STARLIGHT}


def test_event_rejects_oversized_data(client: TestClient) -> None:
    r = client.post("/api/search/event", json={"sid": "s", "type": "not_it", "data": {"x": "y" * 5000}})
    assert r.status_code == 422


def test_event_rejects_missing_type(client: TestClient) -> None:
    assert client.post("/api/search/event", json={"sid": "s", "data": {}}).status_code == 422


# --- offline index manifest -----------------------------------------------------------


def test_offline_index_manifest(client: TestClient, index_dir: Path) -> None:
    r = client.get("/api/search/offline-index")
    assert r.status_code == 200
    manifest = json.loads((index_dir / "manifest.json").read_text())
    entry = manifest["files"]["offline.db.gz"]
    assert r.json() == {
        "run": RUN_ID,
        "schema": 1,
        "normalizer": 3,
        "url": f"https://decide.nomadkaraoke.com/offline-index/{RUN_ID}/offline.db.gz",
        "bytes": entry["bytes"],
        "sha256": entry["sha256"],
    }


def test_offline_index_url_template_env(index_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.search.app import create_search_app

    monkeypatch.setenv("SONG_INDEX_LOCAL_DIR", str(index_dir))
    monkeypatch.setenv("SONG_INDEX_OFFLINE_URL_TEMPLATE", "http://127.0.0.1:8020/x/{run}/o.db.gz")
    with TestClient(create_search_app()) as c:
        assert c.get("/api/search/offline-index").json()["url"] == f"http://127.0.0.1:8020/x/{RUN_ID}/o.db.gz"


def test_offline_index_absent_is_404(index_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.search.app import create_search_app

    for name in ("song_id.db", "availability.db"):
        (tmp_path / name).write_bytes((index_dir / name).read_bytes())
    monkeypatch.setenv("SONG_INDEX_LOCAL_DIR", str(tmp_path))
    with TestClient(create_search_app()) as c:
        assert c.get("/api/search/offline-index").status_code == 404
        # Without a manifest the run id comes from availability.db's meta.
        assert c.get("/api/search", params={"q": "baby"}).json()["index"]["run"] == "20261018-000000"


# --- health / broken index --------------------------------------------------------------


def test_health(client: TestClient) -> None:
    r = client.get("/api/search/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_health_ok_with_broken_index_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.search.app import create_search_app

    (tmp_path / "song_id.db").write_bytes(b"not a database")
    monkeypatch.setenv("SONG_INDEX_LOCAL_DIR", str(tmp_path))
    with TestClient(create_search_app()) as c:
        assert c.get("/api/search/health").json() == {"ok": True}
        r = c.get("/api/search", params={"q": "baby"})
        assert r.status_code == 503
        assert c.get("/api/search/popular").status_code == 503
        assert c.get("/api/search/song", params={"key": STARLIGHT}).status_code == 503
        assert c.get("/api/search/offline-index").status_code == 503


def test_health_does_not_touch_index(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlite3

    def no_db(*a: object, **k: object) -> None:
        raise AssertionError("health touched sqlite")

    monkeypatch.setattr(sqlite3, "connect", no_db)
    assert client.get("/api/search/health").status_code == 200


def test_health_is_not_rate_limited(client: TestClient) -> None:
    for _ in range(320):
        assert client.get("/api/search/health", headers={"X-Device-Id": "pinger"}).status_code == 200


def test_openapi_names_the_contract_models(client: TestClient) -> None:
    schemas = client.get("/api/search/openapi.json").json()["components"]["schemas"]
    for name in (
        "SearchResponse",
        "Identification",
        "SongRef",
        "SongGroup",
        "Version",
        "IndexInfo",
        "SongDetail",
        "SearchEvent",
        "OfflineIndexManifest",
    ):
        assert name in schemas, name
    assert "schema" in schemas["OfflineIndexManifest"]["properties"]


# --- readiness (R14) -------------------------------------------------------------------


def test_ready_when_index_loaded(client: TestClient) -> None:
    r = client.get("/api/search/ready")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_not_ready_with_broken_index_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.search.app import create_search_app

    (tmp_path / "song_id.db").write_bytes(b"not a database")
    monkeypatch.setenv("SONG_INDEX_LOCAL_DIR", str(tmp_path))
    with TestClient(create_search_app()) as c:
        assert c.get("/api/search/ready").status_code == 503
        assert c.get("/api/search/health").status_code == 200


def test_identified_song_lookup_error_degrades(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlite3

    from karaoke_decide.song_search.availability import AvailabilityIndex

    def no_search(self: object, q: str, limit: int = 60) -> list[dict[str, Any]]:
        return []

    def broken(self: object, key: str) -> None:
        raise sqlite3.DatabaseError("database disk image is malformed")

    monkeypatch.setattr(AvailabilityIndex, "search", no_search)
    monkeypatch.setattr(AvailabilityIndex, "song", broken)
    body = _search(client, "starlite muse")
    assert body["identification"]["status"] == "confident"
    assert body["groups"] == []
