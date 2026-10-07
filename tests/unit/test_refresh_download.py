"""Resumable dump download + GCS archive cache (refresh_common)."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from unittest.mock import MagicMock

import httpx
import pytest

from karaoke_decide.etl import musicbrainz_refresh as mr
from karaoke_decide.etl import refresh_common as common
from karaoke_decide.etl.refresh_common import RefreshError
from tests.unit.fake_gcs import FakeBucket

URL = "https://data.example.test/dump.tar.bz2"
BODY = bytes(range(256)) * 400  # 100 KiB
ETAG = '"abc-123"'


class Origin:
    """Fake data.metabrainz.org: honours Range/If-Range; can stall mid-body.

    ``plan`` is consumed one entry per request: an int N = send N bytes of the
    requested range then raise ReadTimeout; "ok" = send the rest; an HTTP
    status int >= 400 written as ("status", code) = fail with that status.
    """

    def __init__(self, plan: list, etag: str = ETAG, body: bytes = BODY):
        self.plan = list(plan)
        self.etag = etag
        self.body = body
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self.plan.pop(0) if self.plan else "ok"
        if isinstance(step, tuple):
            return httpx.Response(step[1])
        start = 0
        status = 200
        headers = {"etag": self.etag, "accept-ranges": "bytes"}
        rng = request.headers.get("range")
        if rng and request.headers.get("if-range", self.etag) == self.etag:
            start = int(re.match(r"bytes=(\d+)-", rng).group(1))
            status = 206
            headers["content-range"] = f"bytes {start}-{len(self.body) - 1}/{len(self.body)}"
        payload = self.body[start:]
        headers["content-length"] = str(len(payload))

        def stream() -> Iterator[bytes]:
            if step == "ok":
                yield payload
                return
            yield payload[:step]
            raise httpx.ReadTimeout("stalled")

        return httpx.Response(status, headers=headers, content=stream())


def _download(origin: Origin, **kw) -> tuple[bytes, list[float]]:
    sleeps: list[float] = []
    http = httpx.Client(transport=httpx.MockTransport(origin))
    data = b"".join(common.resumable_chunks(http, URL, chunk_size=4096, sleep=sleeps.append, **kw))
    return data, sleeps


class TestResumableChunks:
    def test_clean_download_single_request(self):
        origin = Origin(["ok"])
        data, sleeps = _download(origin)
        assert data == BODY
        assert len(origin.requests) == 1 and sleeps == []
        assert "range" not in origin.requests[0].headers

    def test_stall_resumes_from_last_byte_with_if_range(self):
        origin = Origin([30_000, 50_000, "ok"])
        data, sleeps = _download(origin, backoff_s=1)
        assert data == BODY  # nothing duplicated or lost across resumes
        # Resumes start at the bytes actually handed to us (whole 4 KiB chunks); bytes
        # still buffered inside httpx when the stall hit are simply re-requested.
        first = 30_000 // 4096 * 4096
        second = first + 50_000 // 4096 * 4096
        assert [r.headers.get("range") for r in origin.requests] == [None, f"bytes={first}-", f"bytes={second}-"]
        assert all(r.headers["if-range"] == ETAG for r in origin.requests[1:])
        assert sleeps == [1, 2]  # linear backoff

    def test_changed_file_is_never_spliced(self):
        origin = Origin([30_000, "ok"])
        origin_etag_changes = origin.__call__

        def replaced(request):
            if request.headers.get("range"):
                origin.etag = '"new-file"'  # If-Range mismatch -> server sends a full 200
            return origin_etag_changes(request)

        http = httpx.Client(transport=httpx.MockTransport(replaced))
        with pytest.raises(RefreshError, match="file changed or Range unsupported"):
            b"".join(common.resumable_chunks(http, URL, chunk_size=4096, sleep=lambda s: None))

    def test_server_errors_are_retried(self):
        origin = Origin([("status", 503), ("status", 429), "ok"])
        data, sleeps = _download(origin, backoff_s=1)
        assert data == BODY and len(sleeps) == 2

    def test_client_errors_fail_immediately(self):
        origin = Origin([("status", 404)])
        with pytest.raises(httpx.HTTPStatusError):
            _download(origin)
        assert len(origin.requests) == 1

    def test_gives_up_after_max_resumes(self):
        origin = Origin([1000] * 10)
        with pytest.raises(RefreshError, match="after 3 resumes"):
            _download(origin, max_resumes=3)
        assert len(origin.requests) == 4

    def test_short_body_without_error_is_resumed(self):
        # Connection closed cleanly but early: content-length tells us we're short.
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(
                    200, headers={"etag": ETAG, "content-length": str(len(BODY))}, content=iter([BODY[:10_000]])
                )
            return Origin(["ok"])(request)

        http = httpx.Client(transport=httpx.MockTransport(handler))
        data = b"".join(common.resumable_chunks(http, URL, sleep=lambda s: None))
        assert data == BODY and calls["n"] == 2


class TestCacheArchive:
    SHA = hashlib.sha256(BODY).hexdigest()

    def test_downloads_verifies_and_records_sha(self):
        bucket = FakeBucket()
        http = httpx.Client(transport=httpx.MockTransport(Origin([40_000, "ok"])))
        common.cache_archive(http, bucket, URL, "staging/archives/d/x.tar.bz2", self.SHA, sleep=lambda s: None)
        assert bucket.objects["staging/archives/d/x.tar.bz2"] == BODY
        assert bucket.metadata["staging/archives/d/x.tar.bz2"]["sha256"] == self.SHA

    def test_verified_copy_is_reused_without_contacting_origin(self):
        bucket = FakeBucket()
        bucket.objects["a"] = BODY
        bucket.metadata["a"] = {"sha256": self.SHA}
        http = MagicMock()
        common.cache_archive(http, bucket, URL, "a", self.SHA.upper())
        http.stream.assert_not_called()

    def test_unverified_partial_copy_is_replaced(self):
        bucket = FakeBucket()
        bucket.objects["a"] = b"partial"  # no sha metadata: e.g. job died before patch
        origin = Origin(["ok"])
        common.cache_archive(httpx.Client(transport=httpx.MockTransport(origin)), bucket, URL, "a", self.SHA)
        assert bucket.objects["a"] == BODY and len(origin.requests) == 1

    def test_checksum_mismatch_deletes_copy(self):
        bucket = FakeBucket()
        http = httpx.Client(transport=httpx.MockTransport(Origin(["ok"])))
        with pytest.raises(RefreshError, match="SHA256 mismatch"):
            common.cache_archive(http, bucket, URL, "a", "0" * 64)
        assert "a" not in bucket.objects


class TestMusicBrainzUsesCache:
    def test_archives_cached_outside_dump_staging_prefix(self):
        # cleanup_staging / staged_tables only look under staging/<dump_id>/.
        assert not mr.archive_blob_name("20261007-002147", "mbdump.tar.bz2").startswith("staging/20261007-002147/")

    def test_rerun_extracts_from_cache_without_downloading(self, monkeypatch):
        bucket = FakeBucket()
        gcs = MagicMock()
        gcs.bucket.return_value = bucket
        for archive in mr.ARCHIVES:
            name = mr.archive_blob_name("d", archive)
            bucket.objects[name] = b"archive bytes"
            bucket.metadata[name] = {"sha256": "ab" * 32}
        http = MagicMock()
        http.get.return_value = MagicMock(text="\n".join(f"{'ab' * 32}  {a}" for a in mr.ARCHIVES))
        extracted = []
        monkeypatch.setattr(mr, "extract_members", lambda chunks, *a, **k: extracted.append(b"".join(chunks)) or {})
        mr.extract_dump_to_gcs(http, gcs, mr.RunState(dump_id="d"))
        http.stream.assert_not_called()
        assert extracted == [b"archive bytes", b"archive bytes"]
