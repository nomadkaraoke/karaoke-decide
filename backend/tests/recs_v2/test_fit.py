"""Vocal fit in recommendations v2: fit term, Singable filter, sort=fit, RecSong.fit, voice block.

Baritone profile (45-69, comfort 50-64) on the fixture catalog's vocal ranges
(``tests/fixtures/vocal_range/*.tsv``): Muse "Starlight" fits, "Uprising" fitsIn -2,
"Supermassive Black Hole" is a stretch, Adele "Hello" is outside (wide range).
"""

from __future__ import annotations

import random
import shutil
import sqlite3
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_catalog_store_dep as get_store_dep
from backend.api.deps import get_current_user, get_firestore, get_hidden_keys_provider
from backend.main import app
from backend.services.crowd_stats import crowd_doc_id
from backend.services.recs_v2 import service as svc
from backend.services.recs_v2 import weights as w
from backend.services.recs_v2.candidates import Candidate
from backend.services.recs_v2.context import ArtistSignal, UserContextV2
from backend.services.recs_v2.filters import RecFilters, where_sql
from backend.services.recs_v2.scoring import Scored, score
from backend.services.recs_v2.sections import BUCKET_RANK, _flat_key
from backend.services.voice_profile_service import VoiceProfileOut
from backend.tests.recs_v2.factories import cand
from karaoke_decide.catalog.reader import CatalogReader
from karaoke_decide.core.models import User
from karaoke_decide.vocal_range.fit import (
    STRETCH_MAX,
    TIER_TOLERANCE,
    FitResult,
    SongVocalRange,
    UserVoiceRange,
    fit_song,
    is_singable,
)
from karaoke_decide.vocal_range.tsv import SongRange

URL = "/api/my/recommendations/v2"
BARITONE = UserVoiceRange(low=45, high=69, comfort_low=50, comfort_high=64)
PROFILE: dict[str, Any] = {
    "low_midi": 45,
    "high_midi": 69,
    "comfort_low": 50,
    "comfort_high": 64,
    "voice_type": "baritone",
    "register": None,
    "confidence": "high",
    "method": "mic",
    "measured_at": "2026-10-20T10:00:00Z",
    "updated_at": "2026-10-20T10:00:00Z",
}
STARLIGHT, UPRISING, SUPERMASSIVE, HELLO = (
    "muse|starlight",
    "muse|uprising",
    "muse|supermassive black hole",
    "adele|hello",
)
HYSTERIA = "muse|hysteria"  # no own range: Muse's artist range, tier rough
ADELE_ROUGH = "adele|someone like you"  # rough (Adele's artist range), fitsIn an octave down


def _ctx() -> UserContextV2:
    """Loves Muse and Adele, so every one of their songs is a candidate."""
    return UserContextV2(
        user_id="guest_fit",
        artists={"muse": ArtistSignal(0.9, "manual", "Muse"), "adele": ArtistSignal(0.9, "manual", "Adele")},
    )


def _keys(cands: list[Candidate]) -> set[str]:
    return {c.song_key for c in cands}


# ------------------------------------------------------------------ Singable filter (service level)


def test_singable_filters_to_fitting_songs(reader: CatalogReader) -> None:
    got = svc.fitted_candidates(reader, _ctx(), RecFilters(singable=True), "best", BARITONE)
    keys = _keys(got)
    assert HELLO not in keys  # the wide-range "outside" song
    assert UPRISING in keys  # fitsIn -2
    assert STARLIGHT in keys
    assert SUPERMASSIVE not in keys  # a stretch is not singable
    assert got and all(c.fit is not None and is_singable(c.fit) for c in got)
    up = next(c for c in got if c.song_key == UPRISING)
    assert up.fit is not None and (up.fit.bucket, up.fit.transpose, up.fit.octave) == ("fitsIn", -2, 0)


def test_singable_keeps_rough_tier_songs(reader: CatalogReader) -> None:
    got = {c.song_key: c for c in svc.fitted_candidates(reader, _ctx(), RecFilters(singable=True), "best", BARITONE)}
    hysteria, adele = got[HYSTERIA], got[ADELE_ROUGH]
    assert hysteria.vr is not None and hysteria.vr.tier == "rough"
    assert hysteria.fit is not None and hysteria.fit.bucket == "fits"
    assert adele.vr is not None and adele.vr.tier == "rough"
    assert adele.fit is not None and (adele.fit.bucket, adele.fit.octave) == ("fitsIn", -12)


