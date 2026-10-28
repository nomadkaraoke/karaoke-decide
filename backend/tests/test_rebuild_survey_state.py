"""scripts/rebuild_survey_state.py (ruling R18) against the in-memory Firestore of the karaoke-log tests.

The live path (``KaraokeLogService``) writes the expected state; the rebuild must reproduce it from
the log alone, change nothing when it is already right, and be idempotent."""

from __future__ import annotations

import copy
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any

import pytest

from backend.services.catalog_lookup import CatalogLookup
from backend.services.crowd_stats import CROWD_COLLECTION, crowd_doc_id
from backend.services.karaoke_log_service import KaraokeLogService
from backend.tests.test_karaoke_log_service import (
    APP_ID,
    APP_ID2,
    EMAIL,
    HELLO_KEY,
    T1,
    T2,
    T3,
    USER,
    FakeReader,
    FakeStore,
    MemoryFirestore,
    _app_entry,
    _backfill,
    _profile,
    _resolve_user,
)

ROOT = Path(__file__).resolve().parents[2]
APP_ID3 = "app_0b5c3a5e-3333-4222-8333-944445555666"
OTHER_KEY = "the marigolds|velvet staircase"  # invented
NOW = "2026-10-29T03:00:00+00:00"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location(
        "rebuild_survey_state_script", ROOT / "scripts" / "rebuild_survey_state.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


rb = _load()


@pytest.fixture
def fs() -> MemoryFirestore:
    return MemoryFirestore()


@pytest.fixture
def svc(fs: MemoryFirestore) -> KaraokeLogService:
    reader = FakeReader()
    reader.add_kn(4242, "The Lanterns", "Paper Harbour", HELLO_KEY)
    return KaraokeLogService(fs, catalog=CatalogLookup(FakeStore(reader)), resolve_user=_resolve_user)  # type: ignore[arg-type]


def _voice(fs: MemoryFirestore, user_id: str, voice_type: str | None) -> None:
    fs.cols.setdefault("decide_users", {})[f"doc_{user_id}"] = {
        "user_id": user_id,
        "voice_profile": {"low_midi": 40, "high_midi": 60, "voice_type": voice_type, "method": "mic"},
    }


async def _live_history(svc: KaraokeLogService, fs: MemoryFirestore, edits: bool = False) -> None:
    """Several singers, two songs, both buckets and no bucket, a backfill without a key, an
    unresolved song and a deleted record, all through the live path."""
    _profile(fs, "baritone")
    _voice(fs, "user_b", "tenor")
    _voice(fs, "user_c", None)
    await svc.upsert_user(
        USER,
        APP_ID,
        _app_entry(survey={"feel": "great", "key": "right", "crowd_pleaser": True, "answered_at": T1}),
        email=EMAIL,
    )
    await svc.upsert_user(
        USER, APP_ID2, _app_entry(survey={"feel": "ok", "key": "too_high", "answered_at": T2}), email=EMAIL
    )
    await svc.upsert_user(
        USER,
        APP_ID3,
        _app_entry(
            artist="The Marigolds",
            title="Velvet Staircase",
            song_key=OTHER_KEY,
            survey={"key": "too_low", "answered_at": T1},
        ),
        email=EMAIL,
    )
    await svc.delete(USER, APP_ID3)
    await svc.upsert_user(
        "user_b", APP_ID, _app_entry(survey={"key": "too_low", "crowd_pleaser": False, "answered_at": T2})
    )
    await svc.upsert_user(
        "user_b",
        APP_ID2,
        _app_entry(
            artist="The Marigolds",
            title="Velvet Staircase",
            song_key=OTHER_KEY,
            survey={"key": "right", "answered_at": T1},
        ),
    )
    await svc.upsert_user(
        "user_c", APP_ID, _app_entry(survey={"key": "right", "crowd_pleaser": True, "answered_at": T1})
    )
    await svc.upsert_user(
        "user_c",
        APP_ID2,
        _app_entry(
            artist="Nobody Known", title="Unlisted Tune", song_key=None, survey={"feel": "great", "answered_at": T1}
        ),
    )
    await svc.upsert_user(
        "user_d", APP_ID, _backfill(survey={"feel": "great", "crowd_pleaser": True, "answered_at": T1})
    )
    if edits:
        # a feel-only edit keeps the key's older stamp; a profile change keeps the stored bucket
        await svc.upsert_user(USER, APP_ID, {"survey": {"feel": "ok", "answered_at": T3}}, email=EMAIL)
        _voice(fs, "user_b", "bass")


def _rows(fs: MemoryFirestore) -> dict[str, dict[str, Any]]:
    return {rid: {f: row.get(f) for f in rb.DERIVED_FIELDS} for rid, row in fs.user_songs().items()}


def _crowd(fs: MemoryFirestore) -> dict[str, Any]:
    """Comparable crowd counts per doc, all-zero docs left out (the live path keeps them; the rebuild deletes them)."""
    out = {}
    for doc_id, doc in fs.cols.get(CROWD_COLLECTION, {}).items():
        counts = rb.crowd_counts(doc)
        if any(counts[1]) or counts[2] or counts[3] or counts[4]:
            out[doc_id] = counts
    return out


def _strip_to_pre4b(fs: MemoryFirestore) -> None:
    """What prod looks like before 4b: counters on the rows, no survey state, no crowd docs."""
    for row in fs.user_songs().values():
        for f in rb.DERIVED_FIELDS:
            row.pop(f, None)
    fs.cols.pop(CROWD_COLLECTION, None)


@pytest.mark.asyncio
async def test_live_state_is_left_unchanged(svc: KaraokeLogService, fs: MemoryFirestore) -> None:
    await _live_history(svc, fs, edits=True)
    before, live_crowd = copy.deepcopy(fs.cols), _crowd(fs)
    summary = await rb.rebuild(fs, apply=True, now=NOW)
    assert summary.rows_changed == 0 and summary.crowd_written == 0
    assert summary.rows == 5 and summary.crowd_songs == 2
    assert summary.bucket_lookups == 0  # every entry already has its bucket: nothing is re-stamped
    for col, docs in before.items():
        if col != CROWD_COLLECTION:
            assert fs.cols[col] == docs, col
    assert _crowd(fs) == live_crowd


@pytest.mark.asyncio
async def test_rebuild_from_pre4b_matches_live_path(svc: KaraokeLogService, fs: MemoryFirestore) -> None:
    await _live_history(svc, fs)
    live_rows, live_crowd = _rows(fs), _crowd(fs)
    assert live_crowd[crowd_doc_id(HELLO_KEY)][1] == (1, 1, 1)  # too_low (user_b), right (user_c), too_high (USER)
    _strip_to_pre4b(fs)
    summary = await rb.rebuild(fs, apply=True, now=NOW)
    assert summary.rows_changed == 5 and summary.crowd_written == 2
    assert _rows(fs) == live_rows
    assert _crowd(fs) == live_crowd
    hello = fs.cols[CROWD_COLLECTION][crowd_doc_id(HELLO_KEY)]
    assert hello["by_bucket"]["baritone"]["too_high"] == 1 and hello["by_bucket"]["tenor"]["too_low"] == 1
    assert (hello["crowd_pleaser_yes"], hello["crowd_pleaser_answered"]) == (
        2,
        3,
    )  # USER yes, user_b no, user_c yes; backfill w/o key: no vote


@pytest.mark.asyncio
async def test_apply_twice_gives_the_same_docs(svc: KaraokeLogService, fs: MemoryFirestore) -> None:
    await _live_history(svc, fs, edits=True)
    _strip_to_pre4b(fs)
    first = await rb.rebuild(fs, apply=True, now=NOW)
    assert first.rows_changed == 5
    after_first = copy.deepcopy(fs.cols)
    second = await rb.rebuild(fs, apply=True, now="2026-10-30T03:00:00+00:00")
    assert fs.cols == after_first  # nothing written, not even a timestamp
    assert (second.rows_changed, second.crowd_written, second.crowd_deleted) == (0, 0, 0)
    assert (second.entries_added, second.entries_removed) == (0, 0)


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(svc: KaraokeLogService, fs: MemoryFirestore) -> None:
    await _live_history(svc, fs)
    _strip_to_pre4b(fs)
    before = copy.deepcopy(fs.cols)
    summary = await rb.rebuild(fs, now=NOW)
    assert fs.cols == before
    assert summary.rows_changed == 5 and summary.crowd_written == 2 and not summary.apply
    assert "DRY RUN" in summary.lines()[0]


@pytest.mark.asyncio
async def test_crowd_rebuilt_from_scratch_not_added_to(svc: KaraokeLogService, fs: MemoryFirestore) -> None:
    await _live_history(svc, fs)
    live_crowd = _crowd(fs)
    hello = fs.cols[CROWD_COLLECTION][crowd_doc_id(HELLO_KEY)]
    hello["total"] = {"too_low": 9, "right": 9, "too_high": 9}  # drifted / double-counted
    fs.cols[CROWD_COLLECTION]["stale"] = {"song_key": "x|y", "total": {"right": 4}}
    summary = await rb.rebuild(fs, apply=True, now=NOW)
    assert (summary.crowd_written, summary.crowd_deleted) == (1, 1)
    assert "stale" not in fs.cols[CROWD_COLLECTION]
    assert _crowd(fs) == live_crowd


@pytest.mark.asyncio
async def test_drops_entries_of_records_that_no_longer_count(svc: KaraokeLogService, fs: MemoryFirestore) -> None:
    await _live_history(svc, fs)
    row = next(r for r in fs.user_songs().values() if r.get("user_id") == USER and r.get("song_key") == HELLO_KEY)
    row["sung_surveys"]["app_ghost"] = {
        "feel": "bad",
        "key": "too_low",
        "crowd_pleaser": None,
        "at": {},
        "bucket": None,
    }
    summary = await rb.rebuild(fs, apply=True, now=NOW)
    assert summary.entries_removed == 1 and "app_ghost" not in row_after(fs, USER)["sung_surveys"]


def row_after(fs: MemoryFirestore, user_id: str) -> dict[str, Any]:
    return next(r for r in fs.user_songs().values() if r.get("user_id") == user_id and r.get("song_key") == HELLO_KEY)


@pytest.mark.asyncio
async def test_missing_row_is_skipped_with_warning(
    svc: KaraokeLogService, fs: MemoryFirestore, caplog: pytest.LogCaptureFixture
) -> None:
    await _live_history(svc, fs)
    rid = next(k for k, r in fs.user_songs().items() if r.get("user_id") == "user_c")
    del fs.user_songs()[rid]
    with caplog.at_level(logging.INFO):
        summary = await rb.rebuild(fs, apply=True, now=NOW)
    assert summary.rows_missing == 1
    assert [r.levelname for r in caplog.records if "missing" in r.getMessage()] == ["WARNING"]
    assert all(r.levelno <= logging.WARNING for r in caplog.records)


def test_main_defaults_to_dry_run(monkeypatch: pytest.MonkeyPatch, fs: MemoryFirestore) -> None:
    seen: list[bool] = []

    async def fake_rebuild(_fs: Any, apply: bool = False, now: str | None = None) -> Any:
        seen.append(apply)
        return rb.Summary(apply=apply)

    monkeypatch.setattr(rb, "_firestore", lambda project, database: fs)
    monkeypatch.setattr(rb, "rebuild", fake_rebuild)
    assert rb.main([]) == 0
    assert rb.main(["--apply"]) == 0
    assert seen == [False, True]
