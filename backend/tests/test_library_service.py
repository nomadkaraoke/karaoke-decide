"""LibraryService: aggregates the karaoke log + loves + setlists into the Me library (in-memory Firestore)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.services.karaoke_log_service import LOG_COLLECTION, log_doc_id
from backend.services.library_service import LibraryItem, LibraryService
from backend.services.song_feedback_service import SongFeedbackService
from backend.tests.test_catalog_facets import KN_ONLY, LOUD, SOFT, build_catalog
from backend.tests.test_karaoke_log_service import USER, FakeStore, MemoryFirestore
from karaoke_decide.song_search.text_normalize import song_key as make_song_key

OTHER = "user_other"


def _rec(entry_id: str, user_id: str = USER, **over: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "user_id": user_id,
        "source": "app",
        "state": "sung",
        "deleted": False,
        "artist": "The Lanterns",
        "title": "Paper Harbour",
        "song_key": LOUD,
        "song_key_status": "resolved",
        "sung_at": "2026-10-01T21:00:00+00:00",
        "origin": "live",
        "times": 1,
    }
    doc.update(over)
    return doc


def _put(fs: MemoryFirestore, entry_id: str, user_id: str = USER, **over: Any) -> None:
    fs.cols.setdefault(LOG_COLLECTION, {})[log_doc_id(user_id, entry_id)] = _rec(entry_id, user_id, **over)


def _svc(fs: MemoryFirestore, tmp_path: Path | None = None, warming: bool = False) -> LibraryService:
    store = FakeStore(None if warming or tmp_path is None else build_catalog(tmp_path))  # type: ignore[arg-type]
    return LibraryService(fs, SongFeedbackService(fs), MagicMock(PLAYLISTS_COLLECTION="playlists"), store)  # type: ignore[arg-type]


def _by_key(items: list[LibraryItem]) -> dict[str, LibraryItem]:
    return {i.key: i for i in items}


@pytest.fixture
def fs() -> MemoryFirestore:
    return MemoryFirestore()


@pytest.mark.asyncio
async def test_library_groups_and_counts(fs: MemoryFirestore) -> None:
    _put(fs, "app_1", survey={"feel": "great", "answered_at": "2026-10-01T22:00:00+00:00"})
    _put(fs, "app_2", origin="backfill", times=3, sung_at=None, night={"date": "2025-06", "date_precision": "month"})
    resp = await _svc(fs).get_library(USER)
    assert len(resp.items) == 1
    item = resp.items[0]
    assert item.key == LOUD and item.song_key == LOUD
    assert item.times_sung == 4
    assert item.backfill_times == 3
    assert item.feel_counts == {"great": 1, "ok": 0, "bad": 0}
    assert item.crowd_pleaser_count == 0
    assert item.first_sung_at is not None and item.last_sung_at is not None
    assert item.first_sung_at <= item.last_sung_at


@pytest.mark.asyncio
async def test_library_backfill_without_date_is_skipped_for_dates(fs: MemoryFirestore) -> None:
    _put(fs, "app_1", origin="backfill", times=2, sung_at=None, night=None)
    item = (await _svc(fs).get_library(USER)).items[0]
    assert item.times_sung == 2 and item.first_sung_at is None and item.last_sung_at is None


@pytest.mark.asyncio
async def test_library_latest_survey_wins(fs: MemoryFirestore) -> None:
    _put(
        fs,
        "app_1",
        survey={
            "feel": "great",
            "key": "right",
            "crowd_pleaser": True,
            "vibes": ["party"],
            "answered_at": "2026-09-01T22:00:00+00:00",
        },
    )
    _put(
        fs,
        "app_2",
        survey={
            "feel": "bad",
            "key": "too_high",
            "crowd_pleaser": False,
            "vibes": ["ballad"],
            "answered_at": "2026-10-02T22:00:00+00:00",
        },
    )
    item = (await _svc(fs).get_library(USER)).items[0]
    assert item.latest_feel == "bad" and item.latest_key == "too_high"
    assert item.feel_counts == {"great": 1, "ok": 0, "bad": 1}
    assert item.crowd_pleaser_count == 1
    assert sorted(item.vibes) == ["ballad", "party"]


@pytest.mark.asyncio
async def test_library_includes_loved_unsung_and_setlist_items(fs: MemoryFirestore) -> None:
    await SongFeedbackService(fs).love(USER, SOFT, "Moss and Ember", "Slow Tide")  # type: ignore[arg-type]
    fs.cols["playlists"] = {
        "pl_abcdefgh1": {
            "user_id": USER,
            "name": "Set A",
            "items": [{"song_key": KN_ONLY, "artist": "Old Cartwheel", "title": "Brass Lane"}],
        },
        "pl_legacy001": {"user_id": USER, "name": "Old", "song_ids": [LOUD]},
        "pl_noitemkey": {"user_id": USER, "name": "Free", "items": [{"artist": "Someone", "title": "Else"}]},
        "pl_otheruser": {"user_id": OTHER, "name": "Theirs", "items": [{"song_key": "x|y"}]},
    }
    items = _by_key((await _svc(fs).get_library(USER)).items)
    assert set(items) == {SOFT, KN_ONLY, LOUD, "u:" + make_song_key("Someone", "Else")}
    assert items[SOFT].times_sung == 0 and items[SOFT].loved is True
    assert items[KN_ONLY].setlist_ids == ["pl_abcdefgh1"] and items[KN_ONLY].loved is False
    assert items[LOUD].setlist_ids == ["pl_legacy001"]
    assert items[KN_ONLY].artist == "Old Cartwheel" and items[KN_ONLY].title == "Brass Lane"


@pytest.mark.asyncio
async def test_library_uncatalogued(fs: MemoryFirestore) -> None:
    _put(fs, "app_1", artist="Nobody Known", title="Mystery Song", song_key=None, song_key_status="unresolved")
    resp = await _svc(fs, warming=True).get_library(USER)
    item = resp.items[0]
    assert item.key == "u:" + make_song_key("Nobody Known", "Mystery Song")
    assert item.song_key is None and item.facets is None


@pytest.mark.asyncio
async def test_library_excludes_hidden_only_and_deleted(fs: MemoryFirestore) -> None:
    await SongFeedbackService(fs).hide(USER, SOFT, "Moss and Ember", "Slow Tide")  # type: ignore[arg-type]
    _put(fs, "app_1", deleted=True)
    _put(fs, "app_2", state="planned")
    _put(fs, "app_3", state=None)
    _put(fs, "app_4", user_id=OTHER)
    resp = await _svc(fs).get_library(USER)
    assert resp.items == []


@pytest.mark.asyncio
async def test_library_hidden_but_sung_is_flagged(fs: MemoryFirestore) -> None:
    await SongFeedbackService(fs).hide(USER, LOUD, "The Lanterns", "Paper Harbour")  # type: ignore[arg-type]
    _put(fs, "app_1")
    item = (await _svc(fs).get_library(USER)).items[0]
    assert item.hidden is True and item.times_sung == 1


@pytest.mark.asyncio
async def test_library_facets(fs: MemoryFirestore, tmp_path: Path) -> None:
    from karaoke_decide.catalog.moods import MOODS

    _put(fs, "app_1")
    item = (await _svc(fs, tmp_path).get_library(USER)).items[0]
    assert item.facets is not None
    assert item.facets.mood in MOODS
    assert item.facets.energy_band == "high"
    assert item.facets.has_karaoke is True


@pytest.mark.asyncio
async def test_library_etag_stable_and_changes(fs: MemoryFirestore) -> None:
    _put(fs, "app_1")
    svc = _svc(fs)
    e1 = (await svc.get_library(USER)).etag
    assert e1 == (await svc.get_library(USER)).etag
    await SongFeedbackService(fs).love(USER, LOUD, "The Lanterns", "Paper Harbour")  # type: ignore[arg-type]
    assert (await svc.get_library(USER)).etag != e1
