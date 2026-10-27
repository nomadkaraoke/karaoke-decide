"""Personal voice pool ("analyse my music"): /api/voice/personal-pool and /api/admin/voice/costs.

Fixture catalog.db + availability.db, a fixture voice.db without the user's songs, in-memory Firestore.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sqlite3
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import (
    get_catalog_store_dep,
    get_current_user,
    get_firestore,
    get_hidden_keys_provider,
    get_optional_user,
    get_settings,
    get_voice_store,
)
from backend.api.routes.test_support import PERSONAL_SEED_SONGS, personal_seed_candidates, personal_seed_rows
from backend.config import BackendSettings
from backend.main import app
from backend.services import voice_pool_service as vps
from backend.services.recs_v2.context import UserContextV2
from backend.services.voice_data_store import VoiceDataStore
from backend.tests.test_fakes import MemoryFirestore as RouteFirestore
from backend.tests.test_voice_session_routes import FakeCatalogStore, FakeHidden, _store, catalog_reader  # noqa: F401
from karaoke_decide.catalog.reader import CatalogReader
from karaoke_decide.core.models import User
from karaoke_decide.song_search.text_normalize import song_key
from karaoke_decide.voice_pool import fixtures, voicedb
from karaoke_decide.voice_pool.queue import FETCH_COLLECTION, pool_id

USER_ID = "user_personal"
OTHER_ID = "user_personal_other"
EMAIL = "Listener@Example.com"
URL = "/api/voice/personal-pool"
BATCHES, BUDGET, LAST = vps.BATCHES_COLLECTION, vps.BUDGET_COLLECTION, vps.LAST_BATCH_COLLECTION

# (artist, title) as the user's history spells them: catalog songs with a community version.
SONGS = [
    ("ABBA", "Dancing Queen"),
    ("ABBA", "Mamma Mia"),
    ("ABBA", "Waterloo"),
    ("ABBA", "The Winner Takes It All"),
    ("Adele", "Hello"),
    ("Adele", "Skyfall"),
    ("Adele", "Someone Like You"),
    ("Aerosmith", "Dream On"),
]
KEYS = [song_key(a, t) for a, t in SONGS]
IN_VOICE_DB = "a ha|take on me"  # a community song the voice.db fixture keeps


@pytest.fixture(scope="module")
def voice_store(tmp_path_factory: pytest.TempPathFactory) -> Iterator[VoiceDataStore]:
    """The fixture voice.db without ``KEYS`` (the user's songs aren't pooled yet)."""
    src = fixtures.build(tmp_path_factory.mktemp("personal_src"))
    dest = tmp_path_factory.mktemp("personal_voice") / "voice.db"
    shutil.copy(src, dest)
    con = sqlite3.connect(dest)
    for table in ("pool", "analysis", "versions"):
        con.executemany(f"DELETE FROM {table} WHERE song_key = ?", [(k,) for k in KEYS])
    con.commit()
    con.close()
    store = _store(dest)
    yield store
    store.close()


def _user_songs(playcounts: list[int], user_id: str = USER_ID) -> dict[str, dict[str, Any]]:
    return {
        f"{user_id}:{i}": {"user_id": user_id, "artist": a, "title": t, "source": "lastfm", "playcount": p}
        for i, ((a, t), p) in enumerate(zip(SONGS, playcounts, strict=False))
    }


RICH = [200, 190, 180, 170, 160, 150, 140, 130]  # 1,320 listens: eligible by history


@pytest.fixture
def fs() -> RouteFirestore:
    fs = RouteFirestore()
    fs.cols["user_songs"] = {**_user_songs(RICH), **_user_songs(RICH, OTHER_ID)}
    return fs


MakeClient = Callable[..., TestClient]


@pytest.fixture
def make_client(
    fs: RouteFirestore,
    voice_store: VoiceDataStore,
    catalog_reader: CatalogReader,  # noqa: F811
) -> Iterator[MakeClient]:
    def make(
        *, admin: bool = False, open_: bool = True, user_id: str = USER_ID, guest: bool = False, budget: float = 20.0
    ) -> TestClient:
        settings = BackendSettings(
            environment="development",
            google_cloud_project="test-project",
            voice_session_open=open_,
            voice_monthly_budget_usd=budget,
        )
        user = User(id=user_id, email=None if guest else EMAIL, is_admin=admin, is_guest=guest)
        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[get_firestore] = lambda: fs
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_optional_user] = lambda: user
        app.dependency_overrides[get_voice_store] = lambda: voice_store
        app.dependency_overrides[get_catalog_store_dep] = lambda: FakeCatalogStore(catalog_reader)
        app.dependency_overrides[get_hidden_keys_provider] = FakeHidden
        return TestClient(app)

    yield make
    app.dependency_overrides.clear()


@pytest.fixture
def client(make_client: MakeClient) -> TestClient:
    return make_client()


def _month(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).strftime("%Y-%m")


def _batch(fs: RouteFirestore, batch_id: str, n: int, days_ago: float, user_id: str = USER_ID) -> datetime:
    at = datetime.now(UTC) - timedelta(days=days_ago)
    fs.col(BATCHES)[batch_id] = {
        "user_id": user_id,
        "song_keys": [f"old|{batch_id}|{i}" for i in range(n)],
        "requested_at": at,
        "est_cost_usd": 0.0,
        "month": _month(at),
    }
    fs.col(LAST)[user_id] = {"last_batch_id": batch_id, "updated_at": at}
    return at


def _gen_user(fs: RouteFirestore, doc_id: str, **doc: Any) -> None:
    fs.col("gen_users")[doc_id] = {"email": EMAIL.lower(), **doc}


def _set_plays(fs: RouteFirestore, playcounts: list[int]) -> None:
    for k in [k for k, d in fs.col("user_songs").items() if d["user_id"] == USER_ID]:
        del fs.col("user_songs")[k]
    fs.col("user_songs").update(_user_songs(playcounts))


# ---------------------------------------------------------------- gate


def test_routes_closed_for_non_admin(make_client: MakeClient) -> None:
    c = make_client(open_=False)
    for r in (c.get(URL), c.post(URL)):
        assert r.status_code == 403 and r.json() == {"detail": "voice_session_disabled"}
    assert make_client(open_=False, admin=True).get(URL).status_code == 200


# ---------------------------------------------------------------- eligibility


def test_eligible_paid_by_total_spent(client: TestClient, fs: RouteFirestore) -> None:
    _set_plays(fs, [1, 1])
    assert client.get(URL).json()["reason"] == "not_eligible"
    _gen_user(fs, EMAIL.lower(), total_spent=499)
    body = client.get(URL).json()
    assert (body["eligible"], body["reason"]) == (True, "paid")


def test_eligible_paid_by_purchase_txn(client: TestClient, fs: RouteFirestore) -> None:
    """A legacy hash-keyed gen doc is found by its ``email`` field; a purchase transaction counts."""
    _set_plays(fs, [1])
    _gen_user(fs, "legacyhash", total_spent=0, credit_transactions=[{"reason": "welcome_credit", "amount": 2}])
    assert client.get(URL).json()["eligible"] is False
    _gen_user(fs, "legacyhash", total_spent=0, credit_transactions=[{"reason": "purchase", "amount": 5}])
    assert client.get(URL).json()["reason"] == "paid"


@pytest.mark.parametrize("reason", ["stripe_purchase", "iap_purchase"])
def test_eligible_paid_by_gen_purchase_reasons(client: TestClient, fs: RouteFirestore, reason: str) -> None:
    """gen's real reasons: an in-app purchase adds no total_spent, so its transaction alone must count."""
    _set_plays(fs, [1])
    _gen_user(fs, "legacyhash", total_spent=0, credit_transactions=[{"reason": reason, "amount": 5}])
    assert client.get(URL).json()["reason"] == "paid"


