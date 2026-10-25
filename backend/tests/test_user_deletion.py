"""delete_all_user_data: every registry store is erased for one user, others are untouched."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any, cast

import pytest

from backend.services import user_deletion
from backend.services.firestore_service import FirestoreService
from backend.services.user_deletion import USER_DATA_STORES, UserDataStore, delete_all_user_data, erase_voice_data
from backend.services.voice_recording_service import LocalBucket, user_prefix
from backend.tests.test_test_support import FakeFirestore

EMAIL_A = "Alice@Example.com"
EMAIL_B = "bob@example.com"
USER_A = "user_aaaa"
USER_B = "user_bbbb"


def _db(fs: FakeFirestore) -> FirestoreService:
    return cast(FirestoreService, fs)


def _hash(email: str) -> str:
    return hashlib.sha256(email.lower().encode()).hexdigest()


def _seed_store(fs: FakeFirestore, store: UserDataStore, user_id: str, email: str) -> str:
    """One doc for ``user_id`` in ``store``, keyed the way the store is matched. Returns its id."""
    col = fs.cols.setdefault(store.collection, {})
    data: dict[str, Any]
    if store.match == "doc_id_email_hash":
        doc_id, data = _hash(email), {"user_id": user_id, "email": email.lower()}
    elif store.match == "doc_id_user_id":
        doc_id, data = user_id, {"day": "2026-10-25", "count": 1}
    elif store.match == "email_field":
        doc_id, data = f"token-{user_id}", {store.field: email.lower()}
    else:
        doc_id, data = f"{user_id}:x", {store.field: user_id}
    col[doc_id] = data
    return doc_id


def _seed_user(fs: FakeFirestore, user_id: str, email: str) -> dict[str, str]:
    return {s.collection: _seed_store(fs, s, user_id, email) for s in USER_DATA_STORES}


@pytest.fixture
def fs() -> FakeFirestore:
    return FakeFirestore()


@pytest.fixture
def bucket(tmp_path: Path) -> LocalBucket:
    return LocalBucket(tmp_path / "recordings")


def _seed_voice(fs: FakeFirestore, bucket: LocalBucket, user_id: str) -> str:
    """Sessions, attempts, labels, a recording (object + doc), a purge marker and mixed user_songs."""
    fs.cols["voice_sessions"] = {
        "s1": {"user_id": user_id, "state": "done", "setup": {"record": True}},
        "sx": {"user_id": USER_B, "state": "active"},
    }
    fs.cols["voice_attempts"] = {
        "s1:1": {"user_id": user_id, "session_id": "s1", "n": 1, "recording_id": "r1"},
        "sx:1": {"user_id": USER_B, "session_id": "sx", "n": 1},
    }
    name = f"{user_prefix(user_id)}s1/1.m4a"
    bucket.blob(name).upload_from_file(io.BytesIO(b"synthetic"))
    other_name = f"{user_prefix(USER_B)}sx/1.m4a"
    bucket.blob(other_name).upload_from_file(io.BytesIO(b"synthetic"))
    fs.cols["voice_recordings"] = {
        "r1": {"user_id": user_id, "gcs_path": name},
        "rx": {"user_id": USER_B, "gcs_path": other_name},
    }
    fs.cols["voice_labels"] = {"l1": {"user_id": user_id, "song_key": "a|b"}, "lx": {"user_id": USER_B}}
    fs.cols["voice_recording_purges"] = {user_id: {"purged_at": "2026-10-01T00:00:00+00:00"}}
    fs.cols["user_songs"] = {
        f"{user_id}:only": {"user_id": user_id, "source": "voice_session", "sources": ["voice_session"]},
        f"{user_id}:mixed": {
            "user_id": user_id,
            "source": "voice_session",
            "sources": ["spotify_export", "voice_session"],
            "times_sung": 1,
            "session_feel": "loved",
            "session_tags": ["fun"],
        },
        f"{user_id}:plain": {"user_id": user_id, "source": "quiz", "sources": ["quiz"]},
        f"{USER_B}:only": {"user_id": USER_B, "source": "voice_session", "sources": ["voice_session"]},
    }
    return other_name


async def test_delete_all_removes_every_registry_store(fs: FakeFirestore, bucket: LocalBucket) -> None:
    a_ids = _seed_user(fs, USER_A, EMAIL_A)
    b_ids = _seed_user(fs, USER_B, EMAIL_B)

    counts = await delete_all_user_data(_db(fs), bucket, EMAIL_A)

    for store in USER_DATA_STORES:
        col = fs.cols[store.collection]
        assert a_ids[store.collection] not in col, store.collection
        assert b_ids[store.collection] in col, store.collection
        assert counts[store.collection] == 1, store.collection
    assert set(counts) == {s.collection for s in USER_DATA_STORES}


async def test_profile_is_deleted_last(fs: FakeFirestore, bucket: LocalBucket, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure part-way leaves the profile, so a retry still finds the user and finishes."""
    _seed_user(fs, USER_A, EMAIL_A)
    real = fs.batch_delete
    calls: list[str] = []

    async def failing_once(col: str, ids: list[str]) -> None:
        calls.append(col)
        if col == "playlists" and calls.count("playlists") == 1:
            raise RuntimeError("firestore blip")
        await real(col, ids)

    monkeypatch.setattr(fs, "batch_delete", failing_once)
    with pytest.raises(RuntimeError):
        await delete_all_user_data(_db(fs), bucket, EMAIL_A)
    assert _hash(EMAIL_A) in fs.cols["decide_users"]

    await delete_all_user_data(_db(fs), bucket, EMAIL_A)
    assert calls[-1] == "decide_users"
    assert all(not [d for d in col.values() if d.get("user_id") == USER_A] for col in fs.cols.values())


