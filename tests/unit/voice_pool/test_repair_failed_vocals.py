"""scripts/voice/repair_failed_vocals.py: failed songs whose vocals_gcs was gen's bare object name go
back to separated (fake sync Firestore + fake GCS; no GCP)."""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from karaoke_decide.voice_pool import queue, separate
from tests.unit.voice_pool.conftest import FakeGcs

FETCH = queue.FETCH_COLLECTION
GEN = separate.GEN_BUCKET
SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "voice" / "repair_failed_vocals.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("repair_failed_vocals_script", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # @dataclass looks its module up
    spec.loader.exec_module(mod)
    return mod


repair = _load()


class _Snap:
    def __init__(self, doc_id: str, data: dict[str, Any]) -> None:
        self.id, self._data = doc_id, data

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)


class _Ref:
    def __init__(self, db: FakeDb, col: str, doc_id: str) -> None:
        self.db, self.col, self.doc_id = db, col, doc_id

    def update(self, data: dict[str, Any]) -> None:
        self.db.updates.append((self.doc_id, dict(data)))
        self.db.cols[self.col][self.doc_id].update(data)


class _Query:
    def __init__(self, db: FakeDb, col: str, filters: list[tuple[str, str, Any]]) -> None:
        self.db, self.col, self.filters = db, col, filters

    def where(self, field: str, op: str, value: Any) -> _Query:
        return _Query(self.db, self.col, [*self.filters, (field, op, value)])

    def document(self, doc_id: str) -> _Ref:
        return _Ref(self.db, self.col, doc_id)

    def stream(self) -> list[_Snap]:
        docs = self.db.cols.get(self.col, {})
        return [_Snap(i, d) for i, d in docs.items() if all(op == "==" and d.get(f) == v for f, op, v in self.filters)]


class FakeDb:
    """The sync ``firestore.Client`` subset the script uses."""

    def __init__(self, docs: dict[str, dict[str, Any]]) -> None:
        self.cols = {FETCH: copy.deepcopy(docs)}
        self.updates: list[tuple[str, dict[str, Any]]] = []

    def collection(self, name: str) -> _Query:
        return _Query(self, name, [])


def _stem(pid: str) -> str:
    return f"voice-pool/stems/{pid}/vocals.flac"


@pytest.fixture
def world() -> tuple[FakeDb, FakeGcs]:
    docs = {
        "bare": {"status": "failed", "vocals_gcs": _stem("bare"), "error": "vocals stem missing", "attempts": 0},
        "hint": {
            "status": "failed",
            "vocals_gcs": None,
            "attempts": 1,
            "error": f"has no vocals at {_stem('hint')}",
        },  # fmt: skip
        "nostem": {"status": "failed", "vocals_gcs": _stem("nostem"), "error": "vocals stem missing"},
        "uri_ok": {"status": "failed", "vocals_gcs": f"gs://{GEN}/{_stem('uri_ok')}", "error": "analysis: ValueError"},
        "dl_fail": {"status": "failed", "vocals_gcs": None, "error": "flacfetch: no source", "attempts": 3},
        "separated": {"status": "separated", "vocals_gcs": _stem("separated"), "error": None},
    }
    gcs = FakeGcs()
    for pid in ("bare", "hint", "uri_ok", "separated"):
        gcs.bucket(GEN).objects[_stem(pid)] = b"fake flac"
    return FakeDb(docs), gcs


def test_dry_run_prints_and_writes_nothing(world, capsys) -> None:
    db, gcs = world
    before = copy.deepcopy(db.cols)
    assert repair.main(["--dry-run"], db=db, gcs=gcs) == 0
    out = capsys.readouterr().out
    assert db.updates == [] and db.cols == before
    assert "WOULD RESET bare" in out and "WOULD RESET hint" in out and "SKIP nostem" in out
    assert "uri_ok" not in out and "dl_fail" not in out and "separated:" not in out
    assert "would reset 2 docs; 1 skipped" in out


def test_apply_resets_only_affected_docs_with_existing_stems(world) -> None:
    db, gcs = world
    assert repair.main(["--apply"], db=db, gcs=gcs) == 0
    docs = db.cols[FETCH]
    for pid, attempts in (("bare", 0), ("hint", 1)):
        d = docs[pid]
        assert (d["status"], d["vocals_gcs"], d["error"], d["attempts"]) == (
            "separated", f"gs://{GEN}/{_stem(pid)}", None, attempts)  # fmt: skip
    assert docs["nostem"]["status"] == "failed"  # stem missing: left alone
    assert docs["uri_ok"]["status"] == "failed" and docs["dl_fail"]["status"] == "failed"
    assert {pid for pid, _ in db.updates} == {"bare", "hint"}
    assert all(set(u) == {"status", "vocals_gcs", "error", "updated_at"} for _, u in db.updates)


def test_a_mode_flag_is_required(world) -> None:
    db, gcs = world
    with pytest.raises(SystemExit):
        repair.main([], db=db, gcs=gcs)
    with pytest.raises(SystemExit):
        repair.main(["--dry-run", "--apply"], db=db, gcs=gcs)
    assert db.updates == []


def test_stem_name_rules() -> None:
    assert repair.stem_name("p", {"status": "failed", "vocals_gcs": _stem("p")}) == _stem("p")
    assert repair.stem_name("p", {"status": "analysed", "vocals_gcs": _stem("p")}) is None
    assert repair.stem_name("p", {"status": "failed", "vocals_gcs": "gs://x/y", "error": "other"}) is None
    hinted = {"status": "failed", "vocals_gcs": f"gs://{GEN}/{_stem('p')}", "error": f"no vocals at {_stem('p')}"}
    assert repair.stem_name("p", hinted) == _stem("p")
    assert repair.stem_name("p", {"status": "failed", "error": "has no vocals at voice-pool/stems/"}) == _stem("p")