def test_eligible_by_history_counts() -> None:
    user = User(id=USER_ID, email=EMAIL)

    def elig(rows: list[dict[str, Any]]) -> tuple[bool, str, int, int]:
        e = vps.eligibility_from(user, None, rows)
        return e.eligible, e.reason, e.listens, e.songs_with_plays

    assert elig([{"playcount": 999}]) == (False, "not_eligible", 999, 1)
    assert elig([{"playcount": 600}, {"playcount": "400"}]) == (True, "history", 1000, 2)
    assert elig([{"playcount": 1}] * 199 + [{"playcount": 0}, {}]) == (False, "not_eligible", 199, 199)
    assert elig([{"playcount": 1}] * 200) == (True, "history", 200, 200)
    # Admins first; guests never (throwaway accounts), whatever their history.
    assert vps.eligibility_from(User(id="a", is_admin=True), None, []).reason == "admin"
    assert vps.eligibility_from(User(id="g", is_guest=True), None, [{"playcount": 5000}]).eligible is False


def test_not_eligible(make_client: MakeClient, fs: RouteFirestore) -> None:
    _set_plays(fs, [3, 2])
    c = make_client()
    body = c.get(URL).json()
    assert body["eligible"] is False and body["reason"] == "not_eligible"
    assert (body["listens"], body["songs_with_plays"]) == (5, 2)
    r = c.post(URL)
    assert r.status_code == 403 and r.json() == {"detail": {"reason": "not_eligible"}}
    assert not fs.cols.get(BATCHES) and not fs.cols.get(FETCH_COLLECTION)
    r = make_client(guest=True, user_id="guest_x").post(URL)
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "not_eligible"