async def test_voice_contract(fs: FakeFirestore, bucket: LocalBucket) -> None:
    _seed_user(fs, USER_A, EMAIL_A)
    other_object = _seed_voice(fs, bucket, USER_A)

    counts = await delete_all_user_data(_db(fs), bucket, EMAIL_A)

    assert list(fs.cols["voice_sessions"]) == ["sx"]
    assert list(fs.cols["voice_attempts"]) == ["sx:1"]
    assert list(fs.cols["voice_recordings"]) == ["rx"]
    assert list(fs.cols["voice_labels"]) == ["lx"]
    assert USER_A not in fs.cols["voice_recording_purges"]
    assert [b.name for b in bucket.list_blobs()] == [other_object]
    assert list(fs.cols["user_songs"]) == [f"{USER_B}:only"]  # mixed rows go too: the whole account is erased
    assert counts["voice_recordings"] == 1 and counts["voice_sessions"] == 1 and counts["user_songs"] == 3


async def test_erase_voice_data_strips_session_fields_from_mixed_rows(fs: FakeFirestore, bucket: LocalBucket) -> None:
    """The 3d template (used by POST /api/test/voice/reset): mixed rows survive without session data."""
    _seed_voice(fs, bucket, USER_A)

    await erase_voice_data(_db(fs), bucket, USER_A)

    songs = fs.cols["user_songs"]
    assert f"{USER_A}:only" not in songs
    assert songs[f"{USER_A}:plain"] == {"user_id": USER_A, "source": "quiz", "sources": ["quiz"]}
    mixed = songs[f"{USER_A}:mixed"]
    assert mixed["source"] == "spotify_export" and mixed["sources"] == ["spotify_export"]
    assert not [k for k in mixed if k.startswith("session_")]
    assert mixed["times_sung"] == 1
    assert f"{USER_B}:only" in songs
    assert list(fs.cols["voice_sessions"]) == ["sx"]


async def test_unknown_email_noop(fs: FakeFirestore, bucket: LocalBucket) -> None:
    b_ids = _seed_user(fs, USER_B, EMAIL_B)
    before = {c: dict(docs) for c, docs in fs.cols.items()}

    assert await delete_all_user_data(_db(fs), bucket, "nobody@example.com") == {}
    assert fs.cols == before
    assert all(b_ids[c] in fs.cols[c] for c in b_ids)
    assert "decide_users" in fs.cols and _hash("nobody@example.com") not in fs.cols["decide_users"]  # not created


async def test_unknown_email_still_sweeps_magic_links(fs: FakeFirestore, bucket: LocalBucket) -> None:
    """A magic link sent before any profile existed is the email's data too."""
    fs.cols["magic_links"] = {"t1": {"email": "nobody@example.com"}, "t2": {"email": EMAIL_B}}
    assert await delete_all_user_data(_db(fs), bucket, "Nobody@Example.com") == {"magic_links": 1}
    assert list(fs.cols["magic_links"]) == ["t2"]


async def test_idempotent_second_call(fs: FakeFirestore, bucket: LocalBucket) -> None:
    _seed_user(fs, USER_A, EMAIL_A)
    _seed_voice(fs, bucket, USER_A)
    await delete_all_user_data(_db(fs), bucket, EMAIL_A)
    after_first = {c: dict(docs) for c, docs in fs.cols.items()}

    assert await delete_all_user_data(_db(fs), bucket, EMAIL_A) == {}
    assert fs.cols == after_first


