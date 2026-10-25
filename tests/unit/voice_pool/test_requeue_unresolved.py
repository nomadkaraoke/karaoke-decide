"""scripts/voice/requeue_unresolved.py: songs failed with "no spotify id" go back to queued so the
fetch job resolves them (fake sync Firestore; no GCP)."""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from karaoke_decide.voice_pool import queue
from tests.unit.voice_pool.test_repair_failed_vocals import FakeDb

FETCH = queue.FETCH_COLLECTION
SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "voice" / "requeue_unresolved.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("requeue_unresolved_script", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


requeue = _load()


def fake_requeue(db: FakeDb, doc_id: str, ts: Any) -> bool:
    """The transactional re-check against the fake (no transactions in FakeDb)."""
    if not requeue._still_unresolved(db.cols[FETCH].get(doc_id)):
        return False
    db.collection(FETCH).document(doc_id).update({"status": "queued", "error": None, "updated_at": ts})
    return True


@pytest.fixture
def db() -> FakeDb:
    return FakeDb({
        "sheet1": {"status": "failed", "error": "no spotify id", "attempts": 0, "source": "andrew_sheet",
                   "priority": 0, "song_key": "a|one"},
        "sheet2": {"status": "failed", "error": "no spotify id", "attempts": 0, "source": "andrew_sheet",
                   "priority": 0, "song_key": "a|two"},
        "dur": {"status": "failed", "error": "duration 0 ms outside flacfetch's 1-1200 s", "source": "shared",
                "priority": 1, "song_key": "b|dur"},
        "dl": {"status": "failed", "error": "flacfetch: no source", "attempts": 3, "source": "shared",
               "priority": 1, "song_key": "c|dl"},
        "queued": {"status": "queued", "error": None, "source": "personal", "priority": 2, "song_key": "d|q"},
    })  # fmt: skip


def test_dry_run_lists_and_writes_nothing(db, capsys) -> None:
    before = copy.deepcopy(db.cols)
    assert requeue.main(["--dry-run"], db=db) == 0
    out = capsys.readouterr().out
    assert db.updates == [] and db.cols == before
    assert "WOULD REQUEUE sheet1" in out and "WOULD REQUEUE sheet2" in out
    assert "b|dur" not in out and "c|dl" not in out and "d|q" not in out
    assert "would requeue 2 docs" in out and "andrew_sheet 2" in out


def test_apply_requeues_only_no_spotify_id_failures(db, capsys) -> None:
    assert requeue.main(["--apply"], db=db, requeue_one=fake_requeue) == 0
    docs = db.cols[FETCH]
    for pid in ("sheet1", "sheet2"):
        d = docs[pid]
        assert (d["status"], d["error"], d["source"], d["priority"], d["attempts"]) == (
            "queued", None, "andrew_sheet", 0, 0)  # fmt: skip
    assert docs["dur"]["status"] == "failed" and docs["dl"]["status"] == "failed"
    assert {pid for pid, _ in db.updates} == {"sheet1", "sheet2"}
    assert all(set(u) == {"status", "error", "updated_at"} for _, u in db.updates)
    assert "requeued 2 docs" in capsys.readouterr().out


def test_a_mode_flag_is_required(db) -> None:
    with pytest.raises(SystemExit):
        requeue.main([], db=db)
    with pytest.raises(SystemExit):
        requeue.main(["--dry-run", "--apply"], db=db)
    assert db.updates == []


def test_apply_skips_a_doc_that_moved_on_since_the_plan(db) -> None:
    changes = requeue.plan(db)
    db.cols[FETCH]["sheet1"].update(status="downloading", error=None)  # the fetch job claimed it meanwhile
    assert requeue.apply(db, changes, "TS", fake_requeue) == 1
    assert db.cols[FETCH]["sheet1"]["status"] == "downloading"
    assert db.cols[FETCH]["sheet2"]["status"] == "queued"