# ---------------------------------------------------------------- picks


def test_picks_skip_pooled_and_queued(catalog_reader: CatalogReader, voice_store: VoiceDataStore) -> None:  # noqa: F811
    voice = voice_store.require()
    no_karaoke = song_key("Invented Band", "Not A Real Song")
    ctx = UserContextV2(
        user_id=USER_ID,
        known_songs={**{k: 0.8 for k in KEYS}, KEYS[7]: 1.0, IN_VOICE_DB: 1.0, no_karaoke: 1.0},
        hidden={KEYS[1]},
    )
    plays = {KEYS[3]: 50, KEYS[4]: 60}
    picks = vps.pick_personal_songs(ctx, catalog_reader, voice, queued={KEYS[0]}, n=20, playcounts=plays)
    # weight first (KEYS[7]), then playcount (KEYS[4], KEYS[3]), then key; hidden, queued, pooled, no-karaoke out
    assert picks == [KEYS[7], KEYS[4], KEYS[3], *sorted([KEYS[2], KEYS[5], KEYS[6]])]
    assert vps.pick_personal_songs(ctx, catalog_reader, voice, queued=set(), n=2, playcounts=plays) == [
        KEYS[7],
        KEYS[4],
    ]


def test_nothing_to_add_409(client: TestClient, fs: RouteFirestore) -> None:
    for k in KEYS:
        fs.col(FETCH_COLLECTION)[pool_id(k)] = {"song_key": k, "status": "failed", "source": "shared"}
    r = client.post(URL)
    assert r.status_code == 409 and r.json() == {"detail": {"reason": "nothing_to_add"}}
    assert not fs.cols.get(BATCHES) and not fs.cols.get(BUDGET)


# ---------------------------------------------------------------- request


def test_request_queues_personal_songs(client: TestClient, fs: RouteFirestore) -> None:
    r = client.post(URL)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["songs"] == KEYS  # by playcount (equal weights)
    assert body["est_cost_usd"] == pytest.approx(8 * 0.027)
    batch = fs.col(BATCHES)[body["batch_id"]]
    assert batch["user_id"] == USER_ID and batch["song_keys"] == KEYS and batch["month"] == _month()
    assert batch["est_cost_usd"] == pytest.approx(0.216)
    doc = fs.col(FETCH_COLLECTION)[pool_id(KEYS[0])]
    assert (doc["source"], doc["priority"], doc["status"], doc["batch_ids"]) == (
        "personal",
        2,
        "queued",
        [body["batch_id"]],
    )
    assert (doc["artist"], doc["title"], doc["spotify_id"]) == ("ABBA", "Dancing Queen", None)
    month = fs.col(BUDGET)[_month()]
    assert (month["songs"], month["batches"], month["alerted_80"]) == (8, 1, False)
    assert month["est_cost_usd"] == pytest.approx(0.216)
    assert fs.col(LAST)[USER_ID]["last_batch_id"] == body["batch_id"]


@pytest.fixture(scope="module")
def full_voice_store(tmp_path_factory: pytest.TempPathFactory) -> Iterator[VoiceDataStore]:
    """The untouched fixture voice.db, as the local stack loads it."""
    store = _store(fixtures.build(tmp_path_factory.mktemp("personal_full")))
    yield store
    store.close()


