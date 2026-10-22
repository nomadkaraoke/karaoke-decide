"""Karaoke log service: merge rules, ownership, user_songs counters (in-memory Firestore, no GCP)."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import time
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from backend.services.catalog_lookup import CatalogLookup
from backend.services.karaoke_log_service import (
    LOG_COLLECTION,
    UNRESOLVED_COLLECTION,
    KaraokeLogInvalidError,
    KaraokeLogNotFoundError,
    KaraokeLogService,
    NotOwnerError,
    log_doc_id,
    unresolved_doc_id,
)
from backend.services.song_feedback_service import SongFeedbackService, loved_doc_id
from karaoke_decide.catalog.reader import KnMatch
from karaoke_decide.song_search.text_normalize import song_key as make_song_key

USER = "user_a"
EMAIL = "Singer@Example.com"
KJ_ID = "kj_" + "a" * 24
APP_ID = "app_0b5c3a5e-1111-4222-8333-944445555666"

# Invented songs only (never real lyrics; titles are made up too).
HELLO_KEY = "the lanterns|paper harbour"
KN_ID = 4242


# ---------------------------------------------------------------- fakes


class FakeTxn:
    """Buffers writes; Firestore forbids reads after writes inside a transaction."""

    def __init__(self, fs: MemoryFirestore) -> None:
        self._fs = fs
        self._writes: list[tuple[str, str, dict[str, Any] | None]] = []

    async def get(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        assert not self._writes, "read after write inside a transaction"
        doc = self._fs.cols.get(collection, {}).get(doc_id)
        return copy.deepcopy(doc) if doc is not None else None

    def set(self, collection: str, doc_id: str, data: dict[str, Any]) -> None:
        assert "/" not in doc_id
        self._writes.append((collection, doc_id, copy.deepcopy(data)))

    def delete(self, collection: str, doc_id: str) -> None:
        self._writes.append((collection, doc_id, None))

    def commit(self) -> None:
        for collection, doc_id, data in self._writes:
            col = self._fs.cols.setdefault(collection, {})
            if data is None:
                col.pop(doc_id, None)
            else:
                col[doc_id] = data


class MemoryFirestore:
    def __init__(self) -> None:
        self.cols: dict[str, dict[str, dict[str, Any]]] = {}
        self.transactions = 0

    async def get_document(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        doc = self.cols.get(collection, {}).get(doc_id)
        return {"id": doc_id, **copy.deepcopy(doc)} if doc is not None else None

    async def set_document(self, collection: str, doc_id: str, data: dict[str, Any], merge: bool = False) -> None:
        col = self.cols.setdefault(collection, {})
        if merge and doc_id in col:
            col[doc_id] = {**col[doc_id], **copy.deepcopy(data)}
        else:
            col[doc_id] = copy.deepcopy(data)

    async def update_document(self, collection: str, doc_id: str, data: dict[str, Any]) -> None:
        self.cols[collection][doc_id].update(copy.deepcopy(data))

    async def delete_document(self, collection: str, doc_id: str) -> None:
        self.cols.get(collection, {}).pop(doc_id, None)

    async def query_documents(self, collection: str, filters: Any = None, **_: Any) -> list[dict[str, Any]]:
        out = []
        for doc_id, d in self.cols.get(collection, {}).items():
            ok = True
            for f, op, v in filters or []:
                if op == "==":
                    ok = ok and d.get(f) == v
                elif op == "in":
                    ok = ok and d.get(f) in v
                else:  # pragma: no cover
                    raise AssertionError(op)
            if ok:
                out.append({"id": doc_id, **copy.deepcopy(d)})
        return out

    async def run_transaction(self, fn: Callable[[FakeTxn], Awaitable[Any]]) -> Any:
        self.transactions += 1
        txn = FakeTxn(self)
        result = await fn(txn)
        txn.commit()
        return result

    def log(self, entry_id: str, user_id: str = USER) -> dict[str, Any]:
        return self.cols[LOG_COLLECTION][log_doc_id(user_id, entry_id)]

    def user_songs(self) -> dict[str, dict[str, Any]]:
        return self.cols.get("user_songs", {})


class FakeReader:
    """The CatalogReader calls resolution uses."""

    def __init__(self, delay_s: float = 0.0) -> None:
        self.kn: dict[str, tuple[int, str, str, str]] = {}  # match "artist|title" lower -> (kn_id, artist, title, key)
        self.karaoke_keys: set[str] = set()
        self.delay_s = delay_s

    def add_kn(self, kn_id: int, artist: str, title: str, key: str) -> None:
        self.kn[f"{artist.lower()}|{title.lower()}"] = (kn_id, artist, title, key)

    def match_kn(self, artist: str, title: str) -> KnMatch | None:
        if self.delay_s:
            time.sleep(self.delay_s)
        hit = self.kn.get(f"{artist.lower()}|{title.lower()}")
        if hit is None:
            return None
        return KnMatch(kn_id=hit[0], artist=hit[1], title=hit[2], brands="", brand_count=1)

    def kn_song_keys(self, kn_ids: list[int]) -> dict[int, str]:
        return {v[0]: v[3] for v in self.kn.values() if v[0] in kn_ids}

    def kn_ids_for_song_keys(self, keys: list[str]) -> dict[str, int]:
        return {v[3]: v[0] for v in self.kn.values() if v[3] in keys}

    def has_karaoke_key(self, key: str) -> bool:
        return key in self.karaoke_keys or key in {v[3] for v in self.kn.values()}


class FakeStore:
    def __init__(self, reader: FakeReader | None) -> None:
        self.reader = reader


async def _resolve_user(email: str) -> str:
    return USER if email.strip().lower() == EMAIL.lower() else "user_" + email.split("@")[0]


@pytest.fixture
def fs() -> MemoryFirestore:
    return MemoryFirestore()


@pytest.fixture
def reader() -> FakeReader:
    r = FakeReader()
    r.add_kn(KN_ID, "The Lanterns", "Paper Harbour", HELLO_KEY)
    return r


@pytest.fixture
def svc(fs: MemoryFirestore, reader: FakeReader) -> KaraokeLogService:
    return KaraokeLogService(fs, catalog=CatalogLookup(FakeStore(reader)), resolve_user=_resolve_user)  # type: ignore[arg-type]


def _kj_facts(**over: Any) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "state": "sung",
        "artist": "The Lanterns",
        "title": "Paper Harbour",
        "sung_at": "2026-10-21T21:30:00+00:00",
        "ended_at": "2026-10-21T21:34:00+00:00",
        "night_id": "abcd1234abcd1234",
        "night_date": "2026-10-21",
        "venue": {"kind": "nomad", "name": "Andrew"},
        "box_id": "nomadpc",
        "entry_id": "e1",
        "singers": ["Sam"],
        "media": {"brand_code": "NOMAD-0001", "source_type": "gen"},
    }
    facts.update(over)
    return facts


def _app_entry(**over: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "state": "sung",
        "artist": "The Lanterns",
        "title": "Paper Harbour",
        "song_key": HELLO_KEY,
        "sung_at": "2026-10-20T22:00:00Z",
        "night": {"id": "n-uuid", "kind": "other", "venue_name": "The Crown", "date": "2026-10-20"},
    }
    entry.update(over)
    return entry


# ---------------------------------------------------------------- counters / user_songs row choice


@pytest.mark.asyncio
async def test_counter_updates_existing_kn_id_row(svc: KaraokeLogService, fs: MemoryFirestore) -> None:
    kn_row_id = f"{USER}:{KN_ID}"
    fs.cols["user_songs"] = {
        kn_row_id: {
            "user_id": USER,
            "song_id": str(KN_ID),
            "source": "quiz",
            "artist": "The Lanterns",
            "title": "Paper Harbour",
            "times_sung": 0,
            "last_sung_at": None,
        }
    }
    await svc.upsert_partner(EMAIL, KJ_ID, _kj_facts())
    assert list(fs.user_songs()) == [kn_row_id]  # no new row
    row = fs.user_songs()[kn_row_id]
    assert row["times_sung"] == 1
    assert row["source"] == "quiz"  # existing rows keep their source
    assert row["song_key"] == HELLO_KEY
    assert row["last_sung_at"] == "2026-10-21T21:30:00+00:00"


@pytest.mark.asyncio
async def test_counter_uses_loved_doc_id_when_no_kn_row(svc: KaraokeLogService, fs: MemoryFirestore) -> None:
    entry = await svc.upsert_partner(EMAIL, KJ_ID, _kj_facts())
    assert entry.song_key == HELLO_KEY and entry.song_key_status == "resolved"
    row_id = loved_doc_id(USER, HELLO_KEY)
    assert list(fs.user_songs()) == [row_id]
    row = fs.user_songs()[row_id]
    assert row["source"] == "karaoke_log"
    assert row["times_sung"] == 1
    assert (row["song_key"], row["artist"], row["title"]) == (HELLO_KEY, "The Lanterns", "Paper Harbour")
    assert row["user_id"] == USER
    assert isinstance(row["last_sung_at"], str) and isinstance(row["created_at"], str)


@pytest.mark.asyncio
async def test_sung_unplayed_counts_with_ended_at(svc: KaraokeLogService, fs: MemoryFirestore) -> None:
    await svc.upsert_partner(EMAIL, KJ_ID, _kj_facts(state="sung_unplayed", sung_at=None))
    row = fs.user_songs()[loved_doc_id(USER, HELLO_KEY)]
    assert row["times_sung"] == 1
    assert row["last_sung_at"] == "2026-10-21T21:34:00+00:00"


@pytest.mark.asyncio
async def test_sung_increments_and_delete_decrements(svc: KaraokeLogService, fs: MemoryFirestore) -> None:
    kn_row_id = f"{USER}:{KN_ID}"
    fs.cols["user_songs"] = {
        kn_row_id: {
            "user_id": USER,
            "song_id": str(KN_ID),
            "source": "known_songs",
            "times_sung": 2,
            "last_sung_at": "2026-01-01T00:00:00+00:00",
        }
    }
    await svc.upsert_user(USER, APP_ID, _app_entry())
    assert fs.user_songs()[kn_row_id]["times_sung"] == 3
    await svc.delete(USER, APP_ID)
    assert fs.user_songs()[kn_row_id]["times_sung"] == 2
    assert fs.log(APP_ID)["deleted"] is True
    # A row the log created disappears again when its only count is removed.
    other = "app_" + "b" * 32
    await svc.upsert_user(USER, other, _app_entry(song_key="x|y", artist="X", title="Y"))
    assert fs.user_songs()[loved_doc_id(USER, "x|y")]["times_sung"] == 1
    await svc.delete(USER, other)
    assert loved_doc_id(USER, "x|y") not in fs.user_songs()


@pytest.mark.asyncio
async def test_transition_out_of_sung_decrements(svc: KaraokeLogService, fs: MemoryFirestore) -> None:
    await svc.upsert_user(USER, APP_ID, _app_entry(state="requested", sung_at=None))
    assert fs.user_songs() == {}
    await svc.upsert_user(USER, APP_ID, {"state": "sung", "sung_at": "2026-10-20T22:00:00Z"})
    assert fs.user_songs()[loved_doc_id(USER, HELLO_KEY)]["times_sung"] == 1
    await svc.upsert_user(USER, APP_ID, {"state": "skipped"})
    assert loved_doc_id(USER, HELLO_KEY) not in fs.user_songs()


@pytest.mark.asyncio
async def test_redo_does_not_double_count(svc: KaraokeLogService, fs: MemoryFirestore) -> None:
    row_id = loved_doc_id(USER, HELLO_KEY)
    await svc.upsert_partner(EMAIL, KJ_ID, _kj_facts())
    await svc.upsert_partner(EMAIL, KJ_ID, _kj_facts())  # retried delivery
    assert fs.user_songs()[row_id]["times_sung"] == 1
    await svc.delete_partner(EMAIL, KJ_ID)  # host un-does Done
    assert row_id not in fs.user_songs()
    await svc.delete_partner(EMAIL, KJ_ID)  # retried delete
    await svc.upsert_partner(EMAIL, KJ_ID, _kj_facts())  # Done again
    assert fs.user_songs()[row_id]["times_sung"] == 1
    entry = fs.log(KJ_ID)
    assert entry["deleted"] is False and entry["state"] == "sung"
    assert len(fs.cols[LOG_COLLECTION]) == 1


@pytest.mark.asyncio
async def test_user_delete_is_not_undone_by_partner_retry(svc: KaraokeLogService, fs: MemoryFirestore) -> None:
    await svc.upsert_partner(EMAIL, KJ_ID, _kj_facts())
    await svc.delete(USER, KJ_ID)
    await svc.upsert_partner(EMAIL, KJ_ID, _kj_facts())
    assert fs.log(KJ_ID)["deleted"] is True
    assert loved_doc_id(USER, HELLO_KEY) not in fs.user_songs()


@pytest.mark.asyncio
async def test_unresolved_counted_separately(svc: KaraokeLogService, fs: MemoryFirestore) -> None:
    facts = _kj_facts(artist="Nobody Known", title="Song From The SSD")
    entry = await svc.upsert_partner(EMAIL, KJ_ID, facts)
    assert entry.song_key is None and entry.song_key_status == "unresolved"
    assert fs.user_songs() == {}
    doc_id = unresolved_doc_id(USER, "Nobody Known", "Song From The SSD")
    expected = hashlib.sha1(make_song_key("Nobody Known", "Song From The SSD").encode()).hexdigest()[:24]  # noqa: S324
    assert doc_id == f"{USER}:{expected}"
    counter = fs.cols[UNRESOLVED_COLLECTION][doc_id]
    assert counter["times_sung"] == 1
    assert (counter["artist"], counter["title"], counter["user_id"]) == ("Nobody Known", "Song From The SSD", USER)
    # Spelling variants of the same song share one counter.
    assert unresolved_doc_id(USER, "nobody known", "song from the ssd") == doc_id
    await svc.delete_partner(EMAIL, KJ_ID)
    assert doc_id not in fs.cols[UNRESOLVED_COLLECTION]


@pytest.mark.asyncio
async def test_retry_unresolved_moves_count_to_user_songs(
    svc: KaraokeLogService, fs: MemoryFirestore, reader: FakeReader
) -> None:
    await svc.upsert_partner(EMAIL, KJ_ID, _kj_facts(artist="New Band", title="Fresh Song"))
    assert fs.cols[UNRESOLVED_COLLECTION]
    reader.add_kn(77, "New Band", "Fresh Song", "new band|fresh song")  # catalog refresh
    assert await svc.retry_unresolved(USER) == 1
    assert fs.cols[UNRESOLVED_COLLECTION] == {}
    assert fs.user_songs()[loved_doc_id(USER, "new band|fresh song")]["times_sung"] == 1
    assert fs.log(KJ_ID)["song_key_status"] == "resolved"
    assert await svc.retry_unresolved(USER) == 0


@pytest.mark.asyncio
async def test_resolution_over_budget_stays_unresolved(fs: MemoryFirestore) -> None:
    slow = FakeReader(delay_s=0.5)
    slow.add_kn(KN_ID, "The Lanterns", "Paper Harbour", HELLO_KEY)
    svc = KaraokeLogService(fs, catalog=CatalogLookup(FakeStore(slow)), resolve_user=_resolve_user)  # type: ignore[arg-type]
    started = time.monotonic()
    entry = await svc.upsert_partner(EMAIL, KJ_ID, _kj_facts())
    assert time.monotonic() - started < 0.45
    assert entry.song_key_status == "unresolved"
    await asyncio.sleep(0.2)  # let the abandoned lookup thread finish


@pytest.mark.asyncio
async def test_resolve_catalog_warming_up(fs: MemoryFirestore) -> None:
    svc = KaraokeLogService(fs, catalog=CatalogLookup(FakeStore(None)), resolve_user=_resolve_user)  # type: ignore[arg-type]
    assert await svc.resolve("The Lanterns", "Paper Harbour") == (None, "unresolved")


@pytest.mark.asyncio
async def test_resolve_direct_key_wins(svc: KaraokeLogService, reader: FakeReader) -> None:
    assert await svc.resolve("The Lanterns", "Paper Harbour") == (HELLO_KEY, "resolved")
    reader.karaoke_keys.add(make_song_key("Solo Act", "Only Here"))
    assert await svc.resolve("Solo Act", "Only Here") == (make_song_key("Solo Act", "Only Here"), "resolved")
    # The KaraokeNerds match and the exact key name two different catalog songs: the exact key wins.
    reader.add_kn(9, "Twin", "Mirror", "twin|mirror (other)")
    assert await svc.resolve("Twin", "Mirror") == ("twin|mirror (other)", "resolved")
    reader.karaoke_keys.add(make_song_key("Twin", "Mirror"))
    assert await svc.resolve("Twin", "Mirror") == (make_song_key("Twin", "Mirror"), "resolved")
    assert await svc.resolve("", "Mirror") == (None, "unresolved")


# ---------------------------------------------------------------- merge rules / ownership


@pytest.mark.asyncio
async def test_app_entry_upsert_idempotent(svc: KaraokeLogService, fs: MemoryFirestore) -> None:
    first = await svc.upsert_user(USER, APP_ID, _app_entry())
    second = await svc.upsert_user(USER, APP_ID, _app_entry())
    assert len(fs.cols[LOG_COLLECTION]) == 1
    assert first.source == second.source == "app"
    assert second.song_key == HELLO_KEY and second.song_key_status == "resolved"
    assert second.night is not None and second.night.venue_name == "The Crown"
    assert fs.user_songs()[loved_doc_id(USER, HELLO_KEY)]["times_sung"] == 1
    assert first.created_at == second.created_at
    assert isinstance(fs.log(APP_ID)["created_at"], str)


@pytest.mark.asyncio
async def test_app_entry_without_song_key_is_resolved_server_side(svc: KaraokeLogService) -> None:
    entry = await svc.upsert_user(USER, APP_ID, _app_entry(song_key=None))
    assert (entry.song_key, entry.song_key_status) == (HELLO_KEY, "resolved")
    typed = await svc.upsert_user(USER, "app_typed", _app_entry(song_key=None, artist="Pub Band", title="Our Tune"))
    assert (typed.song_key, typed.song_key_status) == (None, "unresolved")


@pytest.mark.asyncio
async def test_partner_and_user_fields_merge_either_order(svc: KaraokeLogService, fs: MemoryFirestore) -> None:
    survey = {"feel": "great", "key": "too_high", "crowd_pleaser": True, "notes": "Try a semitone down"}
    # survey first, then kjbox facts
    await svc.upsert_user(USER, KJ_ID, {"survey": survey})
    assert await svc.list_for_user(USER) == ([], None)  # a survey-only placeholder isn't listed
    a = await svc.upsert_partner(EMAIL, KJ_ID, _kj_facts())
    # kjbox facts first, then survey
    other = "kj_" + "b" * 24
    await svc.upsert_partner(EMAIL, other, _kj_facts(entry_id="e2"))
    b = await svc.upsert_user(USER, other, {"survey": survey})
    for e in (a, b):
        assert e.state == "sung" and e.source == "kjbox" and e.user_id == USER
        assert e.survey is not None and e.survey.feel == "great" and e.survey.key == "too_high"
        assert e.survey.crowd_pleaser is True and e.survey.notes == "Try a semitone down"
        assert e.survey.answered_at is not None
        assert e.night is not None and e.night.id == "abcd1234abcd1234" and e.night.kind == "nomad"
        assert e.night.venue_name == "Andrew"
        assert e.media is not None and e.media.brand_code == "NOMAD-0001"
        assert e.email_hash == hashlib.sha256(EMAIL.lower().encode()).hexdigest()
    # survey answers merge field by field
    c = await svc.upsert_user(USER, other, {"survey": {"notes": None}})
    assert c.survey is not None and c.survey.notes is None and c.survey.feel == "great"
    assert fs.user_songs()[loved_doc_id(USER, HELLO_KEY)]["times_sung"] == 2


@pytest.mark.asyncio
async def test_user_cannot_overwrite_kjbox_facts(svc: KaraokeLogService, fs: MemoryFirestore) -> None:
    await svc.upsert_partner(EMAIL, KJ_ID, _kj_facts())
    for patch in (
        {"state": "skipped"},
        {"sung_at": "2026-10-22T00:00:00Z"},
        {"artist": "Other"},
        {"media": {"brand_code": "X"}},
        {"survey": {"feel": "ok"}, "title": "Other"},
    ):
        with pytest.raises(NotOwnerError):
            await svc.upsert_user(USER, KJ_ID, patch)
    doc = fs.log(KJ_ID)
    assert (doc["state"], doc["artist"], doc["title"]) == ("sung", "The Lanterns", "Paper Harbour")
    assert doc.get("survey") is None
    # another user can't touch this record at all
    with pytest.raises(KaraokeLogNotFoundError):
        await svc.delete("user_b", KJ_ID)
    assert fs.log(KJ_ID)["deleted"] is False
    # the owner may set survey and deleted
    entry = await svc.upsert_user(USER, KJ_ID, {"survey": {"feel": "ok"}, "deleted": True})
    assert entry.deleted is True and entry.survey is not None and entry.survey.feel == "ok"


@pytest.mark.asyncio
async def test_notes_length_limit(svc: KaraokeLogService, fs: MemoryFirestore) -> None:
    await svc.upsert_user(USER, APP_ID, _app_entry(survey={"notes": "n" * 2000}))
    with pytest.raises(KaraokeLogInvalidError):
        await svc.upsert_user(USER, APP_ID, {"survey": {"notes": "n" * 2001}})
    assert len(fs.log(APP_ID)["survey"]["notes"]) == 2000


@pytest.mark.asyncio
async def test_invalid_input(svc: KaraokeLogService) -> None:
    with pytest.raises(KaraokeLogInvalidError):
        await svc.upsert_user(USER, "nope_1", _app_entry())  # unknown id prefix
    with pytest.raises(KaraokeLogInvalidError):
        await svc.upsert_user(USER, "app_a/b", _app_entry())
    with pytest.raises(KaraokeLogInvalidError):
        await svc.upsert_user(USER, APP_ID, _app_entry(state="dancing"))
    with pytest.raises(KaraokeLogInvalidError):
        await svc.upsert_user(USER, APP_ID, _app_entry(sung_at="yesterday"))
    with pytest.raises(KaraokeLogInvalidError):
        await svc.upsert_user(USER, APP_ID, _app_entry(colour="red"))
    with pytest.raises(KaraokeLogInvalidError):
        await svc.upsert_user(USER, APP_ID, {"survey": {"feel": "great"}})  # new app entry needs facts
    with pytest.raises(KaraokeLogInvalidError):
        await svc.upsert_partner(EMAIL, APP_ID, _kj_facts())  # kjbox only writes kj_ ids
    with pytest.raises(KaraokeLogNotFoundError):
        await svc.delete(USER, APP_ID)


@pytest.mark.asyncio
async def test_partner_resolves_profile_by_email(svc: KaraokeLogService, fs: MemoryFirestore) -> None:
    entry = await svc.upsert_partner("  newbie@example.com ", "kj_" + "c" * 24, _kj_facts())
    assert entry.user_id == "user_newbie"
    assert entry.email_hash == hashlib.sha256(b"newbie@example.com").hexdigest()


# ---------------------------------------------------------------- listing


@pytest.mark.asyncio
async def test_list_for_user_filters_and_pages(svc: KaraokeLogService, fs: MemoryFirestore) -> None:
    await svc.upsert_user(USER, "kj_" + "p" * 24, {"survey": {"feel": "ok"}})  # placeholder: hidden
    for i in range(5):
        night = {"id": "n1" if i < 3 else "n2", "kind": "other"}
        await svc.upsert_user(USER, f"app_{i}", _app_entry(night=night, sung_at=f"2026-10-2{i}T20:00:00Z"))
        await asyncio.sleep(0.002)  # distinct updated_at
    await svc.upsert_user("user_b", "app_other", _app_entry())
    await svc.delete(USER, "app_4")

    entries, cursor = await svc.list_for_user(USER)
    assert [e.id for e in entries] == ["app_3", "app_2", "app_1", "app_0"]  # newest first, no deleted
    assert cursor is None

    page1, cursor = await svc.list_for_user(USER, limit=2)
    assert [e.id for e in page1] == ["app_3", "app_2"] and cursor
    page2, cursor2 = await svc.list_for_user(USER, limit=2, cursor=cursor)
    assert [e.id for e in page2] == ["app_1", "app_0"] and cursor2 is None

    n1, _ = await svc.list_for_user(USER, night_id="n1")
    assert {e.id for e in n1} == {"app_0", "app_1", "app_2"}

    since = fs.log("app_2")["updated_at"]
    changed, _ = await svc.list_for_user(USER, since=since)
    # a sync pull includes deletions, so the app learns about them
    assert [e.id for e in changed] == ["app_4", "app_3"]
    assert changed[0].deleted is True

    with pytest.raises(KaraokeLogInvalidError):
        await svc.list_for_user(USER, cursor="garbage")


@pytest.mark.asyncio
async def test_user_undelete_does_not_revive_host_undo(svc: KaraokeLogService, fs: MemoryFirestore) -> None:
    row_id = loved_doc_id(USER, HELLO_KEY)
    await svc.upsert_partner(EMAIL, KJ_ID, _kj_facts())
    await svc.delete_partner(EMAIL, KJ_ID)  # host un-does Done
    assert row_id not in fs.user_songs()
    # a stale outbox replay / full-record survey save carrying deleted: false
    entry = await svc.upsert_user(USER, KJ_ID, {"survey": {"feel": "great"}, "deleted": False})
    assert entry.deleted is True and fs.log(KJ_ID)["deleted_by"] == "partner"
    assert row_id not in fs.user_songs()
    assert fs.cols.get(UNRESOLVED_COLLECTION, {}) == {}
    # the user's own delete can still be undone by the user
    await svc.upsert_user(USER, APP_ID, _app_entry())
    await svc.delete(USER, APP_ID)
    assert row_id not in fs.user_songs()
    assert (await svc.upsert_user(USER, APP_ID, {"deleted": False})).deleted is False
    assert fs.user_songs()[row_id]["times_sung"] == 1


@pytest.mark.asyncio
async def test_deleted_must_be_bool(svc: KaraokeLogService, fs: MemoryFirestore) -> None:
    await svc.upsert_user(USER, APP_ID, _app_entry())
    for bad in ("false", 0, 1, None):
        with pytest.raises(KaraokeLogInvalidError):
            await svc.upsert_user(USER, APP_ID, {"deleted": bad})
    assert fs.log(APP_ID)["deleted"] is False


@pytest.mark.asyncio
async def test_other_user_cannot_squat_a_kj_id(svc: KaraokeLogService, fs: MemoryFirestore) -> None:
    # user B writes first to A's (guessable) kj_ id
    squat = await svc.upsert_user("user_b", KJ_ID, {"survey": {"feel": "bad", "notes": "mine now"}, "deleted": True})
    assert squat.user_id == "user_b"
    # then kjbox delivers A's song
    a = await svc.upsert_partner(EMAIL, KJ_ID, _kj_facts())
    assert a.id == KJ_ID and a.user_id == USER
    assert a.state == "sung" and a.deleted is False and a.survey is None
    assert fs.user_songs()[loved_doc_id(USER, HELLO_KEY)]["times_sung"] == 1
    # B's writes and deletes only ever reach B's own (never-listed) placeholder
    await svc.upsert_user("user_b", KJ_ID, {"survey": {"feel": "ok"}, "deleted": False})
    await svc.delete("user_b", KJ_ID)
    doc = fs.log(KJ_ID)
    assert doc["user_id"] == USER and doc["deleted"] is False and doc.get("survey") is None
    assert fs.user_songs()[loved_doc_id(USER, HELLO_KEY)]["times_sung"] == 1
    assert (await svc.list_for_user("user_b")) == ([], None)
    assert [e.id for e in (await svc.list_for_user(USER))[0]] == [KJ_ID]
    # A's own survey lands on A's record
    mine = await svc.upsert_user(USER, KJ_ID, {"survey": {"feel": "great"}})
    assert mine.survey is not None and mine.survey.feel == "great"


# ---------------------------------------------------------------- love / unlove share the row (C-R1)


def _feedback(fs: MemoryFirestore, reader: FakeReader) -> SongFeedbackService:
    return SongFeedbackService(fs, kn_id_for=lambda k: reader.kn_ids_for_song_keys([k]).get(k))  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_sing_love_unlove_keeps_times_sung(
    svc: KaraokeLogService, fs: MemoryFirestore, reader: FakeReader
) -> None:
    await svc.upsert_partner(EMAIL, KJ_ID, _kj_facts())
    row_id = loved_doc_id(USER, HELLO_KEY)
    assert fs.user_songs()[row_id]["times_sung"] == 1
    feedback = _feedback(fs, reader)

    await feedback.love(USER, HELLO_KEY, "The Lanterns", "Paper Harbour")
    assert list(fs.user_songs()) == [row_id]
    row = fs.user_songs()[row_id]
    assert (row["times_sung"], row["source"], row["loved"]) == (1, "karaoke_log", True)
    assert row["last_sung_at"] == "2026-10-21T21:30:00+00:00"
    assert [s.song_key for s in await feedback.list_loved(USER)] == [HELLO_KEY]

    await feedback.unlove(USER, HELLO_KEY)
    row = fs.user_songs()[row_id]
    assert (row["times_sung"], row["loved"]) == (1, False)
    assert await feedback.list_loved(USER) == []


@pytest.mark.asyncio
async def test_love_survives_host_undo_then_unlove_removes_empty_row(
    svc: KaraokeLogService, fs: MemoryFirestore, reader: FakeReader
) -> None:
    feedback = _feedback(fs, reader)
    await svc.upsert_partner(EMAIL, KJ_ID, _kj_facts())
    await feedback.love(USER, HELLO_KEY, "The Lanterns", "Paper Harbour")
    await svc.delete_partner(EMAIL, KJ_ID)  # host un-does Done: count goes to 0
    row_id = loved_doc_id(USER, HELLO_KEY)
    row = fs.user_songs()[row_id]  # still loved, so the log must not delete it
    assert (row["times_sung"], row["loved"]) == (0, True)
    await svc.upsert_partner(EMAIL, KJ_ID, _kj_facts())  # Done again
    assert fs.user_songs()[row_id]["times_sung"] == 1
    await svc.delete_partner(EMAIL, KJ_ID)
    await feedback.unlove(USER, HELLO_KEY)  # nothing else on the row now
    assert row_id not in fs.user_songs()


@pytest.mark.asyncio
async def test_love_then_sing_uses_the_same_kn_row(
    svc: KaraokeLogService, fs: MemoryFirestore, reader: FakeReader
) -> None:
    kn_row_id = f"{USER}:{KN_ID}"
    fs.cols["user_songs"] = {kn_row_id: {"user_id": USER, "song_id": str(KN_ID), "source": "spotify"}}
    feedback = _feedback(fs, reader)
    await feedback.love(USER, HELLO_KEY, "The Lanterns", "Paper Harbour")
    await svc.upsert_partner(EMAIL, KJ_ID, _kj_facts())
    assert list(fs.user_songs()) == [kn_row_id]
    row = fs.user_songs()[kn_row_id]
    assert (row["times_sung"], row["loved"], row["source"]) == (1, True, "spotify")