def test_singable_excludes_songs_without_range(reader: CatalogReader) -> None:
    every = svc.fitted_candidates(reader, _ctx(), RecFilters(), "best", BARITONE)
    assert any(c.vr is None for c in every)  # crowd pleasers etc. with no vr_*
    singable = svc.fitted_candidates(reader, _ctx(), RecFilters(singable=True), "best", BARITONE)
    assert all(c.vr is not None for c in singable)


def test_no_profile_means_no_fit(reader: CatalogReader) -> None:
    got = svc.fitted_candidates(reader, _ctx(), RecFilters(), "best", None)
    assert got and all(c.fit is None for c in got)
    assert any(c.vr is not None for c in got)  # the range is still read, just not fitted


def test_singable_and_library_filters_combine(reader: CatalogReader) -> None:
    ctx = _ctx()
    singable = _keys(svc.fitted_candidates(reader, ctx, RecFilters(singable=True), "best", BARITONE))
    male = _keys(svc.fitted_candidates(reader, ctx, RecFilters(lead_gender="male"), "best", BARITONE))
    both = _keys(svc.fitted_candidates(reader, ctx, RecFilters(singable=True, lead_gender="male"), "best", BARITONE))
    assert both and both == singable & male
    karaoke = _keys(svc.fitted_candidates(reader, ctx, RecFilters(has_karaoke="yes"), "best", BARITONE))
    both_k = _keys(svc.fitted_candidates(reader, ctx, RecFilters(singable=True, has_karaoke="yes"), "best", BARITONE))
    assert both_k == singable & karaoke


def test_singable_prefilter_limits_fit_calls(reader: CatalogReader) -> None:
    """fit_song only runs for rows that passed the SQL prefilter, never for songs without vr_*."""
    seen: list[SongVocalRange] = []

    def spy(user: UserVoiceRange, song: SongVocalRange) -> FitResult:
        seen.append(song)
        return fit_song(user, song)

    with patch.object(svc, "fit_song", side_effect=spy):
        got = svc.fitted_candidates(reader, _ctx(), RecFilters(singable=True), "best", BARITONE)

    span = BARITONE.comfort_high - BARITONE.comfort_low
    gathered = svc.gather_candidates(reader, _ctx(), RecFilters(singable=True), voice_span=span)
    assert gathered and all(c.vr is not None for c in gathered)  # SQL already dropped vr-less rows
    assert HELLO not in _keys(gathered)  # span 21 > 14 + 2 + 2: SQL drops it before Python
    assert len(seen) == len(gathered)  # one fit_song call per prefiltered row
    assert all(v.p90 - v.p10 <= span + 2 * TIER_TOLERANCE[v.tier] + STRETCH_MAX for v in seen)
    assert _keys(got) <= _keys(gathered)
    unfiltered = svc.gather_candidates(reader, _ctx(), RecFilters())
    assert len(unfiltered) > len(gathered)  # the prefilter really cut the fit work


def test_sort_fit_prefilters_without_singable(reader: CatalogReader) -> None:
    got = svc.fitted_candidates(reader, _ctx(), RecFilters(), "fit", BARITONE)
    keys = _keys(got)
    assert SUPERMASSIVE in keys  # stretch is ranked, not filtered
    assert HELLO not in keys and all(c.vr is not None for c in got)


# ------------------------------------------------------------------ SQL prefilter boundary


def _vr_db(rows: Sequence[tuple[str, float | None, float | None, str | None]]) -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.execute(
        "CREATE TABLE songs (song_key TEXT, vr_low REAL, vr_high REAL, vr_p10 REAL, vr_p50 REAL, vr_p90 REAL, "
        "vr_tier TEXT)"
    )
    for key, p10, p90, tier in rows:
        lo = None if p10 is None else p10 - 3
        hi = None if p90 is None else p90 + 3
        mid = None if p10 is None or p90 is None else (p10 + p90) / 2
        c.execute("INSERT INTO songs VALUES (?,?,?,?,?,?,?)", (key, lo, hi, p10, mid, p90, tier))
    return c


def _passing(c: sqlite3.Connection, span: int) -> set[str]:
    cond, params = where_sql(RecFilters(), has_availability=False, voice_span=span)
    return {r[0] for r in c.execute(f"SELECT s.song_key FROM songs s WHERE {cond}", params)}