def test_local_stack_seed_is_pickable(
    make_client: MakeClient,
    fs: RouteFirestore,
    full_voice_store: VoiceDataStore,
    catalog_reader: CatalogReader,  # noqa: F811
) -> None:
    """``/api/test/voice/personal-seed``'s songs on the real fixtures (catalog, availability, voice.db): eligible by
    history, and a request queues them (Maestro's voice-session-5 relies on it)."""
    picks = personal_seed_candidates(catalog_reader, full_voice_store.require())[:PERSONAL_SEED_SONGS]
    assert picks, "the fixtures have no personal-pool candidates"
    assert not {k for k, _, _ in picks} & fixtures.pooled_song_keys()
    fs.cols["user_songs"] = personal_seed_rows(USER_ID, picks, datetime.now(UTC))
    client = make_client()
    app.dependency_overrides[get_voice_store] = lambda: full_voice_store

    status = client.get(URL).json()
    assert (status["eligible"], status["reason"]) == (True, "history")
    r = client.post(URL)
    assert r.status_code == 201, r.text
    assert set(r.json()["songs"]) == {k for k, _, _ in picks}


def test_shared_row_has_no_user_id(client: TestClient, fs: RouteFirestore, catalog_reader: CatalogReader) -> None:  # noqa: F811
    batch_id = client.post(URL).json()["batch_id"]
    for k in KEYS:
        doc = fs.col(FETCH_COLLECTION)[pool_id(k)]
        assert USER_ID not in repr(doc) and "user_id" not in doc and doc["batch_ids"] == [batch_id]
    for k in KEYS[:2]:  # analysed: the voice.db / BigQuery rows come from the queue doc
        fs.col(FETCH_COLLECTION)[pool_id(k)]["status"] = "analysed"
    pool, _, _ = asyncio.run(voicedb.load_sources(fs, catalog_reader))
    rows = [p for p in pool if p["song_key"] in KEYS]
    assert len(rows) == 8 and {p["source"] for p in rows} == {"personal"}
    assert all(USER_ID not in repr(p) and "user_id" not in p and "batch_ids" not in p for p in rows)


