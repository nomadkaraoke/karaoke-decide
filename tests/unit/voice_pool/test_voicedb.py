"""voice.db: build, publish behind ``voice/latest.json``, sources from the fetch queue + catalog, and
the local-stack fixture (invented ranges; fakes only)."""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from karaoke_decide.catalog.keys import song_key
from karaoke_decide.catalog.reader import CatalogReader
from karaoke_decide.vocal_range.notes import VOICE_TYPES
from karaoke_decide.voice_pool import fixtures, queue, voicedb
from karaoke_decide.voice_pool.models import Version, pool_id
from karaoke_decide.voice_pool.versions import VersionStats
from tests.unit.fake_gcs import FakeBucket

NOW = datetime(2026, 10, 23, 4, 5, 6, tzinfo=UTC)


def _analysis(key: str, p50: float = 57.0, **kw: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "song_key": key,
        "pool_id": pool_id(key),
        "analysis_version": 1,
        "analysed_at": NOW.isoformat(),
        "low": p50 - 7, "high": p50 + 8, "p10": p50 - 4, "p50": p50, "p90": p50 + 5, "voiced_s": 80.0,
        "secs_above": json.dumps({"64": 5.0, "67": 1.0, "69": 0.0, "72": 0.0, "76": 0.0}),
        "secs_below": json.dumps({"45": 0.0, "48": 2.0, "52": 9.0}),
        "octave_split_male": False, "octave_split_female": True, "section_jump": 2.5,
        "sustained_high_count": 3, "sustained_high_s": 4.5, "longest_held_s": 2.0, "high_loudness_db": 3.0,
        "syllables_per_s": 2.2, "density_source": "onsets", "longest_phrase_s": 7.0, "intro_s": 12.0,
        "key": 2, "mode": 1, "key_changes": 0, "energy": 0.7, "valence": 0.4, "duration_ms": 200_000,
    }  # fmt: skip
    row.update(kw)
    return row


def _pool(key: str, versions: tuple[Version, ...], **kw: Any) -> dict[str, Any]:
    return {
        "song_key": key, "pool_id": pool_id(key), "artist": "Invented Artist", "title": key.split("|")[1],
        "artist_key": "invented artist", "source": "shared", "stratum": "male|2000s", "spotify_id": "sp1",
        "duration_ms": 200_000, "popularity": 70, "lead_gender": "male", "versions": versions, **kw,
    }  # fmt: skip


def _tables(db: Path) -> dict[str, list[str]]:
    con = sqlite3.connect(db)
    try:
        names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {n: [r[1] for r in con.execute(f"PRAGMA table_info({n})")] for n in names}
    finally:
        con.close()


def test_build_voice_db_tables_and_meta(tmp_path: Path) -> None:
    v1 = Version("vid_aaaaaaa", "NOMAD", "Nomad Karaoke", 0, None)
    v2 = Version("vid_bbbbbbb", "SING", "Sing King", 1, True)
    pool = [_pool("invented|one", (v1, v2)), _pool("invented|two", (), source="personal")]
    stats = {"vid_aaaaaaa": VersionStats(good=3, not_great=1, embeddable=False)}
    out = voicedb.build_voice_db(pool, [_analysis("invented|one"), _analysis("other|x")], stats, tmp_path / "v.db",
                                 "20261023-040506", now=NOW)  # fmt: skip

    assert out == {"run": "20261023-040506", "songs": 2, "analysed": 1, "versions": 2, "built_at": NOW.isoformat()}
    cols = _tables(tmp_path / "v.db")
    assert set(cols) == {"meta", "pool", "versions", "analysis"}
    assert cols["pool"] == ["song_key", "pool_id", "artist", "title", "artist_key", "source", "stratum",
                            "spotify_id", "duration_ms", "popularity", "lead_gender"]  # fmt: skip
    assert cols["versions"] == ["song_key", "video_id", "brand_code", "brand_name", "rank", "embeddable", "quality"]
    assert cols["analysis"] == [
        "song_key", "analysis_version", "low", "high", "p10", "p50", "p90", "voiced_s", "secs_above_json",
        "secs_below_json", "octave_split_male", "octave_split_female", "section_jump", "sustained_high_count",
        "sustained_high_s", "longest_held_s", "high_loudness_db", "syllables_per_s", "density_source",
        "longest_phrase_s", "intro_s", "key", "mode", "key_changes", "energy", "valence",
    ]  # fmt: skip
    con = sqlite3.connect(tmp_path / "v.db")
    meta = dict(con.execute("SELECT key, value FROM meta"))
    assert meta == {"schema": "1", "run": "20261023-040506", "built_at": NOW.isoformat(), "analysis_version": "1"}
    versions = con.execute("SELECT video_id, rank, embeddable, quality FROM versions ORDER BY rank").fetchall()
    assert versions == [("vid_aaaaaaa", 0, 0, pytest.approx(4 / 6)), ("vid_bbbbbbb", 1, 1, 0.5)]
    split = con.execute("SELECT octave_split_male, octave_split_female, secs_above_json FROM analysis").fetchone()
    assert split[:2] == (0, 1) and json.loads(split[2])["64"] == 5.0
    assert con.execute("SELECT source FROM pool WHERE song_key='invented|two'").fetchone() == ("personal",)
    con.close()