def test_prefilter_boundary_is_inclusive() -> None:
    # comfort span 14: limit = 14 + 2*tol + STRETCH_MAX (2)
    c = _vr_db(
        [
            ("measured_at", 50.0, 66.0, "measured"),  # 16 == 14 + 0 + 2: kept
            ("measured_over", 50.0, 66.01, "measured"),
            ("notes_at", 50.0, 66.0, "notes"),
            ("estimated_at", 50.0, 68.0, "estimated"),  # 18 == 14 + 2 + 2
            ("estimated_over", 50.0, 68.5, "estimated"),
            ("rough_at", 50.0, 70.0, "rough"),  # 20 == 14 + 4 + 2
            ("rough_over", 50.0, 70.5, "rough"),
            ("no_range", None, None, None),
            ("bad_tier", 50.0, 52.0, "guess"),
        ]
    )
    assert _passing(c, 14) == {"measured_at", "notes_at", "estimated_at", "rough_at"}


def test_prefilter_never_drops_a_singable_song() -> None:
    """Necessary condition: any song fit_song rates fits/fitsIn passes the SQL prefilter."""
    rng = random.Random(1234)
    rows = []
    tiers = list(TIER_TOLERANCE)
    songs: dict[str, SongVocalRange] = {}
    for i in range(3000):
        p10 = rng.uniform(40, 75)
        p90 = p10 + rng.uniform(0, 26)
        tier = rng.choice(tiers)
        key = f"s{i}"
        rows.append((key, p10, p90, tier))
        songs[key] = SongVocalRange(p10 - 3, p90 + 3, p10, (p10 + p90) / 2, p90, tier)  # type: ignore[arg-type]
    c = _vr_db(rows)
    for user in (BARITONE, UserVoiceRange(55, 79, 60, 72), UserVoiceRange(50, 60, 53, 57)):
        passing = _passing(c, user.comfort_high - user.comfort_low)
        singable = {k for k, s in songs.items() if is_singable(fit_song(user, s))}
        assert singable and singable <= passing


def test_no_prefilter_without_voice_span() -> None:
    assert where_sql(RecFilters(singable=True), has_availability=False) == ("1", [])


# ------------------------------------------------------------------ scoring


def _fit(bucket: str) -> FitResult:
    return FitResult(bucket, 0, 0, 0.0, 0.0)  # type: ignore[arg-type]


def test_fit_term_raises_fitting_song_in_best_match() -> None:
    ctx = UserContextV2(user_id="u")
    base = score(cand("a|x"), ctx).score
    assert score(cand("a|x", fit=_fit("fits")), ctx).score == pytest.approx(base + w.W_FIT * 1.0)
    assert score(cand("a|x", fit=_fit("fitsIn")), ctx).score == pytest.approx(base + w.W_FIT * 0.7)
    assert score(cand("a|x", fit=_fit("stretch")), ctx).score == pytest.approx(base + w.W_FIT * 0.2)
    assert score(cand("a|x", fit=_fit("outside")), ctx).score == pytest.approx(base - w.W_FIT * 0.5)
    assert w.W_FIT == 0.15
    assert w.FIT_AFFINITY == {"fits": 1.0, "fitsIn": 0.7, "stretch": 0.2, "outside": -0.5}


def test_fit_term_reorders_best_match_on_catalog(reader: CatalogReader) -> None:
    ctx = _ctx()
    with_voice = {
        c.song_key: score(c, ctx).score for c in svc.fitted_candidates(reader, ctx, RecFilters(), "best", BARITONE)
    }
    no_voice = {c.song_key: score(c, ctx).score for c in svc.fitted_candidates(reader, ctx, RecFilters(), "best", None)}
    assert with_voice[STARLIGHT] - no_voice[STARLIGHT] == pytest.approx(0.15)
    assert with_voice[HELLO] - no_voice[HELLO] == pytest.approx(-0.075)
    assert with_voice[STARLIGHT] - with_voice[HELLO] > no_voice[STARLIGHT] - no_voice[HELLO]


# ------------------------------------------------------------------ route


class FakeStore:
    def __init__(self, reader: CatalogReader) -> None:
        self._reader = reader
        self.run = "fixture-run"

    def require(self) -> CatalogReader:
        return self._reader


class FakeHidden:
    async def hidden_keys(self, user_id: str) -> set[str]:
        return set()


