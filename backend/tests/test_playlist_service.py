"""PlaylistService ordered setlists (in-memory Firestore + the fixture catalog)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.services.playlist_service import (
    MAX_ITEMS,
    PlaylistAccessDeniedError,
    PlaylistItemIn,
    PlaylistService,
)
from backend.tests.test_catalog_facets import LOUD, SOFT, build_catalog
from backend.tests.test_karaoke_log_service import USER, FakeStore, MemoryFirestore

OTHER = "user_other"
PL_ID = "pl_abcdef123456"


def _svc(fs: MemoryFirestore, tmp_path: Path | None = None) -> PlaylistService:
    store = FakeStore(build_catalog(tmp_path) if tmp_path else None)  # type: ignore[arg-type]
    return PlaylistService(MagicMock(), fs, store)  # type: ignore[arg-type]


@pytest.fixture
def fs() -> MemoryFirestore:
    return MemoryFirestore()


@pytest.mark.asyncio
async def test_create_with_client_id_is_idempotent(fs: MemoryFirestore) -> None:
    svc = _svc(fs)
    first = await svc.create_playlist(USER, "Friday", playlist_id=PL_ID)
    second = await svc.create_playlist(USER, "Renamed", playlist_id=PL_ID)
    assert first.id == second.id == PL_ID
    assert second.name == "Friday"
    assert len(fs.cols["playlists"]) == 1


@pytest.mark.asyncio
async def test_create_with_other_users_id_denied(fs: MemoryFirestore) -> None:
    svc = _svc(fs)
    await svc.create_playlist(OTHER, "Theirs", playlist_id=PL_ID)
    with pytest.raises(PlaylistAccessDeniedError):
        await svc.create_playlist(USER, "Mine", playlist_id=PL_ID)


@pytest.mark.asyncio
async def test_create_with_items(fs: MemoryFirestore) -> None:
    pl = await _svc(fs).create_playlist(
        USER, "Set", playlist_id=PL_ID, items=[PlaylistItemIn(song_key=LOUD, artist="A", title="T")]
    )
    assert [i.song_key for i in pl.items] == [LOUD]
    assert pl.song_count == 1 and pl.items[0].item_id


@pytest.mark.asyncio
async def test_replace_items_accepts_keyed_item_with_empty_names(fs: MemoryFirestore) -> None:
    svc = _svc(fs)
    await svc.create_playlist(USER, "Set", playlist_id=PL_ID)
    await svc.replace_items(PL_ID, USER, [PlaylistItemIn(song_key="k|1", artist="", title="")], base_rev=0)
    stored = fs.cols["playlists"][PL_ID]["items"][0]
    assert stored["song_key"] == "k|1" and stored["artist"] == "" and stored["title"] == ""


def test_item_without_key_needs_both_names() -> None:
    with pytest.raises(ValueError):
        PlaylistItemIn(song_key=None, artist="A", title="")
    with pytest.raises(ValueError):
        PlaylistItemIn(song_key="", artist="", title="")
    assert PlaylistItemIn(song_key=None, artist="A", title="T").title == "T"


@pytest.mark.asyncio
async def test_replace_items_orders_and_bumps_rev(fs: MemoryFirestore) -> None:
    svc = _svc(fs)
    await svc.create_playlist(USER, "Set", playlist_id=PL_ID)
    pl = await svc.replace_items(
        PL_ID,
        USER,
        [
            PlaylistItemIn(song_key=SOFT, artist="Moss and Ember", title="Slow Tide"),
            PlaylistItemIn(song_key=LOUD, artist="The Lanterns", title="Paper Harbour"),
            PlaylistItemIn(song_key=None, artist="Nobody", title="Unlisted Song"),
        ],
        base_rev=0,
    )
    assert pl.rev == 1
    assert [i.song_key for i in pl.items] == [SOFT, LOUD, None]
    assert pl.items[2].artist == "Nobody" and pl.items[2].title == "Unlisted Song"
    assert pl.song_ids == [SOFT, LOUD]
    assert fs.cols["playlists"][PL_ID]["song_ids"] == [SOFT, LOUD]
    assert fs.cols["playlists"][PL_ID]["rev"] == 1


@pytest.mark.asyncio
async def test_replace_items_stale_base_rev_still_applies(fs: MemoryFirestore) -> None:
    svc = _svc(fs)
    await svc.create_playlist(USER, "Set", playlist_id=PL_ID)
    await svc.replace_items(PL_ID, USER, [PlaylistItemIn(song_key=LOUD, artist="a", title="b")], base_rev=0)
    pl = await svc.replace_items(PL_ID, USER, [PlaylistItemIn(song_key=SOFT, artist="c", title="d")], base_rev=0)
    assert pl.rev == 2
    assert [i.song_key for i in pl.items] == [SOFT]


@pytest.mark.asyncio
async def test_replace_items_keeps_item_id_and_added_at(fs: MemoryFirestore) -> None:
    svc = _svc(fs)
    pl = await svc.create_playlist(
        USER, "Set", playlist_id=PL_ID, items=[PlaylistItemIn(song_key=LOUD, artist="a", title="b")]
    )
    first = pl.items[0]
    pl2 = await svc.replace_items(
        PL_ID, USER, [PlaylistItemIn(item_id=first.item_id, song_key=LOUD, artist="a", title="b")], base_rev=None
    )
    assert pl2.items[0].item_id == first.item_id
    assert pl2.items[0].added_at == first.added_at


@pytest.mark.asyncio
async def test_replace_items_denied_for_other_user(fs: MemoryFirestore) -> None:
    svc = _svc(fs)
    await svc.create_playlist(OTHER, "Theirs", playlist_id=PL_ID)
    with pytest.raises(PlaylistAccessDeniedError):
        await svc.replace_items(PL_ID, USER, [], base_rev=None)


@pytest.mark.asyncio
async def test_replace_items_over_limit_rejected(fs: MemoryFirestore) -> None:
    svc = _svc(fs)
    await svc.create_playlist(USER, "Set", playlist_id=PL_ID)
    items = [PlaylistItemIn(song_key=None, artist="a", title=str(n)) for n in range(501)]
    with pytest.raises(ValueError):
        await svc.replace_items(PL_ID, USER, items, base_rev=None)


def _legacy(fs: MemoryFirestore, song_ids: list[str]) -> None:
    fs.cols.setdefault("playlists", {})[PL_ID] = {
        "id": PL_ID,
        "user_id": USER,
        "name": "Old",
        "description": None,
        "song_ids": song_ids,
        "song_count": len(song_ids),
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
    }


@pytest.mark.asyncio
async def test_legacy_doc_upgraded_on_read(fs: MemoryFirestore, tmp_path: Path) -> None:
    _legacy(fs, [LOUD, "mystery|key"])
    pl = await _svc(fs, tmp_path).get_playlist(PL_ID, USER)
    assert [(i.song_key, i.artist, i.title) for i in pl.items] == [
        (LOUD, "The Lanterns", "Paper Harbour"),
        ("mystery|key", "mystery|key", "mystery|key"),
    ]
    assert "items" not in fs.cols["playlists"][PL_ID]  # in memory only


@pytest.mark.asyncio
async def test_legacy_add_song_appends_item_with_titles(fs: MemoryFirestore, tmp_path: Path) -> None:
    _legacy(fs, [LOUD])
    pl = await _svc(fs, tmp_path).add_song_to_playlist(PL_ID, USER, SOFT)
    assert [i.song_key for i in pl.items] == [LOUD, SOFT]
    assert pl.items[1].title == "Slow Tide"
    stored: dict[str, Any] = fs.cols["playlists"][PL_ID]
    assert [i["song_key"] for i in stored["items"]] == [LOUD, SOFT]
    assert stored["song_ids"] == [LOUD, SOFT]


@pytest.mark.asyncio
async def test_legacy_remove_song_keeps_items_in_sync(fs: MemoryFirestore, tmp_path: Path) -> None:
    _legacy(fs, [LOUD, SOFT])
    pl = await _svc(fs, tmp_path).remove_song_from_playlist(PL_ID, USER, LOUD)
    assert [i.song_key for i in pl.items] == [SOFT]
    assert pl.song_ids == [SOFT]


@pytest.mark.asyncio
async def test_warming_catalog_falls_back_to_song_id(fs: MemoryFirestore) -> None:
    _legacy(fs, [LOUD])
    pl = await _svc(fs).get_playlist(PL_ID, USER)
    assert pl.items[0].title == LOUD


@pytest.mark.asyncio
async def test_update_playlist_bumps_rev(fs: MemoryFirestore) -> None:
    svc = _svc(fs)
    await svc.create_playlist(USER, "Set", playlist_id=PL_ID)
    pl = await svc.update_playlist(PL_ID, USER, name="Renamed")
    assert pl.rev == 1
    assert fs.cols["playlists"][PL_ID]["rev"] == 1


@pytest.mark.asyncio
async def test_add_song_unnamed_stores_empty_names_but_shows_id(fs: MemoryFirestore) -> None:
    svc = _svc(fs)  # no catalog: cannot name anything
    await svc.create_playlist(USER, "Set", playlist_id=PL_ID)
    pl = await svc.add_song_to_playlist(PL_ID, USER, "mystery|key")
    stored = fs.cols["playlists"][PL_ID]["items"][0]
    assert stored["artist"] == "" and stored["title"] == ""
    assert (pl.items[0].artist, pl.items[0].title) == ("mystery|key", "mystery|key")
    assert pl.rev == 1


@pytest.mark.asyncio
async def test_empty_stored_names_repaired_on_read(fs: MemoryFirestore, tmp_path: Path) -> None:
    await _svc(fs).create_playlist(USER, "Set", playlist_id=PL_ID)
    await _svc(fs).add_song_to_playlist(PL_ID, USER, LOUD)  # warming: empty names stored
    assert fs.cols["playlists"][PL_ID]["items"][0]["title"] == ""
    pl = await _svc(fs, tmp_path).get_playlist(PL_ID, USER)
    assert (pl.items[0].artist, pl.items[0].title) == ("The Lanterns", "Paper Harbour")


@pytest.mark.asyncio
async def test_add_and_remove_use_transactions_and_bump_rev(fs: MemoryFirestore) -> None:
    svc = _svc(fs)
    await svc.create_playlist(USER, "Set", playlist_id=PL_ID)
    before = fs.transactions
    await svc.add_song_to_playlist(PL_ID, USER, LOUD)
    pl = await svc.remove_song_from_playlist(PL_ID, USER, LOUD)
    assert fs.transactions == before + 2
    assert pl.rev == 2 and pl.items == []


@pytest.mark.asyncio
async def test_add_song_limit(fs: MemoryFirestore) -> None:
    svc = _svc(fs)
    await svc.create_playlist(USER, "Set", playlist_id=PL_ID)
    await svc.replace_items(
        PL_ID, USER, [PlaylistItemIn(song_key=f"a|{n}", artist="a", title=str(n)) for n in range(MAX_ITEMS)], None
    )
    with pytest.raises(ValueError):
        await svc.add_song_to_playlist(PL_ID, USER, "new|song")