async def test_email_is_case_insensitive(fs: FakeFirestore, bucket: LocalBucket) -> None:
    _seed_user(fs, USER_A, EMAIL_A)
    counts = await delete_all_user_data(_db(fs), bucket, "  ALICE@example.COM ")
    assert counts["decide_users"] == 1 and counts["magic_links"] == 1


def test_no_store_uses_the_unsupported_prefix_match() -> None:
    assert not [s for s in USER_DATA_STORES if s.match == "doc_id_prefix"]


async def test_prefix_match_raises_rather_than_skipping(fs: FakeFirestore) -> None:
    store = UserDataStore("anything", "doc_id_prefix")
    with pytest.raises(NotImplementedError):
        await user_deletion._page_ids(_db(fs), store, USER_A, EMAIL_A)


def test_decide_users_is_last() -> None:
    assert USER_DATA_STORES[-1].collection == "decide_users"
    assert USER_DATA_STORES[-1].match == "doc_id_email_hash"


def test_voice_labels_name_matches_admin_voice() -> None:
    from backend.api.routes.admin_voice import LABELS_COLLECTION

    assert user_deletion.VOICE_LABELS_COLLECTION == LABELS_COLLECTION


async def test_more_rows_than_a_page_are_all_deleted(
    fs: FakeFirestore, bucket: LocalBucket, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No query cap: a big import (user_songs past the page size) and big voice stores go in full."""
    monkeypatch.setattr(user_deletion, "PAGE_SIZE", 3)
    _seed_user(fs, USER_A, EMAIL_A)
    _seed_user(fs, USER_B, EMAIL_B)
    for i in range(10):
        fs.cols["user_songs"][f"{USER_A}:s{i}"] = {"user_id": USER_A, "source": "spotify_export"}
        fs.cols["user_songs"][f"{USER_A}:v{i}"] = {"user_id": USER_A, "sources": ["voice_session"]}
        fs.cols["voice_sessions"][f"s{i}"] = {"user_id": USER_A}
        fs.cols["voice_attempts"][f"s{i}:1"] = {"user_id": USER_A}
        fs.cols["magic_links"][f"t{i}"] = {"email": EMAIL_A.lower()}

    counts = await delete_all_user_data(_db(fs), bucket, EMAIL_A)

    assert counts["user_songs"] == 21 and counts["voice_sessions"] == 11 and counts["voice_attempts"] == 11
    assert counts["magic_links"] == 11
    assert not [d for col in fs.cols.values() for d in col.values() if d.get("user_id") == USER_A]
    assert not [d for d in fs.cols["magic_links"].values() if d.get("email") == EMAIL_A.lower()]
    assert f"{USER_B}:x" in fs.cols["user_songs"]


async def test_erase_voice_data_strips_more_rows_than_a_page(
    fs: FakeFirestore, bucket: LocalBucket, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(user_deletion, "PAGE_SIZE", 2)
    fs.cols["user_songs"] = {
        **{
            f"{USER_A}:m{i}": {"user_id": USER_A, "sources": ["quiz", "voice_session"], "session_feel": "ok"}
            for i in range(5)
        },
        **{f"{USER_A}:o{i}": {"user_id": USER_A, "source": "voice_session"} for i in range(5)},  # no sources list
        **{f"{USER_A}:p{i}": {"user_id": USER_A, "source": "quiz", "sources": ["quiz"]} for i in range(5)},
    }

    await erase_voice_data(_db(fs), bucket, USER_A)

    songs = fs.cols["user_songs"]
    assert sorted(songs) == sorted([f"{USER_A}:m{i}" for i in range(5)] + [f"{USER_A}:p{i}" for i in range(5)])
    assert all(songs[f"{USER_A}:m{i}"] == {"user_id": USER_A, "sources": ["quiz"]} for i in range(5))


async def test_deletes_that_dont_land_raise_instead_of_looping(
    fs: FakeFirestore, bucket: LocalBucket, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_user(fs, USER_A, EMAIL_A)

    async def no_op(col: str, ids: list[str]) -> None:
        return None

    monkeypatch.setattr(fs, "batch_delete", no_op)
    with pytest.raises(RuntimeError, match="keep coming back"):
        await delete_all_user_data(_db(fs), bucket, EMAIL_A)