def test_pointer_written_last_and_prune_three(tmp_path: Path) -> None:
    db = tmp_path / "voice.db"
    stats = voicedb.build_voice_db([_pool("invented|one", ())], [_analysis("invented|one")], {}, db, "r", now=NOW)
    bucket = FakeBucket()
    old = ["20261019-000000", "20261020-000000", "20261021-000000", "20261022-000000"]
    for r in old:
        bucket.objects[f"voice/{r}/voice.db.gz"] = b"old"
    keep = ["voice/selection/20261001-000000/coverage.json", "voice/notes.txt", "catalog/20261019-000000/catalog.db.gz"]
    for name in keep:
        bucket.objects[name] = b"x"

    pointer = voicedb.publish_voice_db(bucket, db, stats, "20261023-040506")

    gz_name = "voice/20261023-040506/voice.db.gz"
    assert bucket.writes == [gz_name, voicedb.VOICE_LATEST]  # pointer LAST
    raw = bucket.objects[gz_name]
    assert raw[4:8] == b"\x00\x00\x00\x00"  # gzip mtime 0: stable bytes for the same db
    assert gzip.decompress(raw) == db.read_bytes()
    assert pointer == json.loads(bucket.objects[voicedb.VOICE_LATEST])
    assert pointer == {"run": "20261023-040506", "sha256": hashlib.sha256(db.read_bytes()).hexdigest(),
                       "songs": 1, "analysed": 1, "built_at": NOW.isoformat()}  # fmt: skip
    runs = sorted({n.split("/")[1] for n in bucket.objects if n.endswith("voice.db.gz") and n.startswith("voice/")})
    assert runs == ["20261021-000000", "20261022-000000", "20261023-040506"]
    assert all(name in bucket.objects for name in keep)


def test_prune_failure_is_a_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    db = tmp_path / "voice.db"
    stats = voicedb.build_voice_db([_pool("invented|one", ())], [], {}, db, "r", now=NOW)
    bucket = FakeBucket()

    def broken(prefix: str = "", match_glob: str | None = None) -> Any:
        raise RuntimeError("list failed")

    bucket.list_blobs = broken  # type: ignore[method-assign]
    voicedb.publish_voice_db(bucket, db, stats, "20261023-040506")
    assert voicedb.VOICE_LATEST in bucket.objects
    assert [r.levelno for r in caplog.records if r.levelno >= logging.WARNING] == [logging.WARNING]