SONGS = [  # known songs make Muse and Adele known artists
    {"user_id": "guest_fit", "artist": "Muse", "title": "Starlight", "source": "manual"},
    {"user_id": "guest_fit", "artist": "Adele", "title": "Hello", "source": "manual"},
]


def _firestore(user_doc: dict[str, Any]) -> MagicMock:
    fs = MagicMock()
    fs.get_document = AsyncMock(return_value=user_doc)
    fs.get_many = AsyncMock(return_value={})  # no crowd stats

    async def query(collection: str, **_: Any) -> list[dict[str, Any]]:
        if collection == "user_songs":
            return SONGS
        return []

    fs.query_documents = AsyncMock(side_effect=query)
    return fs


@pytest.fixture
def client_for(reader: CatalogReader) -> Iterator[Callable[..., TestClient]]:
    def make(*, profile: dict[str, Any] | None = PROFILE, store_reader: CatalogReader | None = None) -> TestClient:
        doc: dict[str, Any] = {"user_id": "guest_fit"}
        if profile is not None:
            doc["voice_profile"] = profile
        app.dependency_overrides[get_current_user] = lambda: User(id="guest_fit", is_guest=True)
        app.dependency_overrides[get_firestore] = lambda: _firestore(doc)
        app.dependency_overrides[get_store_dep] = lambda: FakeStore(store_reader or reader)
        app.dependency_overrides[get_hidden_keys_provider] = lambda: FakeHidden()
        return TestClient(app)

    yield make
    app.dependency_overrides.clear()


def _songs(body: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for sec in body["sections"] for s in sec["songs"]]


def test_rec_song_carries_fit(client_for: Callable[..., TestClient]) -> None:
    body = client_for().get(URL).json()  # best: both known songs sit in songs_you_know
    assert body["voice"] == {"has_profile": True, "method": "mic"}
    songs = {s["song_key"]: s for s in _songs(body)}
    assert songs[STARLIGHT]["fit"] == {"bucket": "fits", "transpose": 0, "octave": 0, "tier": "measured"}
    assert songs[HELLO]["fit"]["bucket"] == "outside"
    no_range = [s for s in songs.values() if s["fit"] is None]
    assert no_range  # songs with no vr_* carry fit: null

    body = client_for(profile=None).get(URL).json()
    assert body["voice"] == {"has_profile": False, "method": None}
    assert all(s["fit"] is None for s in _songs(body))


def test_singable_route_echo_and_fit(client_for: Callable[..., TestClient]) -> None:
    body = client_for().get(URL, params={"singable": "true", "sort": "az"}).json()
    assert body["filters_applied"] == {"singable": True}
    songs = _songs(body)
    assert songs and all(s["fit"]["bucket"] in ("fits", "fitsIn") for s in songs)
    assert HELLO not in {s["song_key"] for s in songs}


def test_singable_ignored_without_profile(client_for: Callable[..., TestClient]) -> None:
    client = client_for(profile=None)
    plain = client.get(URL).json()
    body = client.get(URL, params={"singable": "true"}).json()
    assert "singable" not in body["filters_applied"]
    assert body["voice"]["has_profile"] is False
    assert [s["song_key"] for s in _songs(body)] == [s["song_key"] for s in _songs(plain)]
    assert HELLO in {s["song_key"] for s in _songs(body)}


def test_sort_fit_orders_by_score(client_for: Callable[..., TestClient], reader: CatalogReader) -> None:
    body = client_for().get(URL, params={"sort": "fit"}).json()
    assert [sec["id"] for sec in body["sections"]] == ["all"]
    songs = _songs(body)
    assert songs and all(s["fit"] is not None for s in songs)
    keys = [s["song_key"] for s in songs]
    rows = {
        r["song_key"]: r
        for r in reader.conn().execute(f"SELECT * FROM songs WHERE song_key IN ({','.join('?' * len(keys))})", keys)
    }

    def order(k: str) -> tuple[int, float, int]:
        r = rows[k]
        f = fit_song(
            BARITONE,
            SongVocalRange(
                r["vr_low"], r["vr_high"], r["vr_p10"], r["vr_p50"], r["vr_p90"], r["vr_tier"], r["vr_octave_conf"]
            ),
        )
        return (BUCKET_RANK[f.bucket], f.score, -(r["popularity"] or 0))

    assert [order(k) for k in keys] == sorted(order(k) for k in keys)
    assert songs[0]["fit"]["bucket"] == "fits"


