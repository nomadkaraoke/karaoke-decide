"""Tests for catalog.db publish, gate and pointer handling (no GCP)."""

import gzip
import hashlib
import json
from unittest.mock import MagicMock

from karaoke_decide.catalog import publish as pub
from karaoke_decide.catalog.build import CatalogStats, build_catalog_db
from tests.unit.fake_gcs import FakeBucket


def _stats(songs=200_000, **coverage):
    cov = {"genre": 0.9, "mood": 0.8, "decade": 0.9}
    cov.update(coverage)
    return CatalogStats(songs=songs, artists=10, similar=5, kn=songs, coverage=cov, bytes=1000)


def _prev(songs=200_000, **coverage):
    cov = {"genre": 0.9, "mood": 0.8, "decade": 0.9}
    cov.update(coverage)
    return {"songs": songs, "coverage": cov}


def test_gate_first_build_min_songs():
    assert pub.catalog_gate(_stats(songs=99_999), None)
    assert pub.catalog_gate(_stats(songs=100_000), None) == []
    assert pub.catalog_gate(_stats(songs=5), None, min_songs=5) == []


def test_gate_song_drop():
    assert pub.catalog_gate(_stats(songs=179_000), _prev(songs=200_000))
    assert pub.catalog_gate(_stats(songs=181_000), _prev(songs=200_000)) == []


def test_gate_coverage_drop():
    failures = pub.catalog_gate(_stats(mood=0.65), _prev(mood=0.8))
    assert len(failures) == 1 and "mood" in failures[0]
    assert pub.catalog_gate(_stats(mood=0.75), _prev(mood=0.8)) == []


def _db(tmp_path, make_rows):
    db = tmp_path / "catalog.db"
    stats = build_catalog_db(make_rows(), db, "r")
    return db, stats


def test_pointer_written_last(tmp_path, make_rows):
    db, stats = _db(tmp_path, make_rows)
    bucket = FakeBucket()
    pub.publish_catalog(bucket, db, stats, "20261021-010101")
    assert bucket.writes == ["catalog/20261021-010101/catalog.db.gz", "catalog/latest.json"]


def test_pointer_sha_matches_uncompressed(tmp_path, make_rows):
    db, stats = _db(tmp_path, make_rows)
    bucket = FakeBucket()
    ptr = pub.publish_catalog(bucket, db, stats, "20261021-010101")
    assert json.loads(bucket.objects["catalog/latest.json"]) == ptr
    assert ptr["sha256"] == hashlib.sha256(db.read_bytes()).hexdigest()
    assert ptr["bytes"] == db.stat().st_size
    gz = bucket.objects[ptr["path"]]
    assert ptr["gz_bytes"] == len(gz)
    assert gzip.decompress(gz) == db.read_bytes()
    assert ptr["run"] == "20261021-010101" and ptr["songs"] == stats.songs
    assert ptr["coverage"] == stats.coverage and ptr["schema"] == 1 and ptr["built_at"]


def test_gzip_is_deterministic(tmp_path, make_rows):
    db, stats = _db(tmp_path, make_rows)
    a, b = FakeBucket(), FakeBucket()
    pa = pub.publish_catalog(a, db, stats, "r1")
    pb = pub.publish_catalog(b, db, stats, "r1")
    assert a.objects[pa["path"]] == b.objects[pb["path"]]


def test_prune_keeps_three(tmp_path, make_rows):
    db, stats = _db(tmp_path, make_rows)
    bucket = FakeBucket()
    for run in ("20261001-000000", "20261008-000000", "20261015-000000", "20261022-000000", "20261029-000000"):
        pub.publish_catalog(bucket, db, stats, run)
    assert sorted(bucket.objects) == [
        "catalog/20261015-000000/catalog.db.gz",
        "catalog/20261022-000000/catalog.db.gz",
        "catalog/20261029-000000/catalog.db.gz",
        "catalog/latest.json",
    ]


def _gcs(bucket):
    gcs = MagicMock()
    gcs.bucket.return_value = bucket
    return gcs


def test_build_and_publish_success(tmp_path, make_rows, monkeypatch):
    monkeypatch.setattr(pub, "fetch_catalog_rows", lambda bq: make_rows())
    bucket = FakeBucket()
    ptr = pub.build_and_publish_catalog(MagicMock(), _gcs(bucket), tmp_path, min_songs=1)
    assert ptr is not None
    assert json.loads(bucket.objects["catalog/latest.json"])["run"] == ptr["run"]


def test_gate_failure_keeps_old_pointer(tmp_path, make_rows, monkeypatch, caplog):
    monkeypatch.setattr(pub, "fetch_catalog_rows", lambda bq: make_rows())
    bucket = FakeBucket()
    bucket.objects["catalog/latest.json"] = json.dumps(_prev(songs=1000, mood=0.8)).encode()
    before = dict(bucket.objects)
    with caplog.at_level("WARNING"):
        assert pub.build_and_publish_catalog(MagicMock(), _gcs(bucket), tmp_path, min_songs=1) is None
    assert bucket.objects == before and bucket.writes == []
    gate_logs = [r for r in caplog.records if "catalog gate failed" in r.getMessage()]
    assert len(gate_logs) == 1 and gate_logs[0].levelname == "WARNING"
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


def test_unreadable_previous_pointer_is_treated_as_first_build(tmp_path, make_rows, monkeypatch):
    monkeypatch.setattr(pub, "fetch_catalog_rows", lambda bq: make_rows())
    bucket = FakeBucket()
    bucket.objects["catalog/latest.json"] = b"not json"
    assert pub.build_and_publish_catalog(MagicMock(), _gcs(bucket), tmp_path, min_songs=1) is not None


def test_prune_ignores_non_run_objects(tmp_path, make_rows):
    db, stats = _db(tmp_path, make_rows)
    bucket = FakeBucket()
    bucket.objects["catalog/notes/readme.txt"] = b"x"
    for run in ("20261001-000000", "20261008-000000", "20261015-000000", "20261022-000000"):
        pub.publish_catalog(bucket, db, stats, run)
    assert "catalog/notes/readme.txt" in bucket.objects
    assert "catalog/20261001-000000/catalog.db.gz" not in bucket.objects
