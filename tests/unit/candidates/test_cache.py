"""Tests for the layered TTL cache."""

import time

from karaoke_decide.candidates.cache import CandidateCache


class TestBlobCache:
    def test_roundtrip(self, tmp_path):
        cache = CandidateCache(tmp_path)
        cache.set_blob("tracks", [{"a": 1}])
        assert cache.get_blob("tracks", max_age=None) == [{"a": 1}]

    def test_missing_returns_none(self, tmp_path):
        assert CandidateCache(tmp_path).get_blob("nope", max_age=None) is None

    def test_expired_blob_returns_none(self, tmp_path):
        cache = CandidateCache(tmp_path)
        cache.set_blob("tracks", [1, 2])
        # Backdate the timestamp beyond max_age.
        path = cache._blob_path("tracks")
        import json

        payload = json.loads(path.read_text())
        payload["ts"] = time.time() - 100
        path.write_text(json.dumps(payload))
        assert cache.get_blob("tracks", max_age=10) is None
        assert cache.get_blob("tracks", max_age=1000) == [1, 2]


class TestItemCache:
    def test_roundtrip_forever(self, tmp_path):
        cache = CandidateCache(tmp_path)
        cache.set_item("lrclib", "Pendulum::Slam", {"plain": "x"})
        assert cache.get_item("lrclib", "Pendulum::Slam", max_age=None) == {"plain": "x"}

    def test_empty_sentinel_distinguished_from_missing(self, tmp_path):
        cache = CandidateCache(tmp_path)
        cache.set_item("lrclib", "A::B", {})
        # {} is a real cached value (confirmed-absent), not the None miss signal.
        assert cache.get_item("lrclib", "A::B", max_age=None) == {}
        assert cache.get_item("lrclib", "X::Y", max_age=None) is None

    def test_corrupt_file_returns_none(self, tmp_path):
        cache = CandidateCache(tmp_path)
        path = cache._item_path("lrclib", "A::B")
        path.write_text("{not json")
        assert cache.get_item("lrclib", "A::B", max_age=None) is None