async def test_publish_from_queue_joins_catalog_versions_and_stats(fs, reader: CatalogReader, tmp_path: Path) -> None:
    con = reader.conn()
    row = con.execute(
        "SELECT s.song_key, s.artist, s.title, s.artist_key, s.popularity, s.lead_gender FROM songs s "
        "WHERE EXISTS (SELECT 1 FROM av.versions v WHERE v.song_key = s.song_key AND v.kind = 'community' "
        "AND v.youtube_url LIKE '%youtu%') ORDER BY s.song_key LIMIT 1"
    ).fetchone()
    key = row["song_key"]
    await queue.enqueue(fs, [
        {"song_key": key, "artist": row["artist"], "title": row["title"], "source": "shared", "stratum": "s1"},
        {"song_key": "nowhere|unknown", "artist": "Nowhere", "title": "Unknown", "source": "personal"},
        {"song_key": "gone|failed", "artist": "Gone", "title": "Failed", "source": "shared"},
    ])  # fmt: skip
    await fs.update_document(queue.FETCH_COLLECTION, pool_id(key), {"status": "analysed", "markers": _analysis(key)})
    await fs.update_document(queue.FETCH_COLLECTION, pool_id("gone|failed"), {"status": "failed"})

    pool, analysis, stats = await voicedb.load_sources(fs, reader)
    by_key = {p["song_key"]: p for p in pool}
    assert set(by_key) == {key, "nowhere|unknown"}  # failed songs never reach voice.db
    assert (by_key[key]["artist_key"], by_key[key]["popularity"]) == (row["artist_key"], row["popularity"])
    assert by_key[key]["versions"] and [v.rank for v in by_key[key]["versions"]] == list(
        range(len(by_key[key]["versions"]))
    )
    assert by_key["nowhere|unknown"]["artist_key"] == "nowhere" and by_key["nowhere|unknown"]["versions"] == ()
    assert [a["song_key"] for a in analysis] == [key]
    assert stats == {}

    vid = by_key[key]["versions"][0].video_id
    await fs.create_document(voicedb.VERSION_STATS_COLLECTION, vid, {"good": 2, "plays": 3, "embeddable": False})
    bucket = FakeBucket()
    pointer = await voicedb.publish_from_queue(fs, bucket, reader, tmp_path, now=NOW)
    assert pointer is not None and pointer["run"] == "20261023-040506"
    assert (pointer["songs"], pointer["analysed"]) == (2, 1)
    db = tmp_path / "voice.db"
    con2 = sqlite3.connect(db)
    assert con2.execute("SELECT embeddable FROM versions WHERE video_id=?", (vid,)).fetchone() == (0,)
    con2.close()


async def test_publish_from_empty_queue_publishes_nothing(fs, reader: CatalogReader, tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.INFO)
    bucket = FakeBucket()
    assert await voicedb.publish_from_queue(fs, bucket, reader, tmp_path, now=NOW) is None
    assert bucket.objects == {}
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_fixture_db_covers_six_types(tmp_path: Path) -> None:
    db = fixtures.build(tmp_path)
    assert db == tmp_path / "voice.db"
    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT a.song_key, a.p50, a.low, a.high FROM analysis a JOIN pool p USING (song_key)"
    ).fetchall()
    assert len(rows) >= 40
    centres = {t: (lo + hi) / 2 for t, (lo, hi) in VOICE_TYPES.items()}

    def nearest(p50: float) -> str:
        return min(centres, key=lambda t: abs(centres[t] - p50))

    counts = {t: sum(nearest(p50) == t for _, p50, _, _ in rows) for t in VOICE_TYPES}
    assert all(n >= 6 for n in counts.values()), counts
    by_key = {k: p50 for k, p50, _, _ in rows}
    assert nearest(by_key[song_key("Muse", "Starlight")]) == "baritone"
    assert all(lo < p50 < hi for _, p50, lo, hi in rows)
    videos = con.execute("SELECT song_key, video_id FROM versions").fetchall()
    assert len(videos) == len(rows) and all(v.startswith("FIXTURE") for _, v in videos)
    assert len({v for _, v in videos}) == len(videos)
    # Every fixture song is a real song of the 3a fixture catalog.
    from karaoke_decide.catalog import fixtures as catalog_fixtures

    (tmp_path / "cat").mkdir()
    cat = sqlite3.connect(catalog_fixtures.build(tmp_path / "cat"))
    known = {r[0] for r in cat.execute("SELECT song_key FROM songs")}
    assert {k for k, *_ in rows} <= known
    assert dict(con.execute("SELECT key, value FROM meta"))["run"] == "fixture"
    cat.close()
    con.close()


def test_fixture_cli(tmp_path: Path, capsys) -> None:
    assert fixtures.main(["fixtures", "build", str(tmp_path)]) == 0
    assert (tmp_path / "voice.db").exists() and str(tmp_path / "voice.db") in capsys.readouterr().out
    assert fixtures.main(["fixtures"]) == 2