def _fit_scored(key: str, bucket: str, fit_score: float) -> Scored:
    c = cand(key)
    c.fit = FitResult(bucket, 0, -1, fit_score, fit_score)  # type: ignore[arg-type]
    return Scored(c, 0.5, "crowd_pleaser", None)


def test_sort_fit_ranks_bucket_before_raw_score() -> None:
    """Baritone, comfort 48-62: an octave penalty must not lift a worse bucket over a better one."""
    fits_in_octave = _fit_scored("a|octave down fitsin", "fitsIn", 0.75)
    stretch = _fit_scored("b|plain stretch", "stretch", 0.5)
    octave_stretch = _fit_scored("c|octave stretch", "stretch", 1.1)
    outside = _fit_scored("d|outside", "outside", 0.9)  # raw score below the octave stretch
    fits = _fit_scored("e|fits", "fits", 0.2)
    no_fit = Scored(cand("f|none"), 0.5, "crowd_pleaser", None)
    key = _flat_key("fit")
    ranked = sorted([no_fit, outside, octave_stretch, stretch, fits_in_octave, fits], key=key)
    assert [s.c.song_key for s in ranked] == [
        fits.c.song_key,
        fits_in_octave.c.song_key,
        stretch.c.song_key,
        octave_stretch.c.song_key,
        outside.c.song_key,
        no_fit.c.song_key,
    ]


def test_sort_fit_pages(client_for: Callable[..., TestClient], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "PAGE_SIZE", 2)
    client = client_for()
    first = client.get(URL, params={"sort": "fit"}).json()
    assert len(_songs(first)) == 2 and first["next_cursor"]
    second = client.get(URL, params={"sort": "fit", "cursor": first["next_cursor"]}).json()
    assert len(_songs(second)) >= 1
    assert not {s["song_key"] for s in _songs(first)} & {s["song_key"] for s in _songs(second)}


def test_sort_fit_without_profile_is_popularity(client_for: Callable[..., TestClient]) -> None:
    body = client_for(profile=None).get(URL, params={"sort": "fit"}).json()
    pops = [s["facets"]["popularity"] or 0 for s in _songs(body)]
    assert pops and pops == sorted(pops, reverse=True)


def test_voice_profile_failure_degrades_to_no_profile(client_for: Callable[..., TestClient]) -> None:
    with patch(
        "backend.services.voice_profile_service.VoiceProfileService.get", side_effect=RuntimeError("firestore down")
    ):
        r = client_for().get(URL, params={"sort": "az"})
    assert r.status_code == 200
    assert r.json()["voice"] == {"has_profile": False, "method": None}


@pytest.fixture
def reader_v1(catalog_dir: Path, tmp_path: Path) -> Iterator[CatalogReader]:
    """A schema-1 catalog.db (no vr_* columns), as prod has until the vocal-range catalog build."""
    path = tmp_path / "catalog_v1.db"
    shutil.copy(catalog_dir / "catalog.db", path)
    c = sqlite3.connect(path)
    for col in ("vr_low", "vr_high", "vr_p10", "vr_p50", "vr_p90", "vr_tier", "vr_octave_conf", "vr_duet"):
        c.execute(f"ALTER TABLE songs DROP COLUMN {col}")
    c.commit()
    c.close()
    r = CatalogReader(path, None)
    yield r
    r.close()


def test_old_catalog_without_vr_columns(client_for: Callable[..., TestClient], reader_v1: CatalogReader) -> None:
    assert reader_v1.has_vocal_range is False
    client = client_for(store_reader=reader_v1)
    best = client.get(URL)
    assert best.status_code == 200 and _songs(best.json())
    assert all(s["fit"] is None for s in _songs(best.json()))
    singable = client.get(URL, params={"singable": "true", "sort": "az"})
    assert singable.status_code == 200 and singable.json()["sections"] == []
    assert reader_v1.vocal_range(STARLIGHT) is None


def test_old_catalog_sort_fit_with_profile(client_for: Callable[..., TestClient], reader_v1: CatalogReader) -> None:
    """Schema-1 catalog: sort=fit with a profile degrades to fit: null songs in popularity order."""
    r = client_for(store_reader=reader_v1).get(URL, params={"sort": "fit"})
    assert r.status_code == 200
    songs = _songs(r.json())
    assert songs and all(s["fit"] is None for s in songs)
    pops = [s["facets"]["popularity"] or 0 for s in songs]
    assert pops == sorted(pops, reverse=True)
    assert r.json()["voice"]["has_profile"] is True


