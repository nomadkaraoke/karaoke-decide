"""scripts/voice/export_session_fixture.py (fake read-only Firestore) and scripts/voice/sim_report.py."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from karaoke_decide.voice_session import replay

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = Path(__file__).parent / "fixtures" / "andrew_2026_10_25.json"
EMAIL = "Singer@Example.com"


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(f"{name}_script", ROOT / "scripts" / "voice" / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


export = _load("export_session_fixture")
sim_report = _load("sim_report")


class _Snap:
    def __init__(self, data: dict[str, Any] | None) -> None:
        self._data = copy.deepcopy(data)

    def to_dict(self) -> dict[str, Any] | None:
        return self._data


class _Ref:
    def __init__(self, data: dict[str, Any] | None) -> None:
        self._data = data

    def get(self) -> _Snap:
        return _Snap(self._data)


class _Col:
    def __init__(self, docs: dict[str, dict[str, Any]]) -> None:
        self._docs = docs
        self._filters: list[tuple[str, Any]] = []

    def document(self, doc_id: str) -> _Ref:
        return _Ref(self._docs.get(doc_id))

    def where(self, field: str, op: str, value: Any) -> _Col:
        assert op == "=="
        col = _Col(self._docs)
        col._filters = [*self._filters, (field, value)]
        return col

    def stream(self) -> list[_Snap]:
        return [_Snap(d) for d in self._docs.values() if all(d.get(f) == v for f, v in self._filters)]


class FakeDb:
    """Read-only: no set / update / delete exists, so any write would raise AttributeError."""

    def __init__(self, cols: dict[str, dict[str, dict[str, Any]]]) -> None:
        self.cols = cols

    def collection(self, name: str) -> _Col:
        return _Col(self.cols.setdefault(name, {}))


def _db() -> FakeDb:
    """Andrew's session as Firestore stores it, with invented song keys, titles and ids."""
    fx = replay.load_fixture(FIXTURE)
    attempts = {}
    for a in fx["attempts"]:
        attempts[f"sess1:{a['n']}"] = {
            "user_id": "u1", "session_id": "sess1", "n": a["n"], "song_key": f"artist {a['n']}|song {a['n']}",
            "video_id": "VID-xyz", "quiz": a["quiz"], "pitch": a["pitch"],
            "context": {**a["context"], "record": True, "device": "Pixel"}, "stopped_early": a["stopped_early"],
            "recording_id": "REC-xyz", "song": {"artist": "Artist", "title": "Title", "stratum": "x", "demand": a["demand"]},
        }  # fmt: skip
    attempts["other:1"] = {**attempts["sess1:1"], "user_id": "u2", "session_id": "sess1"}  # never exported
    prior = {**fx["prior_profile"], "measured_at": "2026-10-25T00:00:00Z", "session_id": None}
    return FakeDb({
        export.USERS: {export.email_hash(EMAIL): {"user_id": "u1", "email": EMAIL.lower()}},
        export.SESSIONS: {"sess1": {"user_id": "u1", "state": "done", "skips": [{"song_key": "z", "done": True}],
                                    "setup": {**fx["setup"], "device": "Pixel"}, "prior_profile": prior}},
        export.ATTEMPTS: attempts,
    })  # fmt: skip


def test_export_matches_the_fixture_schema_replay_reads() -> None:
    fx = replay.load_fixture(FIXTURE)
    out = export.build_fixture(_db(), EMAIL, "sess1")
    assert set(out) == set(fx)
    assert out["setup"] == fx["setup"] and out["prior_profile"] == fx["prior_profile"]
    assert [{**a, "song_key": None} for a in out["attempts"]] == [{**a, "song_key": None} for a in fx["attempts"]]
    assert [a["song_key"] for a in out["attempts"]] == [export.short_key(f"artist {n}|song {n}") for n in (1, 2, 3, 4)]
    assert replay.replay_fixture(out) == replay.replay_fixture(fx)
    text = json.dumps(out)
    for leaked in ("Artist", "Title", "Pixel", "u1", "sess1", "VID-xyz", "REC-xyz", "artist 1", "example.com"):
        assert leaked not in text


def test_export_refuses_another_users_session() -> None:
    db = _db()
    db.cols[export.SESSIONS]["sess1"]["user_id"] = "u2"
    with pytest.raises(export.ExportError):
        export.build_fixture(db, EMAIL, "sess1")
    with pytest.raises(export.ExportError):
        export.build_fixture(_db(), "nobody@example.com", "sess1")


def test_export_dry_run_prints_and_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert export.main(["--email", EMAIL, "--session", "sess1", "--dry-run"], db=_db()) == 0
    printed = json.loads(capsys.readouterr().out)
    assert len(printed["attempts"]) == 4
    assert list(tmp_path.iterdir()) == []


def test_export_writes_out_file(tmp_path: Path) -> None:
    out = tmp_path / "fx.json"
    assert export.main(["--email", EMAIL, "--session", "sess1", "--out", str(out)], db=_db()) == 0
    assert replay.replay_fixture(replay.load_fixture(out)) == replay.replay_fixture(replay.load_fixture(FIXTURE))
    assert export.main(["--email", EMAIL, "--session", "nope", "--out", str(out)], db=_db()) == 1


def test_missing_demand_exports_null() -> None:
    a = export.export_attempt({"n": 1, "song_key": "a|b", "quiz": {}, "song": {}, "pitch": None})
    assert a["demand"] is None and a["pitch"] is None


def test_sim_report_prints_prior_pitch_k_table(capsys: pytest.CaptureFixture[str]) -> None:
    assert sim_report.main(["--n", "6", "--priors", "mic", "--pitches", "open"]) == 0
    out = capsys.readouterr().out
    assert "| prior | pitch | k | exact | adjacent | confident |" in out
    for k in (3, 4, 5, 10):
        assert f"| mic | open | {k} |" in out
