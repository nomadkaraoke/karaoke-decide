"""Screenshot import: extract (mocked extractor), rate limit, confirm. No GCP, no Gemini."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_current_user
from backend.api.routes.services import get_screenshot_import_service
from backend.main import app
from backend.services import screenshot_import_service as svc_mod
from backend.services.import_ingest_service import IngestResult
from backend.services.screenshot_import_service import RawCandidate, ScreenshotImportService
from karaoke_decide.core.models import User
from karaoke_decide.song_search.text_normalize import song_key

MUSE_KEY = song_key("Muse", "Hysteria")
PNG = b"\x89PNG fake"


class MemoryFirestore:
    def __init__(self) -> None:
        self.docs: dict[tuple[str, str], dict[str, Any]] = {}

    async def transform_document_atomically(
        self, collection: str, doc_id: str, fn: Callable[[dict[str, Any] | None], dict[str, Any] | None]
    ) -> dict[str, Any] | None:
        new = fn(self.docs.get((collection, doc_id)))
        if new is None:
            self.docs.pop((collection, doc_id), None)
        else:
            self.docs[(collection, doc_id)] = new
        return new


class FakeCatalog:
    def lookup_song_keys(self, keys: list[str]) -> set[str]:
        return {k for k in keys if k == MUSE_KEY}

    def lookup_artist_keys(self, keys: list[str]) -> dict[str, str]:
        return {"muse": "Muse"} if "muse" in keys else {}


class FakeExtractor:
    def __init__(self, result: list[RawCandidate] | Exception) -> None:
        self.result = result
        self.calls = 0

    async def extract(self, images: list[tuple[bytes, str]]) -> list[RawCandidate]:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


MUSE_TWICE = [
    RawCandidate("song", "Muse", "Hysteria", 0.6),
    RawCandidate("song", "MUSE", "Hysteria", 0.9),
    RawCandidate("artist", "muse", None, 0.7),
]


class Env:
    def __init__(self, extractor: FakeExtractor | None) -> None:
        self.fs = MemoryFirestore()
        self.ingest = AsyncMock()
        self.ingest.ingest.return_value = IngestResult(1, 2, 3, 4)
        self.service = ScreenshotImportService(self.fs, self.ingest, FakeCatalog(), extractor)  # type: ignore[arg-type]


@pytest.fixture
def make_client() -> Iterator[Callable[[FakeExtractor | None], tuple[TestClient, Env]]]:
    def make(extractor: FakeExtractor | None) -> tuple[TestClient, Env]:
        env = Env(extractor)
        app.dependency_overrides[get_current_user] = lambda: User(id="fixture-user")
        app.dependency_overrides[get_screenshot_import_service] = lambda: env.service
        return TestClient(app), env

    yield make
    app.dependency_overrides.clear()


def _post(client: TestClient, n: int = 1, size: int = 10, mime: str = "image/png") -> Any:
    files = [("images", (f"s{i}.png", PNG + b"x" * size, mime)) for i in range(n)]
    return client.post("/api/services/screenshot/extract", files=files)


def test_extract_dedupes_and_matches(make_client: Any) -> None:
    client, _ = make_client(FakeExtractor(MUSE_TWICE + [RawCandidate("song", "Nobody", "Unknown Song", 0.99)]))
    resp = _post(client, 2)
    assert resp.status_code == 200
    cands = resp.json()["candidates"]
    assert len(cands) == 3
    song = cands[0]
    assert (song["kind"], song["matched"], song["song_key"], song["confidence"]) == ("song", True, MUSE_KEY, 0.9)
    assert cands[1]["kind"] == "artist" and cands[1]["matched"] and cands[1]["artist"] == "Muse"
    assert cands[2]["matched"] is False  # unmatched sorts last despite higher confidence


def test_unmatched_artist_kept(make_client: Any) -> None:
    client, _ = make_client(FakeExtractor([RawCandidate("artist", "e2e-band", None, 0.8)]))
    c = _post(client).json()["candidates"]
    assert c == [
        {"kind": "artist", "artist": "e2e-band", "title": None, "confidence": 0.8, "matched": False, "song_key": None}
    ]


def test_rejects_six_images_400(make_client: Any) -> None:
    client, _ = make_client(FakeExtractor([]))
    r = _post(client, 6)
    assert (r.status_code, r.json()["detail"]) == (400, "too_many_images")


def test_no_images_and_not_an_image(make_client: Any) -> None:
    client, _ = make_client(FakeExtractor([]))
    assert client.post("/api/services/screenshot/extract").json()["detail"] == "no_images"
    r = _post(client, mime="application/pdf")
    assert (r.status_code, r.json()["detail"]) == (400, "not_an_image")


def test_rejects_5mb_413(make_client: Any) -> None:
    client, _ = make_client(FakeExtractor([]))
    r = _post(client, size=5 * 1024 * 1024)
    assert (r.status_code, r.json()["detail"]) == (413, "image_too_large")


def test_daily_limit_counts_images(make_client: Any) -> None:
    client, env = make_client(FakeExtractor([]))
    assert _post(client, 5).status_code == 200
    assert _post(client, 5).status_code == 200
    r = _post(client, 1)
    assert (r.status_code, r.json()["detail"]) == (429, "daily_limit")
    assert env.fs.docs[("screenshot_imports", "fixture-user")]["count"] == 10


def test_gemini_failure_releases_reservation(make_client: Any, caplog: pytest.LogCaptureFixture) -> None:
    client, env = make_client(FakeExtractor(RuntimeError("boom")))
    r = _post(client, 3)
    assert (r.status_code, r.json()["detail"]) == (503, "screenshot_unavailable")
    assert env.fs.docs[("screenshot_imports", "fixture-user")]["count"] == 0


def test_quota_error_logs_error_once(
    make_client: Any, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(svc_mod, "_quota_logged", False)
    client, _ = make_client(FakeExtractor(RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")))
    assert _post(client).status_code == 503
    assert _post(client).status_code == 503
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1 and "quota" in errors[0].getMessage().lower()


def test_disabled_returns_503(make_client: Any) -> None:
    client, _ = make_client(None)
    r = _post(client)
    assert (r.status_code, r.json()["detail"]) == (503, "screenshot_unavailable")


def test_confirm_saves_with_source_screenshot(make_client: Any) -> None:
    client, env = make_client(FakeExtractor([]))
    body = {
        "items": [
            {"kind": "song", "artist": "Muse", "title": "Hysteria"},
            {"kind": "artist", "artist": "Muse"},
        ]
    }
    r = client.post("/api/services/screenshot/confirm", json=body)
    assert r.status_code == 200
    assert r.json() == {
        "source": "screenshot",
        "artists_added": 1,
        "songs_added": 2,
        "songs_updated": 3,
        "tracks_matched": 4,
    }
    args = env.ingest.ingest.call_args
    assert args.args[1] == "screenshot"
    assert [(t.artist, t.title) for t in args.args[2]] == [("Muse", "Hysteria")]
    assert [a.name for a in args.args[3]] == ["Muse"]
    env.ingest.record_import.assert_awaited_once()


def test_confirm_rejects_over_200(make_client: Any) -> None:
    client, _ = make_client(FakeExtractor([]))
    items = [{"kind": "artist", "artist": f"a{i}"} for i in range(201)]
    assert client.post("/api/services/screenshot/confirm", json={"items": items}).status_code == 422


def test_catalog_lookup_helpers_empty_while_warming_up() -> None:
    from types import SimpleNamespace

    from backend.services.catalog_lookup import CatalogLookup

    lookup = CatalogLookup(store=SimpleNamespace(reader=None))  # type: ignore[arg-type]
    assert lookup.lookup_song_keys(["a|b"]) == set()
    assert lookup.lookup_artist_keys(["a"]) == {}


def test_catalog_lookup_helpers_use_reader() -> None:
    from types import SimpleNamespace

    from backend.services.catalog_lookup import CatalogLookup

    reader = SimpleNamespace(
        has_karaoke_key=lambda k: k == "muse|hysteria",
        artist_summaries=lambda keys, top_songs: {"muse": SimpleNamespace(name="Muse")},
    )
    lookup = CatalogLookup(store=SimpleNamespace(reader=reader))  # type: ignore[arg-type]
    assert lookup.lookup_song_keys(["muse|hysteria", "x|y"]) == {"muse|hysteria"}
    assert lookup.lookup_artist_keys(["muse", "zzz"]) == {"muse": "Muse"}