class NoStoredProfile:
    async def get(self, user_id: str) -> Any:
        raise AssertionError("an explicit profile skips the stored-profile read")


async def test_recommend_with_explicit_profile(reader: CatalogReader) -> None:
    """A voice session's result (saved or not) ranks with its own profile, not the stored one."""
    session = VoiceProfileOut.model_validate(
        {**PROFILE, "method": "session", "confidence": "low", "evidence_count": 2, "session_id": "s1"}
    )
    service = svc.RecommendationServiceV2(
        _firestore({"user_id": "guest_fit"}), FakeStore(reader), FakeHidden(), voice=NoStoredProfile()
    )
    res = await service.recommend(
        User(id="guest_fit", is_guest=True), RecFilters(singable=True), "fit", profile=session
    )
    assert res.voice.model_dump() == {"has_profile": True, "method": "session"}
    songs = [s for sec in res.sections for s in sec.songs]
    assert songs and all(s.fit is not None and s.fit.bucket in ("fits", "fitsIn") for s in songs)


# ------------------------------------------------------------------ Phase 4b: per-song key hints


def _ranged(key: str, bucket: str, transpose: int = 0, octave: int = 0) -> Scored:
    c = cand(key, "Muse", vr=SongRange(50, 62, 51, 56, 61, "measured"))
    c.fit = FitResult(bucket, transpose, octave, 0.0, 1.0)  # type: ignore[arg-type]
    return Scored(c=c, score=1.0, reason_type="known_song", reason_artist=None)


def _hinted(bucket: str, transpose: int, hint: int, octave: int = 0) -> tuple[str, int, int]:
    fit = svc._rec_song(_ranged("m|s", bucket, transpose, octave), {"m|s": hint}).fit
    assert fit is not None
    return fit.bucket, fit.transpose, fit.octave


def test_key_hint_sets_transpose() -> None:
    """R20a: a hint shifts only an in-key fit (fits / fitsIn, octave 0); 0 after the shift is ``fits``."""
    assert svc._rec_song(_ranged("m|high", "fits"), {"m|high": -2}).fit.model_dump() == {  # type: ignore[union-attr]
        "bucket": "fitsIn", "transpose": -2, "octave": 0, "tier": "measured"
    }  # fmt: skip
    assert _hinted("fitsIn", -2, 2) == ("fits", 0, 0)
    assert _hinted("fitsIn", -1, 2) == ("fitsIn", 1, 0)
    assert _hinted("stretch", 1, -2) == ("stretch", 1, 0)
    assert _hinted("outside", 0, -2) == ("outside", 0, 0)
    assert _hinted("fitsIn", 1, -2, octave=-1) == ("fitsIn", 1, -1)  # octave fits stay untouched
    plain = svc._rec_song(_ranged("m|none", "fits"), {"m|other": -2}).fit
    assert plain is not None and (plain.bucket, plain.transpose) == ("fits", 0)
    no_fit = cand("m|high")
    assert (
        svc._rec_song(Scored(c=no_fit, score=1.0, reason_type="known_song", reason_artist=None), {"m|high": -2}).fit
        is None
    )


async def test_key_hint_through_recommend(reader: CatalogReader) -> None:
    """End to end: Starlight fits the baritone, but the singer's last key answer was too high."""
    rows: list[dict[str, Any]] = [*SONGS, {"user_id": "guest_fit", "artist": "Muse", "title": "Starlight", "source": "karaoke_log",
                     "song_key": STARLIGHT, "times_sung": 1, "sung_key_latest": "too_high"}]  # fmt: skip
    fs = MagicMock()
    fs.get_document = AsyncMock(return_value={"user_id": "guest_fit"})

    async def query(collection: str, **_: Any) -> list[dict[str, Any]]:
        return rows if collection == "user_songs" else []

    fs.query_documents = AsyncMock(side_effect=query)
    profile = VoiceProfileOut.model_validate(PROFILE)
    service = svc.RecommendationServiceV2(fs, FakeStore(reader), FakeHidden(), voice=NoStoredProfile())
    res = await service.recommend(User(id="guest_fit", is_guest=True), RecFilters(), "best", profile=profile)
    fit = {s.song_key: s for sec in res.sections for s in sec.songs}[STARLIGHT].fit
    assert fit is not None and (fit.bucket, fit.transpose) == ("fitsIn", -2)


