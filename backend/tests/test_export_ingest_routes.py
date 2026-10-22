"""Spotify / Apple export aggregate ingestion: chunk idempotency and status. No GCP."""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_current_user, get_import_ingest_service_dep
from backend.main import app
from backend.services.import_ingest_service import ImportIngestService, IngestResult
from backend.tests.test_import_ingest_service import USER, FakeFirestore, FakeMatcher
from karaoke_decide.core.models import User


class MemoryFirestore:
    def __init__(self) -> None:
        self.docs: dict[tuple[str, str], dict[str, Any]] = {}

    async def transform_document_atomically(
        self, collection: str, doc_id: str, fn: Callable[[dict[str, Any] | None], dict[str, Any] | None]
    ) -> dict[str, Any] | None:
        new = fn(self.docs.get((collection, doc_id)))
        self.docs[(collection, doc_id)] = new  # type: ignore[assignment]
        return new

    async def get_document(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        return self.docs.get((collection, doc_id))


class Env:
    def __init__(self) -> None:
        self.fs = MemoryFirestore()
        self.ingest = AsyncMock()
        self.ingest.db = self.fs
        self.ingest.IMPORTS_COLLECTION = ImportIngestService.IMPORTS_COLLECTION
        self.ingest.ingest.return_value = IngestResult(
            artists_added=1, songs_added=2, songs_updated=3, tracks_matched=4
        )

        async def get_import(user_id: str, source: str) -> dict[str, Any] | None:
            return self.fs.docs.get(("user_imports", f"{user_id}:{source}"))

        self.ingest.get_import.side_effect = get_import

    def doc(self, source: str = "spotify_export") -> dict[str, Any]:
        return self.fs.docs[("user_imports", f"fixture-user:{source}")]


@pytest.fixture
def env() -> Iterator[tuple[TestClient, Env]]:
    e = Env()
    app.dependency_overrides[get_current_user] = lambda: User(id="fixture-user")
    app.dependency_overrides[get_import_ingest_service_dep] = lambda: e.ingest
    yield TestClient(app), e
    app.dependency_overrides.clear()


def track(artist: str = "Muse", title: str = "Hysteria", plays: int = 3) -> dict[str, Any]:
    return {"artist": artist, "title": title, "ms_played": 90000, "plays": plays, "first_ts": 1, "last_ts": 2}


def spotify(chunk_index: int = 0, chunk_count: int = 1, kind: str = "account", **over: Any) -> dict[str, Any]:
    body = {
        "kind": kind,
        "generated_at": "2026-10-01",
        "chunk_index": chunk_index,
        "chunk_count": chunk_count,
        "tracks": [track()],
        "liked": [],
        "playlists": [],
    }
    return body | over


def test_spotify_export_ingests_chunk(env: Any) -> None:
    client, e = env
    r = client.post(
        "/api/services/spotify-export",
        json=spotify(
            liked=[{"artist": "Queen", "title": "Bohemian Rhapsody"}],
            playlists=[{"name": "p", "tracks": [{"artist": "queen", "title": "Bohemian  Rhapsody"}]}],
        ),
    )
    assert r.status_code == 200, r.text
    assert r.json() == {
        "source": "spotify_export",
        "artists_added": 1,
        "songs_added": 2,
        "songs_updated": 3,
        "tracks_matched": 4,
        "duplicate": False,
        "chunks_done": 1,
        "chunk_count": 1,
    }
    args = e.ingest.ingest.call_args
    assert args.args[:2] == ("fixture-user", "spotify_export")
    tracks = args.args[2]
    assert [(t.artist, t.plays) for t in tracks] == [("Muse", 3), ("Queen", None)]  # liked/playlist deduped
    assert tracks[0].ms_played == 90000 and tracks[0].last_ts == 2


def test_duplicate_chunk_is_noop(env: Any) -> None:
    client, e = env
    first = client.post("/api/services/spotify-export", json=spotify(0, 2)).json()
    second = client.post("/api/services/spotify-export", json=spotify(0, 2))
    assert second.status_code == 200
    assert e.ingest.ingest.await_count == 1
    assert second.json() == first | {"duplicate": True}


def test_status_importing_until_last_chunk(env: Any) -> None:
    client, e = env
    client.post("/api/services/spotify-export", json=spotify(1, 2))
    assert e.doc()["status"] == "importing"
    r = client.post("/api/services/spotify-export", json=spotify(0, 2))
    assert e.doc()["status"] == "ok"
    assert r.json()["chunks_done"] == 2
    assert r.json()["songs_added"] == 4  # totals accumulate across chunks
    assert e.doc()["exports"]["account:2026-10-01"]["chunks_done"] == [0, 1]
    assert e.doc()["songs"] == 10 and e.doc()["artists"] == 2


def test_apple_export_source_apple_export(env: Any) -> None:
    client, e = env
    body = {"generated_at": "2026-10-01", "chunk_index": 0, "chunk_count": 1, "tracks": [track()]}
    r = client.post("/api/services/apple-export", json=body)
    assert r.status_code == 200 and r.json()["source"] == "apple_export"
    assert e.ingest.ingest.call_args.args[1] == "apple_export"
    assert e.doc("apple_export")["status"] == "ok"


def test_rejects_20001_tracks_422(env: Any) -> None:
    client, e = env
    r = client.post("/api/services/spotify-export", json=spotify(tracks=[track()] * 20001))
    assert r.status_code == 422
    e.ingest.ingest.assert_not_awaited()


def test_bad_chunk_400(env: Any) -> None:
    client, e = env
    r = client.post("/api/services/spotify-export", json=spotify(2, 2))
    assert r.status_code == 400 and r.json()["detail"] == "bad_chunk"
    client.post("/api/services/spotify-export", json=spotify(0, 2))
    r = client.post("/api/services/spotify-export", json=spotify(1, 3))  # count differs from chunk 0
    assert r.status_code == 400 and r.json()["detail"] == "bad_chunk"
    assert e.ingest.ingest.await_count == 1


@pytest.mark.parametrize("over", [{"plays": 0}, {"ms_played": -1}, {"artist": ""}, {"title": "x" * 301}])
def test_track_validation_422(env: Any, over: dict[str, Any]) -> None:
    client, _ = env
    assert client.post("/api/services/spotify-export", json=spotify(tracks=[track() | over])).status_code == 422


def test_ingest_failure_leaves_chunk_unrecorded(env: Any) -> None:
    client, e = env
    e.ingest.ingest.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        client.post("/api/services/spotify-export", json=spotify())
    assert ("user_imports", "fixture-user:spotify_export") not in e.fs.docs


def test_playlist_refs_capped_at_20000_422(env: Any) -> None:
    client, _ = env
    refs = [{"artist": f"a{i}", "title": "t"} for i in range(10001)]
    body = spotify(playlists=[{"name": "a", "tracks": refs[:10000]}, {"name": "b", "tracks": refs[:10000]}])
    assert client.post("/api/services/spotify-export", json=body).status_code == 200
    body = spotify(playlists=[{"name": f"p{i}", "tracks": refs[:10000]} for i in range(3)])
    assert client.post("/api/services/spotify-export", json=body).status_code == 422


# ---- real ImportIngestService over an in-memory Firestore -------------------------------------


class RealFirestore(FakeFirestore):
    async def transform_document_atomically(
        self, collection: str, doc_id: str, fn: Callable[[dict[str, Any] | None], dict[str, Any] | None]
    ) -> dict[str, Any] | None:
        new = fn(copy.deepcopy(self.col(collection).get(doc_id)))
        self.col(collection)[doc_id] = new  # type: ignore[assignment]
        return new


@pytest.fixture
def real() -> Iterator[tuple[TestClient, RealFirestore]]:
    fs = RealFirestore()
    svc = ImportIngestService(fs, FakeMatcher(), lambda: None)  # type: ignore[arg-type]
    app.dependency_overrides[get_current_user] = lambda: User(id=USER)
    app.dependency_overrides[get_import_ingest_service_dep] = lambda: svc
    yield TestClient(app), fs
    app.dependency_overrides.clear()


def artist_plays(fs: RealFirestore, name: str = "e2e-band") -> int:
    docs = [d for d in fs.col("user_artists").values() if d["artist_name"] == name]
    assert len(docs) == 1
    return int(docs[0]["playcount"])


def song_plays(fs: RealFirestore, title: str) -> int:
    return next(int(d["playcount"]) for d in fs.col("user_songs").values() if d["title"] == title)


def post(client: TestClient, idx: int, count: int, tracks: list[dict[str, Any]], **over: Any) -> Any:
    r = client.post("/api/services/spotify-export", json=spotify(idx, count, tracks=tracks, **over))
    assert r.status_code == 200, r.text
    return r


def test_artist_spanning_two_chunks_sums_and_repost_is_stable(real: Any) -> None:
    client, fs = real
    post(client, 0, 2, [track("e2e-band", "Song One", 5), track("other-act", "Song Z", 1)])
    assert artist_plays(fs) == 5
    post(client, 1, 2, [track("e2e-band", "Song Two", 4)])
    assert artist_plays(fs) == 9
    post(client, 1, 2, [track("e2e-band", "Song Two", 4)])  # duplicate chunk
    post(client, 0, 2, [track("e2e-band", "Song One", 5), track("other-act", "Song Z", 1)])
    assert artist_plays(fs) == 9
    ranks = {d["artist_name"]: d["rank"] for d in fs.col("user_artists").values()}
    assert ranks == {"e2e-band": 1, "other-act": 2}
    assert fs.col("user_imports")[f"{USER}:spotify_export"]["status"] == "ok"


def test_account_then_extended_no_double_count(real: Any) -> None:
    client, fs = real
    tracks = [track("e2e-band", "Song One", 5), track("e2e-band", "Song Two", 4)]
    post(client, 0, 1, tracks, kind="account")
    post(client, 0, 1, tracks, kind="extended", generated_at="2026-10-05")
    assert artist_plays(fs) == 9
    assert song_plays(fs, "Song One") == 5
    post(client, 0, 1, tracks, kind="extended", generated_at="2026-10-05")
    assert artist_plays(fs) == 9 and song_plays(fs, "Song Two") == 4


def test_failed_ingest_retry_does_not_double_artist_sums(real: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    client, fs = real
    svc = app.dependency_overrides[get_import_ingest_service_dep]()
    real_ingest = svc.ingest
    calls = {"n": 0}

    async def flaky(*a: Any, **k: Any) -> IngestResult:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        result: IngestResult = await real_ingest(*a, **k)
        return result

    monkeypatch.setattr(svc, "ingest", flaky)
    body = spotify(0, 1, tracks=[track("e2e-band", "Song One", 5)])
    with pytest.raises(RuntimeError):
        client.post("/api/services/spotify-export", json=body)
    assert client.post("/api/services/spotify-export", json=body).status_code == 200
    assert artist_plays(fs) == 5


@pytest.mark.parametrize("field", ["first_ts", "last_ts"])
@pytest.mark.parametrize("bad", [1_700_000_000_000, -1, 4_102_444_801])
def test_out_of_range_timestamp_is_422(env: tuple[TestClient, Env], field: str, bad: int) -> None:
    client, e = env
    t = track()
    t[field] = bad
    r = client.post("/api/services/spotify-export", json=spotify(tracks=[t]))
    assert r.status_code == 422
    e.ingest.ingest.assert_not_called()


def test_abandoned_incomplete_export_does_not_block_ok_status(env: Any) -> None:
    client, e = env
    # An earlier export (different generated_at) abandoned after 1 of 3 chunks.
    assert client.post("/api/services/spotify-export", json=spotify(0, 3, generated_at="2026-09-01")).status_code == 200
    assert e.doc()["status"] == "importing"
    # A later, complete export finishes: status reflects this export only.
    assert client.post("/api/services/spotify-export", json=spotify(0, 1, generated_at="2026-10-01")).status_code == 200
    assert e.doc()["status"] == "ok"