def test_song_queued_meanwhile_gets_the_batch_id_and_costs_nothing(
    client: TestClient, fs: RouteFirestore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A song a concurrent batch queued after the picks were made: the batch id is appended, not re-charged."""
    real = fs.get_many

    async def stale_get_many(collection: str, ids: list[str]) -> dict[str, dict[str, Any]]:
        out = await real(collection, ids)
        if collection == FETCH_COLLECTION:
            fs.col(FETCH_COLLECTION)[pool_id(KEYS[0])] = {"song_key": KEYS[0], "source": "personal",
                                                          "status": "queued", "batch_ids": ["other"]}  # fmt: skip
        return out

    monkeypatch.setattr(fs, "get_many", stale_get_many)
    body = client.post(URL).json()
    assert body["songs"] == KEYS and body["est_cost_usd"] == pytest.approx(7 * 0.027)
    assert fs.col(FETCH_COLLECTION)[pool_id(KEYS[0])]["batch_ids"] == ["other", body["batch_id"]]
    assert fs.col(BUDGET)[_month()]["songs"] == 7


def test_stale_cap_query_is_recomputed(client: TestClient, fs: RouteFirestore) -> None:
    """A last-batch pointer naming a batch the cap query didn't see: the request never commits on stale data."""
    fs.col(LAST)[USER_ID] = {"last_batch_id": "committed-meanwhile"}
    r = client.post(URL)
    assert r.status_code == 409 and r.json() == {"detail": {"reason": "concurrent_request"}}
    assert not fs.cols.get(BATCHES) and not fs.cols.get(BUDGET)


# ---------------------------------------------------------------- cap


def test_cap_20_per_30_days_and_admin_exempt(make_client: MakeClient, fs: RouteFirestore) -> None:
    _batch(fs, "aged", 20, days_ago=31)
    at = _batch(fs, "recent", 20, days_ago=10)
    c = make_client()
    body = c.get(URL).json()
    assert (body["cap"], body["used"]) == (20, 20)
    next_at = datetime.fromisoformat(body["next_allowed_at"])
    assert abs((next_at - (at + timedelta(days=30))).total_seconds()) < 1
    r = c.post(URL)
    assert r.status_code == 429 and r.json()["detail"]["reason"] == "cap"
    assert abs((datetime.fromisoformat(r.json()["detail"]["next_allowed_at"]) - next_at).total_seconds()) < 1
    admin = make_client(admin=True)
    assert admin.get(URL).json()["next_allowed_at"] is None
    r = admin.post(URL)
    assert r.status_code == 201 and len(r.json()["songs"]) == 8


def test_cap_next_allowed_is_when_enough_ages_out() -> None:
    now = datetime(2026, 10, 27, tzinfo=UTC)
    batches = [
        {"requested_at": now - timedelta(days=20), "song_keys": ["a"] * 5},
        {"requested_at": now - timedelta(days=12), "song_keys": ["a"] * 10},
        {"requested_at": now - timedelta(days=2), "song_keys": ["a"] * 5},
    ]
    assert vps.cap_usage(batches, now, 20, 30) == (20, now + timedelta(days=10))
    assert vps.cap_usage(batches[1:], now, 20, 30) == (15, None)


def test_cap_trims_to_remaining(client: TestClient, fs: RouteFirestore) -> None:
    _batch(fs, "recent", 15, days_ago=3)
    body = client.post(URL).json()
    assert body["songs"] == KEYS[:5] and body["est_cost_usd"] == pytest.approx(5 * 0.027)
    r = client.get(URL).json()
    assert r["used"] == 20 and r["next_allowed_at"] is not None
    assert client.post(URL).status_code == 429


# ---------------------------------------------------------------- budget


def test_budget_guard_pauses(client: TestClient, fs: RouteFirestore, caplog: pytest.LogCaptureFixture) -> None:
    fs.col(BUDGET)[_month()] = {
        "month": _month(),
        "est_cost_usd": 19.98,
        "songs": 740,
        "batches": 40,
        "alerted_80": True,
    }  # fmt: skip  (0.02 left: not one song fits)
    r = client.post(URL)
    assert r.status_code == 503 and r.json() == {"detail": {"reason": "budget_paused"}}
    assert fs.col(BUDGET)[_month()]["est_cost_usd"] == 19.98 and not fs.cols.get(BATCHES)
    assert not fs.cols.get(FETCH_COLLECTION)
    assert any(r.levelno == logging.WARNING and "budget" in r.getMessage() for r in caplog.records)
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)
    fs.col(BUDGET)[_month()]["est_cost_usd"] = 19.7  # 8 songs (0.216) fit again
    r = client.post(URL)
    assert r.status_code == 201 and len(r.json()["songs"]) == 8


def test_budget_trims_to_what_fits(client: TestClient, fs: RouteFirestore, caplog: pytest.LogCaptureFixture) -> None:
    """0.10 left at 0.027 a song: the request is trimmed to 3 songs, as the cap trims."""
    caplog.set_level(logging.INFO)
    fs.col(BUDGET)[_month()] = {"month": _month(), "est_cost_usd": 19.9, "songs": 737, "batches": 40,
                                "alerted_80": True, "alerted_80_budget": 20.0}  # fmt: skip
    r = client.post(URL)
    assert r.status_code == 201
    body = r.json()
    assert body["songs"] == KEYS[:3] and body["est_cost_usd"] == pytest.approx(3 * 0.027)
    assert fs.col(BATCHES)[body["batch_id"]]["song_keys"] == KEYS[:3]
    assert sorted(fs.col(FETCH_COLLECTION)) == sorted(pool_id(k) for k in KEYS[:3])
    month = fs.col(BUDGET)[_month()]
    assert month["est_cost_usd"] == pytest.approx(19.981) and month["songs"] == 740
    assert any("trimmed to 3 of 8" in r.getMessage() for r in caplog.records)
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)
    assert client.post(URL).status_code == 503  # now nothing fits


