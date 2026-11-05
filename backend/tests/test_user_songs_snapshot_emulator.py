"""The snapshot's marker and transaction on real Firestore semantics. Skipped without the emulator."""

from __future__ import annotations

import os
import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.config import BackendSettings
from backend.services.firestore_service import USER_SONGS_SNAPSHOT_META as META
from backend.services.firestore_service import FirestoreService
from backend.services.user_songs_cache import HEAVY_ROWS
from backend.services.user_songs_snapshot import UserSongsSnapshots
from karaoke_decide.catalog.reader import CatalogReader

pytestmark = pytest.mark.skipif(not os.environ.get("FIRESTORE_EMULATOR_HOST"), reason="needs the Firestore emulator")


def _reader() -> MagicMock:
    import sqlite3

    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.execute("CREATE TABLE songs (song_key TEXT, artist TEXT, title TEXT)")
    r = MagicMock(spec=CatalogReader)
    r.kn_song_keys = lambda ids: {}
    r.has_karaoke_key = lambda key: False
    r.match_kn = lambda artist, title: None
    r.conn = lambda: db
    return r


def _row(user: str, i: int, **over: Any) -> dict[str, Any]:
    return {
        "user_id": user,
        "song_id": f"s{i}",
        "artist": "Invented Band",
        "title": f"Song {i}",
        "source": "lastfm",
        **over,
    }


async def test_marker_build_overlay_and_transaction_on_the_emulator() -> None:
    fs = FirestoreService(BackendSettings())
    user = f"user_{uuid.uuid4().hex}"
    await fs.batch_set("user_songs", {f"{user}:{i}": _row(user, i) for i in range(HEAVY_ROWS)})
    await fs.set_document("user_songs", f"{user}:x", _row(user, 99999))  # no meta doc yet: a no-op, no error

    snaps = UserSongsSnapshots(schedule=lambda _: None)
    built = await snaps.view(fs, _reader(), "r1", user)
    assert built is not None and built.row_count == HEAVY_ROWS + 1
    meta = await fs.get_document(META, user)
    assert meta is not None and meta["rev"] == 0 and meta["dirty_ids"] == []

    # single writes are listed, and every reader sees them
    await fs.set_document("user_songs", f"{user}:new", _row(user, 100000, loved=True))
    await fs.delete_document("user_songs", f"{user}:0")
    meta = await fs.get_document(META, user)
    assert meta is not None and meta["rev"] == 2
    assert sorted(meta["dirty_ids"]) == sorted([f"{user}:new", f"{user}:0"])
    view = await UserSongsSnapshots(schedule=lambda _: None).view(fs, _reader(), "r1", user)
    assert view is not None and view.row_count == HEAVY_ROWS + 1

    # a write in a transaction is reported as well
    async def tx(t: Any) -> None:
        t.set("user_songs", f"{user}:tx", _row(user, 100001))

    await fs.run_transaction(tx)
    meta = await fs.get_document(META, user)
    assert meta is not None and meta["rev"] == 3 and f"{user}:tx" in meta["dirty_ids"]

    # a bulk write goes stale, and a rebuild clears it
    await fs.batch_set("user_songs", {f"{user}:b{i}": _row(user, 200000 + i) for i in range(60)})
    meta = await fs.get_document(META, user)
    assert meta is not None and meta["stale"] is True and meta["dirty_ids"] == []
    assert await snaps.refresh(fs, _reader(), "r1", user) == "rebuilt"
    meta = await fs.get_document(META, user)
    assert meta is not None and meta["stale"] is False and meta["row_count"] == HEAVY_ROWS + 62