def _crowd_fs(rows: list[dict[str, Any]], crowd: dict[str, dict[str, Any]]) -> MagicMock:
    fs = MagicMock()
    fs.get_document = AsyncMock(return_value={"user_id": "guest_fit"})

    async def query(collection: str, **_: Any) -> list[dict[str, Any]]:
        return rows if collection == "user_songs" else []

    async def get_many(collection: str, ids: list[str]) -> dict[str, dict[str, Any]]:
        assert collection == "song_crowd_stats"
        stored = {crowd_doc_id(k): v for k, v in crowd.items()}  # R23: hashed doc ids
        return {i: stored[i] for i in ids if i in stored}

    fs.query_documents = AsyncMock(side_effect=query)
    fs.get_many = AsyncMock(side_effect=get_many)
    return fs


def _bucket_stats(bucket: str, right: int, too_high: int = 0, too_low: int = 0) -> dict[str, Any]:
    counts = {"right": right, "too_high": too_high, "too_low": too_low}
    return {"total": dict(counts), "by_bucket": {bucket: dict(counts)}}


async def _starlight_fit(rows: list[dict[str, Any]], crowd: dict[str, dict[str, Any]], reader: CatalogReader) -> Any:
    profile = VoiceProfileOut.model_validate(PROFILE)  # baritone
    service = svc.RecommendationServiceV2(
        _crowd_fs(rows, crowd), FakeStore(reader), FakeHidden(), voice=NoStoredProfile()
    )
    res = await service.recommend(User(id="guest_fit", is_guest=True), RecFilters(), "best", profile=profile)
    return {s.song_key: s for sec in res.sections for s in sec.songs}[STARLIGHT]


async def test_crowd_key_hint_for_viewer_bucket(reader: CatalogReader) -> None:
    """Baritones mostly found Starlight too high: the in-key fit shifts down 2, like the user's own hint."""
    song = await _starlight_fit(SONGS, {STARLIGHT: _bucket_stats("baritone", right=2, too_high=3)}, reader)
    assert (song.fit.bucket, song.fit.transpose) == ("fitsIn", -2)
    assert song.crowd is not None and song.crowd.bucket == "baritone"

    song = await _starlight_fit(SONGS, {STARLIGHT: _bucket_stats("baritone", right=2, too_low=3)}, reader)
    assert (song.fit.bucket, song.fit.transpose) == ("fitsIn", 2)

    # other buckets' votes don't move a baritone's key; nor do too few baritones
    song = await _starlight_fit(SONGS, {STARLIGHT: _bucket_stats("tenor", right=0, too_high=9)}, reader)
    assert (song.fit.bucket, song.fit.transpose) == ("fits", 0)
    few = _bucket_stats("baritone", right=0, too_high=4)
    few["total"]["right"] = 6
    song = await _starlight_fit(SONGS, {STARLIGHT: few}, reader)
    assert (song.fit.bucket, song.fit.transpose) == ("fits", 0)


async def test_own_key_hint_beats_crowd_hint(reader: CatalogReader) -> None:
    rows: list[dict[str, Any]] = [*SONGS, {"user_id": "guest_fit", "artist": "Muse", "title": "Starlight", "source": "karaoke_log",
                     "song_key": STARLIGHT, "times_sung": 1, "sung_key_latest": "too_low"}]  # fmt: skip
    song = await _starlight_fit(rows, {STARLIGHT: _bucket_stats("baritone", right=0, too_high=5)}, reader)
    assert (song.fit.bucket, song.fit.transpose) == ("fitsIn", 2)


async def test_own_right_answer_beats_crowd_hint(reader: CatalogReader) -> None:
    """The user's latest key answer was "right": the crowd's "too high" never shifts it."""
    rows: list[dict[str, Any]] = [*SONGS, {"user_id": "guest_fit", "artist": "Muse", "title": "Starlight", "source": "karaoke_log",
                     "song_key": STARLIGHT, "times_sung": 1, "sung_key_latest": "right"}]  # fmt: skip
    song = await _starlight_fit(rows, {STARLIGHT: _bucket_stats("baritone", right=0, too_high=5)}, reader)
    assert (song.fit.bucket, song.fit.transpose) == ("fits", 0)