def test_free_songs_go_in_when_the_budget_is_used_up(
    client: TestClient, fs: RouteFirestore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A song a concurrent batch queued meanwhile costs nothing: it still joins a batch when no new song fits."""
    fs.col(BUDGET)[_month()] = {"month": _month(), "est_cost_usd": 20.0, "songs": 740, "batches": 40}
    real = fs.get_many

    async def stale_get_many(collection: str, ids: list[str]) -> dict[str, dict[str, Any]]:
        out = await real(collection, ids)
        if collection == FETCH_COLLECTION:
            fs.col(FETCH_COLLECTION)[pool_id(KEYS[0])] = {"song_key": KEYS[0], "source": "personal",
                                                          "status": "queued", "batch_ids": ["other"]}  # fmt: skip
        return out

    monkeypatch.setattr(fs, "get_many", stale_get_many)
    r = client.post(URL)
    assert r.status_code == 201 and r.json()["songs"] == [KEYS[0]] and r.json()["est_cost_usd"] == 0
    assert fs.col(FETCH_COLLECTION)[pool_id(KEYS[0])]["batch_ids"] == ["other", r.json()["batch_id"]]
    assert len(fs.col(FETCH_COLLECTION)) == 1 and fs.col(BUDGET)[_month()]["est_cost_usd"] == 20.0


def test_admin_cap_exempt_budget_not(make_client: MakeClient, fs: RouteFirestore) -> None:
    _batch(fs, "recent", 40, days_ago=1)
    fs.col(BUDGET)[_month()] = {"month": _month(), "est_cost_usd": 20.0, "songs": 740, "batches": 41}
    r = make_client(admin=True).post(URL)
    assert r.status_code == 503 and r.json()["detail"]["reason"] == "budget_paused"


def test_budget_alert_once_at_80pct(
    make_client: MakeClient, fs: RouteFirestore, caplog: pytest.LogCaptureFixture
) -> None:
    fs.col(BUDGET)[_month()] = {"month": _month(), "est_cost_usd": 15.9, "songs": 589, "batches": 30}
    assert make_client().post(URL).status_code == 201  # 15.9 + 0.216 = 16.116 >= 80% of 20
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    msg = errors[0].getMessage()
    assert "81%" in msg and "$20.00" in msg and "$16.12" in msg and "VOICE_MONTHLY_BUDGET_USD" in msg
    assert fs.col(BUDGET)[_month()]["alerted_80"] is True and fs.col(BUDGET)[_month()]["alerted_80_budget"] == 20.0
    fs.cols.pop(FETCH_COLLECTION)  # the other user's songs are the same ones: free them again
    assert make_client(user_id=OTHER_ID).post(URL).status_code == 201
    assert len([r for r in caplog.records if r.levelno >= logging.ERROR]) == 1


def test_budget_alert_rearms_on_a_budget_raise(
    make_client: MakeClient, fs: RouteFirestore, caplog: pytest.LogCaptureFixture
) -> None:
    fs.col(BUDGET)[_month()] = {"month": _month(), "est_cost_usd": 23.9, "songs": 885, "batches": 44,
                                "alerted_80": True, "alerted_80_budget": 20.0}  # fmt: skip
    assert make_client(budget=20.0, user_id=OTHER_ID).post(URL).status_code == 503  # over the old budget
    assert make_client(budget=30.0).post(URL).status_code == 201  # 24.116 >= 80% of the raised 30
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and "$30.00" in errors[0].getMessage()
    assert fs.col(BUDGET)[_month()]["alerted_80_budget"] == 30.0
    fs.cols.pop(FETCH_COLLECTION)
    assert make_client(budget=30.0, user_id=OTHER_ID).post(URL).status_code == 201  # same budget: no repeat
    assert len([r for r in caplog.records if r.levelno >= logging.ERROR]) == 1


def test_below_80pct_no_alert(client: TestClient, fs: RouteFirestore, caplog: pytest.LogCaptureFixture) -> None:
    fs.col(BUDGET)[_month()] = {"month": _month(), "est_cost_usd": 15.0, "songs": 555, "batches": 28}
    assert client.post(URL).status_code == 201
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)
    assert fs.col(BUDGET)[_month()]["alerted_80"] is False


def test_next_month_label() -> None:
    assert vps._next_month("2026-10") == "2026-11" and vps._next_month("2026-12") == "2027-01"


# ---------------------------------------------------------------- status


def test_status_ready_counts(client: TestClient, fs: RouteFirestore) -> None:
    analysed_missing = KEYS[0]  # analysed, but the current voice.db predates it
    old = datetime.now(UTC) - timedelta(days=2)
    fs.col(BATCHES)["b_done"] = {"user_id": USER_ID, "song_keys": [IN_VOICE_DB, analysed_missing, KEYS[1]],
                                 "requested_at": old, "est_cost_usd": 0.081, "month": _month(old)}  # fmt: skip
    fs.col(BATCHES)["b_running"] = {"user_id": USER_ID, "song_keys": [KEYS[2], KEYS[3]],
                                    "requested_at": old + timedelta(hours=1), "est_cost_usd": 0.054,
                                    "month": _month(old)}  # fmt: skip
    fs.col(BATCHES)["b_queued"] = {"user_id": USER_ID, "song_keys": [KEYS[4]],
                                   "requested_at": old + timedelta(hours=2), "est_cost_usd": 0.027,
                                   "month": _month(old)}  # fmt: skip
    fs.col(BATCHES)["theirs"] = {"user_id": OTHER_ID, "song_keys": [KEYS[5]], "requested_at": old}
    for k, st in ((IN_VOICE_DB, "analysed"), (analysed_missing, "analysed"), (KEYS[1], "failed"),
                  (KEYS[2], "separating"), (KEYS[3], "queued"), (KEYS[4], "queued")):  # fmt: skip
        fs.col(FETCH_COLLECTION)[pool_id(k)] = {"song_key": k, "status": st, "source": "personal"}
    fs.col(LAST)[USER_ID] = {"last_batch_id": "b_queued"}
    body = client.get(URL).json()
    assert body["used"] == 6 and body["cap"] == 20 and body["next_allowed_at"] is None
    got = [(b["batch_id"], b["total"], b["ready"], b["status"]) for b in body["batches"]]
    assert got == [("b_queued", 1, 0, "queued"), ("b_running", 2, 0, "running"), ("b_done", 3, 1, "done")]


def test_batch_progress() -> None:
    assert vps.batch_progress(["analysed", "failed", None], 1) == (1, "done")
    assert vps.batch_progress(["queued", "downloading"], 0) == (0, "running")
    assert vps.batch_progress(["queued"], 0) == (0, "queued")


# ---------------------------------------------------------------- admin costs


def test_admin_costs(make_client: MakeClient, fs: RouteFirestore) -> None:
    fs.col(BUDGET)[_month()] = {"month": _month(), "est_cost_usd": 3.24, "songs": 120, "batches": 7}
    fs.col(BUDGET)["2001-01"] = {"month": "2001-01", "est_cost_usd": 19.0, "songs": 700, "batches": 35}
    for i, (source, st) in enumerate(
        [("shared", "analysed"), ("shared", "analysed"), ("andrew_sheet", "analysed"), ("personal", "analysed"),
         ("shared", "queued")]
    ):  # fmt: skip
        fs.col(FETCH_COLLECTION)[f"q{i}"] = {"song_key": f"k{i}", "source": source, "status": st}
    assert make_client().get("/api/admin/voice/costs").status_code == 403
    r = make_client(admin=True).get("/api/admin/voice/costs")
    assert r.status_code == 200
    assert r.json() == {
        "month": _month(),
        "personal_batches": 7,
        "personal_songs": 120,
        "est_cost_usd": 3.24,
        "budget_usd": 20.0,
        "paused": False,
        "shared_songs_analysed": 3,
        "est_cost_per_song_usd": 0.027,
    }
    fs.col(BUDGET)[_month()]["est_cost_usd"] = 19.97  # one song (0.027) still fits
    assert make_client(admin=True).get("/api/admin/voice/costs").json()["paused"] is False
    fs.col(BUDGET)[_month()]["est_cost_usd"] = 19.99
    assert make_client(admin=True).get("/api/admin/voice/costs").json()["paused"] is True


def test_songs_that_fit() -> None:
    assert vps.songs_that_fit(19.9, 20.0, 0.027) == 3
    assert vps.songs_that_fit(20.0 - 0.027 * 2, 20.0, 0.027) == 2  # exact fits despite float error
    assert vps.songs_that_fit(20.5, 20.0, 0.027) == 0
