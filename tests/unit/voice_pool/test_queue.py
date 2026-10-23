"""The voice-pool fetch queue (Firestore ``voice_pool_fetch``), against an in-memory fake."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from karaoke_decide.voice_pool import queue
from karaoke_decide.voice_pool.models import pool_id


def item(key: str, source: str = "shared", **kw):
    return {"song_key": key, "artist": f"Artist {key}", "title": f"Title {key}", "source": source, **kw}


async def test_enqueue_writes_full_doc(fs):
    added = await queue.enqueue(
        fs, [item("a|one", spotify_id="sp1", duration_ms=200_000, stratum="gender=male|decade=1990s")]
    )
    assert added == 1
    doc = fs.cols[queue.FETCH_COLLECTION][pool_id("a|one")]
    assert set(doc) == {
        "song_key", "artist", "title", "spotify_id", "duration_ms", "source", "priority", "batch_ids", "status",
        "attempts", "flacfetch_id", "audio_gcs", "vocals_gcs", "manifest_run", "error", "added_at", "updated_at",
        "stratum",
    }  # fmt: skip
    assert doc["spotify_id"] == "sp1" and doc["duration_ms"] == 200_000
    assert doc["priority"] == 1 and doc["status"] == "queued" and doc["attempts"] == 0
    assert doc["batch_ids"] == [] and doc["stratum"] == "gender=male|decade=1990s"
    assert doc["flacfetch_id"] is doc["audio_gcs"] is doc["vocals_gcs"] is doc["manifest_run"] is doc["error"] is None
    assert doc["added_at"] == doc["updated_at"] and doc["added_at"].tzinfo is not None
    assert queue.pool_id is pool_id  # re-exported for callers that import it from queue


async def test_enqueue_minimal_item_defaults(fs):
    assert await queue.enqueue(fs, [item("b|two", source="andrew_sheet")]) == 1
    doc = fs.cols[queue.FETCH_COLLECTION][pool_id("b|two")]
    assert doc["priority"] == 0 and doc["spotify_id"] is None and doc["duration_ms"] is None and doc["stratum"] is None


async def test_enqueue_skips_existing(fs):
    assert await queue.enqueue(fs, [item("a|one"), item("b|two")]) == 2
    fs.cols[queue.FETCH_COLLECTION][pool_id("a|one")]["status"] = "fetched"
    assert await queue.enqueue(fs, [item("a|one", source="andrew_sheet"), item("c|three"), item("c|three")]) == 1
    col = fs.cols[queue.FETCH_COLLECTION]
    assert len(col) == 3
    # The existing doc was not touched.
    assert col[pool_id("a|one")]["status"] == "fetched" and col[pool_id("a|one")]["source"] == "shared"


async def test_next_queued_orders_priority_then_age(fs):
    t0 = datetime(2026, 10, 22, tzinfo=UTC)
    col = fs.cols.setdefault(queue.FETCH_COLLECTION, {})

    def put(key: str, priority: int, minutes: int, status: str = "queued") -> None:
        col[pool_id(key)] = {
            "song_key": key, "priority": priority, "status": status, "added_at": t0 + timedelta(minutes=minutes),
        }  # fmt: skip

    put("shared|old", 1, 0)
    put("personal|oldest", 2, -60)
    put("sheet|new", 0, 30)
    put("shared|new", 1, 10)
    put("sheet|failed", 0, -90, status="failed")
    put("sheet|fetched", 0, -90, status="fetched")

    out = await queue.next_queued(fs, 3)
    assert [d["song_key"] for d in out] == ["sheet|new", "shared|old", "shared|new"]
    assert out[0]["id"] == pool_id("sheet|new")
    ((collection, filters, kw),) = fs.queries
    assert collection == queue.FETCH_COLLECTION and filters == [("status", "==", "queued")]
    assert "order_by" not in kw  # no composite index: sorted in Python
    assert [d["song_key"] for d in await queue.next_queued(fs, 10)][-1] == "personal|oldest"


def test_statuses():
    assert queue.STATUSES == ("queued", "downloading", "fetched", "separating", "separated", "analysed", "failed")
    assert queue.PRIORITY == {"andrew_sheet": 0, "shared": 1, "personal": 2}
