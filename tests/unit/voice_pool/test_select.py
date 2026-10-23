"""Voice-pool selection job: candidates (catalog.db + availability.db + BigQuery), the
embeddability probe loop and the run's outputs (fakes only: no GCP, no network)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
import pytest
from google.api_core.exceptions import NotFound
from google.cloud import bigquery

from karaoke_decide.services.bq_limits import MAX_BYTES_CATALOG
from karaoke_decide.voice_pool import queue, select
from karaoke_decide.voice_pool.models import pool_id
from karaoke_decide.voice_pool.versions import RateLimiter
from tests.unit.fake_gcs import FakeBucket

NO_WAIT = RateLimiter(0)
_COMMUNITY_SQL = """
SELECT 1 FROM av.versions WHERE song_key = ? AND kind = 'community' AND youtube_url IS NOT NULL
"""


def kn_id(reader, key: str) -> int:
    return reader.kn_ids_for_song_keys([key])[key]


def test_load_candidates_requires_community_version(reader, fake_bq):
    cands = select.load_candidates(reader, fake_bq(ultrastar={"adele|hello"}), n=1000)
    assert cands
    keys = {c.song_key for c in cands}
    conn = reader.conn()
    for c in cands:
        assert conn.execute(_COMMUNITY_SQL, (c.song_key,)).fetchone(), c.song_key
        assert c.versions and all(re.fullmatch(r"[A-Za-z0-9_-]{11}", v.video_id) for v in c.versions)
        assert [v.rank for v in c.versions] == list(range(len(c.versions)))  # ordinal among community versions
        assert c.spotify_id == f"sp{kn_id(reader, c.song_key):06d}" and c.duration_ms == 200_000
        assert c.popularity >= 45
    # In KaraokeNerds with only commercial versions (popularity 46): not a candidate.
    assert "muse|starlight live" not in keys
    assert not conn.execute(_COMMUNITY_SQL, ("muse|starlight live",)).fetchone()
    # Catalog-only (not in KN) songs never qualify.
    assert not any(k.startswith("fixture artist") for k in keys)

    adele = next(c for c in cands if c.song_key == "adele|hello")
    assert adele.has_ultrastar and adele.vr_span == pytest.approx(29.0) and adele.vr_p50 is not None
    assert adele.decade == "2010s+" and adele.vr_tier == "estimated" and adele.duet is False
    assert not any(c.has_ultrastar for c in cands if c.song_key != "adele|hello")
    decades = {c.decade for c in cands}
    assert decades <= {"pre1980", "1980s", "1990s", "2000s", "2010s+", None} and "pre1980" in decades
    assert all(c.vr_span is None for c in cands if c.vr_p50 is None)


def test_load_candidates_needs_spotify_track_in_duration_window(reader, fake_bq):
    base = {c.song_key for c in select.load_candidates(reader, fake_bq(), n=1000)}
    short, long_, gone = sorted(base)[:3]
    bq = fake_bq(
        durations={kn_id(reader, short): 119_000, kn_id(reader, long_): 421_000},
        missing={kn_id(reader, gone)},
    )
    keys = {c.song_key for c in select.load_candidates(reader, bq, n=1000)}
    assert keys == base - {short, long_, gone}


def test_min_pop_steps_down(reader, fake_bq, caplog):
    caplog.set_level(logging.INFO, logger=select.logger.name)
    # 3 x 1000 is far more than the fixture has: steps 60 -> 55 -> 50 -> 45 and stops there.
    low = select.load_candidates(reader, fake_bq(), n=1000)
    assert min(c.popularity for c in low) in range(45, 50)
    assert "min_pop 45" in caplog.text
    # A tiny target is met at the default floor.
    high = select.load_candidates(reader, fake_bq(), n=1)
    assert min(c.popularity for c in high) >= 60
    assert {c.song_key for c in high} < {c.song_key for c in low}
    # An explicit floor is respected.
    assert min(c.popularity for c in select.load_candidates(reader, fake_bq(), n=1, min_pop=80)) >= 80


def test_bq_query_has_byte_cap(reader, fake_bq):
    bq = fake_bq()
    select.load_candidates(reader, bq, n=10)
    assert bq.sqls == [select.SPOTIFY_FOR_KN_SQL, select.ULTRASTAR_SQL]
    assert all(c.maximum_bytes_billed == MAX_BYTES_CATALOG for c in bq.configs)
    assert "QUALIFY ROW_NUMBER() OVER (PARTITION BY l.karaoke_id" in select.SPOTIFY_FOR_KN_SQL
    assert "UNNEST(@kn_ids)" in select.SPOTIFY_FOR_KN_SQL
    assert "song_vocal_range_sources" in select.ULTRASTAR_SQL and "source = 'ultrastar'" in select.ULTRASTAR_SQL
    (kn_param,) = bq.configs[0].query_parameters
    assert kn_param.name == "kn_ids" and kn_param.array_type == "INT64" and kn_param.values


def test_missing_ultrastar_table_is_a_warning(reader, fake_bq, caplog):
    class Bq(fake_bq):
        def query(self, sql: str, job_config: Any = None) -> Any:
            if sql == select.ULTRASTAR_SQL:
                raise NotFound("song_vocal_range_sources")
            return super().query(sql, job_config)

    cands = select.load_candidates(reader, Bq(ultrastar={"adele|hello"}), n=10)
    assert cands and not any(c.has_ultrastar for c in cands)
    assert any(r.levelno == logging.WARNING and "ultrastar" in r.getMessage() for r in caplog.records)
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


def _client(blocked: set[str], seen: list[str]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        vid = request.url.params["url"].rsplit("=", 1)[1]
        seen.append(vid)
        return httpx.Response(404 if vid in blocked else 200, json={})

    return httpx.Client(transport=httpx.MockTransport(handler))


async def test_select_replaces_all_non_embeddable(reader, fake_bq):
    seen: list[str] = []
    with _client(set(), seen) as http:
        first = await select.run_select(reader, fake_bq(), None, http, n=5, run="r1", dry_run=True, limiter=NO_WAIT)
    assert first["picked"] == 5 and first["non_embeddable"] == 0 and first["probed"] == 5
    picks = first["songs"]
    victim = picks[0]
    victim_versions = [
        v.video_id for c in select.load_candidates(reader, fake_bq(), n=5) if c.song_key == victim for v in c.versions
    ]

    seen = []
    with _client(set(victim_versions), seen) as http:
        out = await select.run_select(reader, fake_bq(), None, http, n=5, run="r2", dry_run=True, limiter=NO_WAIT)
    assert out["picked"] == 5 and victim not in out["songs"]
    assert out["non_embeddable"] == 1
    assert set(victim_versions) <= set(seen)  # every version of the dropped song was tried
    assert out["probed"] == len(seen) == len(set(seen))  # each video probed once
    assert out["enqueued"] == 0 and out["candidates"] >= 5
    assert out["target"] == 5 and set(out["per_dimension"]) >= {"gender", "decade"}


async def test_select_unknown_probe_keeps_song(reader, fake_bq):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        out = await select.run_select(reader, fake_bq(), None, http, n=3, run="r", dry_run=True, limiter=NO_WAIT)
    assert out["picked"] == 3 and out["non_embeddable"] == 0 and out["probed"] == 3
    assert out["probe_unknown"] == 3


async def test_mostly_unknown_probes_warn_once(reader, fake_bq, caplog):
    blocked = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        blocked["n"] += 1
        return httpx.Response(429 if blocked["n"] % 4 else 200, json={})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        out = await select.run_select(reader, fake_bq(), None, http, n=8, run="r", dry_run=True, limiter=NO_WAIT)
    assert out["probe_unknown"] > out["probed"] / 2
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "unknown" in r.getMessage()]
    assert len(warnings) == 1 and "rate-limit" in warnings[0].getMessage()
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


async def test_known_probes_do_not_warn(reader, fake_bq, caplog):
    with _client(set(), []) as http:
        out = await select.run_select(reader, fake_bq(), None, http, n=5, run="r", dry_run=True, limiter=NO_WAIT)
    assert out["probe_unknown"] == 0
    assert not any("unknown" in r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)


class FakeGcs:
    def __init__(self) -> None:
        self.buckets: dict[str, FakeBucket] = {}

    def bucket(self, name: str) -> FakeBucket:
        return self.buckets.setdefault(name, FakeBucket())


async def test_run_select_writes_queue_bq_and_coverage(reader, fake_bq, fs):
    bq, gcs, seen = fake_bq(), FakeGcs(), []
    with _client(set(), seen) as http:
        out = await select.run_select(
            reader, bq, fs, http, n=4, run="20261022-120000", dry_run=False, gcs=gcs, limiter=NO_WAIT
        )
    assert out["enqueued"] == 4 and out["picked"] == 4

    col = fs.cols[queue.FETCH_COLLECTION]
    assert set(col) == {pool_id(k) for k in out["songs"]}
    doc = next(iter(col.values()))
    assert doc["source"] == "shared" and doc["priority"] == 1 and doc["spotify_id"].startswith("sp")
    assert doc["duration_ms"] == 200_000 and doc["stratum"].startswith("gender=")
    # The oEmbed verdicts travel with the song so voice.db can skip non-embeddable versions.
    probed = {vid: ok for d in col.values() for vid, ok in d["versions_embeddable"].items()}
    assert probed and set(probed) <= set(seen) and all(ok is True for ok in probed.values())

    ((rows, table, config),) = bq.loads
    assert table == "nomadkaraoke.karaoke_decide.voice_pool_songs"
    assert config.write_disposition == bigquery.WriteDisposition.WRITE_APPEND
    assert {f.name for f in config.schema} == set(rows[0]) == {
        "song_key", "pool_id", "artist", "title", "source", "stratum", "spotify_id", "duration_ms", "video_id",
        "brand_code", "selection_run", "added_at",
    }  # fmt: skip
    assert {r["song_key"] for r in rows} == set(out["songs"])
    assert all(r["selection_run"] == "20261022-120000" and r["video_id"] in seen for r in rows)
    assert all(r["pool_id"] == pool_id(r["song_key"]) for r in rows)

    bucket = gcs.buckets["nomadkaraoke-musicbrainz-data"]
    assert bucket.writes == ["voice/selection/20261022-120000/coverage.json"]
    report = json.loads(bucket.objects["voice/selection/20261022-120000/coverage.json"])
    assert report["picked"] == 4 and report["enqueued"] == 4 and "per_dimension" in report

    # Re-running enqueues nothing new (same songs already queued).
    with _client(set(), []) as http:
        again = await select.run_select(reader, bq, fs, http, n=4, run="r2", dry_run=False, gcs=gcs, limiter=NO_WAIT)
    assert again["enqueued"] == 0


async def test_dry_run_writes_nothing(reader, fake_bq, fs):
    bq, gcs = fake_bq(), FakeGcs()
    out = await select.run_select(reader, bq, fs, None, n=4, run="r", dry_run=True, gcs=gcs, probe=False)
    assert out["picked"] == 4 and out["probed"] == 0 and out["enqueued"] == 0
    assert not fs.cols and not bq.loads and not gcs.buckets


async def test_dedupes_candidates_by_song_key(reader, fake_bq, monkeypatch):
    cands = select.load_candidates(reader, fake_bq(), n=1000)
    monkeypatch.setattr(select, "load_candidates", lambda *a, **k: cands + cands[:10])
    out = await select.run_select(reader, fake_bq(), None, None, n=50, run="r", dry_run=True, probe=False)
    assert out["candidates"] == len(cands)
    assert len(out["songs"]) == len(set(out["songs"]))


def test_without_bigquery_uses_catalog_durations(reader, caplog):
    cands = select.load_candidates(reader, None, n=1000)
    assert cands and all(c.spotify_id is None and not c.has_ultrastar for c in cands)
    assert all(120_000 <= c.duration_ms <= 420_000 for c in cands)
    assert "BigQuery disabled" in caplog.text
