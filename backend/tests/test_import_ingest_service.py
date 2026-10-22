"""ImportIngestService: the one write path every import source goes through."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import AsyncGenerator, Sequence
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.cloud import firestore

from backend.config import BackendSettings
from backend.services.firestore_service import FirestoreService
from backend.services.import_ingest_service import (
    ImportIngestService,
    IngestArtist,
    IngestResult,
    IngestTrack,
    _iso,
)
from backend.services.song_feedback_service import loved_doc_id
from backend.services.track_matcher import MatchedTrack
from karaoke_decide.services.bigquery_catalog import SongResult
from karaoke_decide.song_search.text_normalize import song_key

USER = "fixture-user"
KN_ID = 4242
MATCHED = ("The Lanterns", "Paper Harbour")
MATCHED_KEY = song_key(*MATCHED)
UNMATCHED = ("e2e-band", "Nobody Sings This")


class FakeFirestore:
    """In-memory stand-in for the FirestoreService calls the ingest service makes."""

    def __init__(self) -> None:
        self.cols: dict[str, dict[str, dict[str, Any]]] = {}
        self.batch_set_calls: list[tuple[str, int]] = []

    def col(self, name: str) -> dict[str, dict[str, Any]]:
        return self.cols.setdefault(name, {})

    async def get_document(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        doc = self.col(collection).get(doc_id)
        return {"id": doc_id, **copy.deepcopy(doc)} if doc is not None else None

    async def set_document(self, collection: str, doc_id: str, data: dict[str, Any], merge: bool = False) -> None:
        cur = self.col(collection).get(doc_id) if merge else None
        out = {**(cur or {})}
        for field, value in data.items():
            if isinstance(value, firestore.ArrayUnion):  # server-side union, like Firestore
                out[field] = list(dict.fromkeys([*out.get(field, []), *value.values]))
            else:
                out[field] = copy.deepcopy(value)
        self.col(collection)[doc_id] = out

    async def delete_document(self, collection: str, doc_id: str) -> None:
        self.col(collection).pop(doc_id, None)

    async def get_many(self, collection: str, ids: list[str]) -> dict[str, dict[str, Any]]:
        return {i: copy.deepcopy(self.col(collection)[i]) for i in ids if i in self.col(collection)}

    async def batch_set(self, collection: str, docs: dict[str, dict[str, Any]], merge: bool = False) -> None:
        self.batch_set_calls.append((collection, len(docs)))
        for doc_id, data in docs.items():
            await self.set_document(collection, doc_id, data, merge=merge)

    async def batch_delete(self, collection: str, doc_ids: list[str]) -> None:
        for doc_id in doc_ids:
            self.col(collection).pop(doc_id, None)

    async def query_documents(
        self, collection: str, filters: list[tuple[str, str, Any]] | None = None, **_: Any
    ) -> list[dict[str, Any]]:
        out = []
        for doc_id, doc in self.col(collection).items():
            if all(op == "==" and doc.get(f) == v for f, op, v in filters or []):
                out.append({"id": doc_id, **copy.deepcopy(doc)})
        return out


class FakeMatcher:
    """Matches ``MATCHED`` to KaraokeNerds id ``KN_ID``; everything else is unmatched."""

    def __init__(self) -> None:
        self.calls = 0

    async def batch_match_tracks(self, tracks: list[dict[str, Any]]) -> list[MatchedTrack]:
        self.calls += 1
        out = []
        for t in tracks:
            hit = (t["artist"], t["title"]) == MATCHED
            out.append(
                MatchedTrack(
                    original_artist=t["artist"],
                    original_title=t["title"],
                    normalized_artist=t["artist"].lower(),
                    normalized_title=t["title"].lower(),
                    catalog_song=SongResult(id=KN_ID, artist=MATCHED[0], title=MATCHED[1], brands="", brand_count=0)
                    if hit
                    else None,
                    match_confidence=1.0 if hit else 0.0,
                    playcount=t.get("playcount"),
                )
            )
        return out


class FakeReader:
    def kn_song_keys(self, kn_ids: Sequence[int]) -> dict[int, str]:
        return {KN_ID: MATCHED_KEY} if KN_ID in kn_ids else {}

    def has_karaoke_key(self, key: str) -> bool:
        return key == MATCHED_KEY

    def kn_ids_for_song_keys(self, keys: Sequence[str]) -> dict[str, int]:
        return {MATCHED_KEY: KN_ID} if MATCHED_KEY in keys else {}


def make_service(db: FakeFirestore | None = None, reader: FakeReader | None = None) -> ImportIngestService:
    return ImportIngestService(
        db or FakeFirestore(),  # type: ignore[arg-type]
        track_matcher=FakeMatcher(),  # type: ignore[arg-type]
        reader=lambda: reader if reader is not None else FakeReader(),
    )


def two_tracks(plays: int = 5) -> list[IngestTrack]:
    return [
        IngestTrack(artist=MATCHED[0], title=MATCHED[1], plays=plays, last_ts=1_700_000_000),
        IngestTrack(artist=MATCHED[0], title="Another Harbour", plays=plays + 2),
    ]


def songs(db: FakeFirestore) -> dict[str, dict[str, Any]]:
    return db.col("user_songs")


@pytest.mark.asyncio
async def test_ingest_creates_songs_and_derives_artists() -> None:
    db = FakeFirestore()
    result = await make_service(db).ingest(USER, "spotify_export", two_tracks())

    assert result == IngestResult(artists_added=1, songs_added=2, songs_updated=0, tracks_matched=1)
    matched = songs(db)[loved_doc_id(USER, MATCHED_KEY)]  # no {uid}:{kn_id} row yet: the song_key row
    assert matched["song_key"] == MATCHED_KEY
    assert matched["source"] == "spotify_export" and matched["sources"] == ["spotify_export"]
    assert matched["playcount"] == 5 and matched["times_sung"] == 0
    assert matched["has_karaoke_version"] is True
    assert matched["last_played_at"] == "2023-11-14T22:13:20+00:00"
    unmatched_id = f"{USER}:spotify_export:the lanterns:another harbour"
    assert songs(db)[unmatched_id]["has_karaoke_version"] is False
    assert "song_key" not in songs(db)[unmatched_id]

    (artist,) = db.col("user_artists").values()
    assert artist["artist_name"] == "The Lanterns"
    assert artist["artist_key"] == "the lanterns"
    assert artist["playcount"] == 12 and artist["rank"] == 1 and artist["source"] == "spotify_export"
    assert f"{USER}:spotify_export:the_lanterns" in db.col("user_artists")


@pytest.mark.asyncio
async def test_new_docs_have_the_sync_field_set() -> None:
    db = FakeFirestore()
    await make_service(db).ingest(USER, "screenshot", two_tracks())
    doc = songs(db)[loved_doc_id(USER, MATCHED_KEY)]
    sync_fields = {
        "id", "user_id", "song_id", "source", "sync_count", "playcount", "rank", "last_played_at", "is_saved",
        "times_sung", "last_sung_at", "average_rating", "notes", "artist", "title", "has_karaoke_version",
        "spotify_popularity", "duration_ms", "explicit", "created_at", "updated_at",
    }  # fmt: skip
    assert sync_fields <= set(doc)
    assert doc["id"] == loved_doc_id(USER, MATCHED_KEY)
    assert doc["is_saved"] is False


@pytest.mark.asyncio
async def test_max_mode_does_not_double() -> None:
    db = FakeFirestore()
    svc = make_service(db)
    await svc.ingest(USER, "spotify_export", two_tracks(5))
    second = await svc.ingest(USER, "spotify_export", two_tracks(5))
    assert second.songs_added == 0 and second.songs_updated == 2
    assert songs(db)[loved_doc_id(USER, MATCHED_KEY)]["playcount"] == 5
    (artist,) = db.col("user_artists").values()
    assert artist["playcount"] == 12


@pytest.mark.asyncio
async def test_add_mode_accumulates() -> None:
    db = FakeFirestore()
    svc = make_service(db)
    await svc.ingest(USER, "listenbrainz", [IngestTrack(*MATCHED, plays=5)], play_mode="add")
    await svc.ingest(USER, "listenbrainz", [IngestTrack(*MATCHED, plays=3)], play_mode="add")
    assert songs(db)[loved_doc_id(USER, MATCHED_KEY)]["playcount"] == 8


@pytest.mark.asyncio
async def test_duplicate_tracks_in_one_ingest_sum_into_one_row() -> None:
    db = FakeFirestore()
    result = await make_service(db).ingest(
        USER, "apple_export", [IngestTrack(*MATCHED, plays=2), IngestTrack(*MATCHED, plays=3)]
    )
    assert result.songs_added == 1
    assert songs(db)[loved_doc_id(USER, MATCHED_KEY)]["playcount"] == 5


@pytest.mark.asyncio
async def test_existing_doc_keeps_source_adds_sources() -> None:
    db = FakeFirestore()
    kn_row = f"{USER}:{KN_ID}"
    db.col("user_songs")[kn_row] = {"user_id": USER, "song_id": str(KN_ID), "source": "lastfm", "playcount": 9}
    result = await make_service(db).ingest(USER, "spotify_export", [IngestTrack(*MATCHED, plays=4)])

    assert result.songs_updated == 1 and result.songs_added == 0
    row = songs(db)[kn_row]
    assert row["source"] == "lastfm"
    assert row["sources"] == ["lastfm", "spotify_export"]
    assert row["playcount"] == 9  # max(9, 4)
    assert row["song_key"] == MATCHED_KEY
    assert loved_doc_id(USER, MATCHED_KEY) not in songs(db)


@pytest.mark.asyncio
async def test_tonight_row_keeps_times_sung_and_gains_source() -> None:
    """A song logged by Tonight (karaoke log) twice keeps its count after an import (A3)."""
    db = FakeFirestore()
    kn_row = f"{USER}:{KN_ID}"
    tonight = {
        "id": kn_row,
        "user_id": USER,
        "song_id": str(KN_ID),
        "song_key": MATCHED_KEY,
        "source": "karaoke_log",
        "times_sung": 2,
        "last_sung_at": "2026-10-20T21:00:00+00:00",
        "loved": True,
        "hidden": False,
        "artist": MATCHED[0],
        "title": MATCHED[1],
    }
    db.col("user_songs")[kn_row] = copy.deepcopy(tonight)
    await make_service(db).ingest(USER, "spotify_export", [IngestTrack(*MATCHED, plays=7)])

    row = songs(db)[kn_row]
    for field in ("times_sung", "last_sung_at", "loved", "hidden", "source"):
        assert row[field] == tonight[field]
    assert row["sources"] == ["karaoke_log", "spotify_export"]
    assert row["playcount"] == 7
    assert len(songs(db)) == 1


@pytest.mark.asyncio
async def test_explicit_artists_are_stored_with_ranks() -> None:
    db = FakeFirestore()
    result = await make_service(db).ingest(
        USER,
        "spotify_export",
        [],
        artists=[IngestArtist("Zed Fixture", plays=30), IngestArtist("米津玄師", plays=20, rank=7)],
    )
    assert result.artists_added == 2
    by_name = {a["artist_name"]: a for a in db.col("user_artists").values()}
    assert by_name["Zed Fixture"]["rank"] == 1
    assert by_name["米津玄師"]["rank"] == 7
    # A name with no a-z/0-9 still gets its own, stable doc id (not "fixture-user:spotify_export:____").
    ids = list(db.col("user_artists"))
    assert len(set(ids)) == 2 and not any(i.endswith(":____") for i in ids)


@pytest.mark.asyncio
async def test_without_catalog_reader_matched_rows_use_the_kn_id() -> None:
    """catalog.db still warming up (BigQuery match): no song_key, the legacy {uid}:{song_id} row."""
    db = FakeFirestore()
    svc = ImportIngestService(db, track_matcher=FakeMatcher(), reader=lambda: None)  # type: ignore[arg-type]
    await svc.ingest(USER, "screenshot", [IngestTrack(*MATCHED, plays=1)])
    assert f"{USER}:{KN_ID}" in songs(db)


@pytest.mark.asyncio
@patch("backend.services.firestore_service.firestore.AsyncClient")
async def test_batches_writes_in_chunks_of_500(mock_async_client: MagicMock) -> None:
    """1,200 tracks: three user_songs batch commits (FirestoreService.batch_set chunks at 400 ≤ 500)."""
    commits: list[tuple[str, int]] = []

    def new_batch() -> MagicMock:
        b = MagicMock()
        b.sets = []
        b.set.side_effect = lambda ref, data, merge=False: b.sets.append(ref.path)
        b.commit = AsyncMock(side_effect=lambda: commits.append((b.sets[0], len(b.sets))))
        return b

    async def get_all(refs: list[MagicMock]) -> AsyncGenerator[MagicMock, None]:
        for _ in refs:
            snap = MagicMock()
            snap.exists = False
            yield snap

    client = mock_async_client.return_value
    client.batch.side_effect = new_batch
    client.get_all = get_all
    client.collection.side_effect = lambda name: MagicMock(document=lambda i: MagicMock(path=name))
    firestore = FirestoreService(BackendSettings())
    svc = ImportIngestService(firestore, track_matcher=FakeMatcher(), reader=lambda: FakeReader())  # type: ignore[arg-type]

    tracks = [IngestTrack("e2e-band", f"Fixture Song {i}", plays=1) for i in range(1200)]
    result = await svc.ingest(USER, "spotify_export", tracks)

    assert result.songs_added == 1200
    assert [n for col, n in commits if col == "user_songs"] == [400, 400, 400]


@pytest.mark.asyncio
async def test_matcher_is_called_once_per_ingest() -> None:
    matcher = FakeMatcher()
    svc = ImportIngestService(FakeFirestore(), track_matcher=matcher, reader=lambda: FakeReader())  # type: ignore[arg-type]
    await svc.ingest(USER, "spotify_playlist", two_tracks())
    assert matcher.calls == 1


@pytest.mark.asyncio
async def test_remove_source_data_only_that_source() -> None:
    db = FakeFirestore()
    svc = make_service(db)
    other_kn_row = f"{USER}:{KN_ID}"
    db.col("user_songs")[other_kn_row] = {"user_id": USER, "source": "lastfm", "playcount": 9}
    db.col("user_songs")["someone:1"] = {"user_id": "someone", "source": "spotify_export"}
    db.col("user_artists")[f"{USER}:lastfm:the_lanterns"] = {"user_id": USER, "source": "lastfm"}
    await svc.ingest(USER, "spotify_export", two_tracks())  # matched lands on the lastfm row
    await svc.record_import(USER, "spotify_export", status="done", result=None)

    removed = await svc.remove_source_data(USER, "spotify_export")

    assert removed == (1, 1)  # the unmatched song row, the derived artist
    assert set(songs(db)) == {other_kn_row, "someone:1"}
    assert songs(db)[other_kn_row]["sources"] == ["lastfm"]
    assert songs(db)[other_kn_row]["source"] == "lastfm"
    assert set(db.col("user_artists")) == {f"{USER}:lastfm:the_lanterns"}
    assert await svc.get_import(USER, "spotify_export") is None


@pytest.mark.asyncio
async def test_remove_source_data_keeps_sung_and_loved_rows() -> None:
    """A row the import created but Tonight has since counted (or the user loved) survives removal."""
    db = FakeFirestore()
    svc = make_service(db)
    await svc.ingest(USER, "screenshot", two_tracks())
    row_id = loved_doc_id(USER, MATCHED_KEY)
    db.col("user_songs")[row_id]["times_sung"] = 1
    db.col("user_songs")[f"{USER}:screenshot:the lanterns:another harbour"]["loved"] = True

    removed = await svc.remove_source_data(USER, "screenshot")

    assert removed[0] == 0
    sung = songs(db)[row_id]
    assert sung["times_sung"] == 1 and sung["source"] == "karaoke_log" and sung["sources"] == []
    assert "playcount" not in sung or sung["playcount"] is None
    assert songs(db)[f"{USER}:screenshot:the lanterns:another harbour"]["source"] == "manual"


@pytest.mark.asyncio
async def test_remove_promotes_another_source() -> None:
    db = FakeFirestore()
    svc = make_service(db)
    await svc.ingest(USER, "screenshot", [IngestTrack(*MATCHED, plays=1)])
    await svc.ingest(USER, "spotify_playlist", [IngestTrack(*MATCHED)])
    await svc.remove_source_data(USER, "screenshot")
    row = songs(db)[loved_doc_id(USER, MATCHED_KEY)]
    assert row["source"] == "spotify_playlist" and row["sources"] == ["spotify_playlist"]


@pytest.mark.asyncio
async def test_record_and_get_import() -> None:
    db = FakeFirestore()
    svc = make_service(db)
    result = IngestResult(artists_added=3, songs_added=10, songs_updated=2, tracks_matched=4)
    await svc.record_import(USER, "spotify_export", status="done", result=result, extra={"chunk_count": 2})
    await svc.record_import(USER, "spotify_export", status="done", result=None, extra={"note": "x"})

    doc = await svc.get_import(USER, "spotify_export")
    assert doc is not None
    assert doc["source"] == "spotify_export" and doc["status"] == "done"
    assert doc["artists"] == 3 and doc["songs"] == 12
    assert doc["chunk_count"] == 2 and doc["note"] == "x"  # merged, not replaced
    assert isinstance(doc["last_import_at"], str)
    assert f"{USER}:spotify_export" in db.col("user_imports")
    assert await svc.get_import(USER, "screenshot") is None


@pytest.mark.asyncio
async def test_empty_ingest_writes_nothing() -> None:
    db = FakeFirestore()
    result = await make_service(db).ingest(USER, "screenshot", [])
    assert result == IngestResult(0, 0, 0, 0)
    assert db.batch_set_calls == []


async def test_blank_tracks_are_skipped() -> None:
    db = FakeFirestore()
    result = await make_service(db).ingest(
        USER, "screenshot", [IngestTrack("", "Untitled"), IngestTrack("e2e-band", "  "), IngestTrack(*MATCHED)]
    )
    assert result.songs_added == 1 and len(songs(db)) == 1


def test_dep_is_exported() -> None:
    from backend.api import deps

    assert deps.ImportIngestServiceDep is not None


@pytest.mark.asyncio
async def test_sources_written_as_array_union_so_concurrent_ingests_keep_both() -> None:
    """Two sources that both read the row before either wrote still end up in ``sources``."""
    db = FakeFirestore()
    kn_row = f"{USER}:{KN_ID}"
    db.col("user_songs")[kn_row] = {"user_id": USER, "song_id": str(KN_ID), "source": "lastfm", "sources": ["lastfm"]}
    written: list[dict[str, Any]] = []
    real_batch_set = db.batch_set

    async def spy(collection: str, docs: dict[str, dict[str, Any]], merge: bool = False) -> None:
        written.extend(docs.values())
        await real_batch_set(collection, docs, merge=merge)

    db.batch_set = spy  # type: ignore[method-assign]
    service = make_service(db)
    # Both ingests have already read the row (sources == ["lastfm"]) when they write.
    await asyncio.gather(
        service.ingest(USER, "spotify_export", [IngestTrack(*MATCHED, plays=1)]),
        service.ingest(USER, "screenshot", [IngestTrack(*MATCHED)]),
    )
    song_writes = [d for d in written if "sources" in d]
    assert song_writes and all(isinstance(d["sources"], firestore.ArrayUnion) for d in song_writes)
    assert set(songs(db)[kn_row]["sources"]) == {"lastfm", "spotify_export", "screenshot"}


@pytest.mark.asyncio
async def test_legacy_row_without_sources_keeps_its_source_in_the_union() -> None:
    db = FakeFirestore()
    kn_row = f"{USER}:{KN_ID}"
    db.col("user_songs")[kn_row] = {"user_id": USER, "song_id": str(KN_ID), "source": "lastfm"}
    await make_service(db).ingest(USER, "screenshot", [IngestTrack(*MATCHED)])
    assert songs(db)[kn_row]["sources"] == ["lastfm", "screenshot"]


def test_later_handles_naive_vs_aware_datetimes() -> None:
    from backend.services.import_ingest_service import _later

    aware, naive = "2026-10-01T00:00:00+00:00", "2026-10-02T00:00:00"
    assert _later(aware, naive) == naive  # comparing them raises TypeError; the new value wins
    assert _later(None, aware) == aware
    assert _later("2026-10-03T00:00:00+00:00", aware) == "2026-10-03T00:00:00+00:00"


def test_iso_is_defensive_about_out_of_range_timestamps() -> None:
    assert _iso(None) is None
    assert _iso(1_700_000_000) is not None
    assert _iso(1_700_000_000_000_000) is None  # microseconds-scale: year out of range
    assert _iso(10**30) is None
