"""Private voice recordings: consent-gated upload, delete all, count (Task C4).

Fake bucket (the GCS blob API), in-memory Firestore, synthetic bytes only.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_current_user, get_firestore, get_settings
from backend.api.routes.admin_voice import get_recordings_bucket
from backend.config import BackendSettings
from backend.main import app
from backend.services import voice_recording_service as vrs
from backend.services.voice_recording_service import LocalBucket, VoiceRecordingService
from backend.tests.test_karaoke_log_routes import RouteFirestore
from karaoke_decide.core.models import User

USER = "user_rec_a"
OTHER = "user_rec_b"
SIMILAR = "user_rec_a0"  # shares a textual prefix with USER
AUDIO = b"\x00synthetic-tone" * 1000
ADMIN_TOKEN = "script-token-1"


class FakeBlob:
    def __init__(self, bucket: FakeBucket, name: str) -> None:
        self.bucket, self.name = bucket, name
        self.metadata: dict[str, str] | None = None

    def upload_from_file(self, f: Any, content_type: str | None = None, **_: Any) -> None:
        self.bucket.objects[self.name] = f.read()
        self.bucket.meta[self.name] = {"content_type": content_type, **(self.metadata or {})}

    def open(self, mode: str = "rb", **_: Any) -> io.BytesIO:
        if self.name not in self.bucket.objects:
            raise FileNotFoundError(self.name)
        return io.BytesIO(self.bucket.objects[self.name])

    def delete(self) -> None:
        if self.name not in self.bucket.objects:
            raise FileNotFoundError(self.name)
        del self.bucket.objects[self.name]


class FakeBucket:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.meta: dict[str, dict[str, Any]] = {}

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(self, name)

    def list_blobs(self, prefix: str = "") -> Iterator[FakeBlob]:
        return iter([FakeBlob(self, n) for n in sorted(self.objects) if n.startswith(prefix)])


@pytest.fixture
def fs() -> RouteFirestore:
    return RouteFirestore()


@pytest.fixture
def bucket() -> FakeBucket:
    return FakeBucket()


MakeClient = Callable[..., TestClient]


@pytest.fixture
def make_client(fs: RouteFirestore, bucket: FakeBucket) -> Iterator[MakeClient]:
    def make(*, user_id: str = USER, admin: bool = True, open_: bool = False) -> TestClient:
        settings = BackendSettings(
            environment="development",
            google_cloud_project="test-project",
            voice_session_open=open_,
            admin_tokens=[ADMIN_TOKEN],
        )
        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[get_firestore] = lambda: fs
        app.dependency_overrides[get_current_user] = lambda: User(id=user_id, email="x@example.com", is_admin=admin)
        app.dependency_overrides[get_recordings_bucket] = lambda: bucket
        return TestClient(app)

    yield make
    app.dependency_overrides.clear()


def _seed(fs: RouteFirestore, user_id: str = USER, sid: str = "s1", record: bool = True, attempts: int = 2) -> str:
    fs.cols.setdefault("voice_sessions", {})[sid] = {
        "user_id": user_id,
        "state": "active",
        "setup": {"posture": "standing", "warmth": "warm", "record": record, "headphones": True, "device": "Pixel"},
    }
    for n in range(1, attempts + 1):
        fs.cols.setdefault("voice_attempts", {})[f"{sid}:{n}"] = {
            "user_id": user_id,
            "session_id": sid,
            "n": n,
            "song_key": "artist|title",
            "video_id": "vid1",
            "context": {"posture": "standing", "warmth": "warm", "headphones": True, "device": "Pixel"},
            "recording_id": None,
        }
    return sid


def _post(
    c: TestClient,
    sid: str = "s1",
    n: int = 1,
    data: bytes = AUDIO,
    ctype: str = "audio/mp4",
    **extra: Any,
) -> Any:
    return c.post(
        "/api/voice/recordings",
        data={"session_id": sid, "attempt_n": str(n), "duration_s": "12.5", **extra},
        files={"file": ("rec", data, ctype)},
    )


def test_upload_rejected_without_consent(make_client: MakeClient, fs: RouteFirestore, bucket: FakeBucket) -> None:
    _seed(fs, record=False)
    r = _post(make_client())
    assert r.status_code == 403 and r.json() == {"detail": "recording_not_consented"}
    assert bucket.objects == {} and "voice_recordings" not in fs.cols


def test_consent_is_current_setup_record(make_client: MakeClient, fs: RouteFirestore, bucket: FakeBucket) -> None:
    _seed(fs, record=True)
    c = make_client()
    assert _post(c).status_code == 201
    fs.cols["voice_sessions"]["s1"]["setup"]["record"] = False  # the chip was turned off mid-session
    assert _post(c, n=2).status_code == 403
    assert len(bucket.objects) == 1


def test_upload_wav_and_m4a_paths(make_client: MakeClient, fs: RouteFirestore, bucket: FakeBucket) -> None:
    _seed(fs)
    c = make_client()
    r1 = _post(c, n=1, ctype="audio/mp4", device="Pixel 9")
    r2 = _post(c, n=2, ctype="audio/wav")
    assert r1.status_code == 201 and r1.json() == {"recording_id": "s1:1"}
    assert r2.status_code == 201
    assert sorted(bucket.objects) == [f"voice-recordings/{USER}/s1/1.m4a", f"voice-recordings/{USER}/s1/2.wav"]
    doc = fs.cols["voice_recordings"]["s1:1"]
    assert doc["gcs_path"] == f"voice-recordings/{USER}/s1/1.m4a"
    assert (doc["user_id"], doc["session_id"], doc["attempt_n"]) == (USER, "s1", 1)
    assert (doc["content_type"], doc["bytes"], doc["duration_s"]) == ("audio/mp4", len(AUDIO), 12.5)
    assert "created_at" in doc
    assert fs.cols["voice_attempts"]["s1:1"]["recording_id"] == "s1:1"
    meta = bucket.meta[f"voice-recordings/{USER}/s1/1.m4a"]
    assert meta == {
        "content_type": "audio/mp4",
        "song_key": "artist|title",
        "video_id": "vid1",
        "posture": "standing",
        "warmth": "warm",
        "headphones": "True",
        "device": "Pixel 9",
    }


def test_content_type_parameters_are_ignored(make_client: MakeClient, fs: RouteFirestore) -> None:
    _seed(fs)
    assert _post(make_client(), ctype="audio/mp4; codecs=mp4a.40.2").status_code == 201


def test_bad_type_415(make_client: MakeClient, fs: RouteFirestore, bucket: FakeBucket) -> None:
    _seed(fs)
    for ctype in ("audio/mpeg", "video/mp4", "application/octet-stream"):
        assert _post(make_client(), ctype=ctype).status_code == 415
    assert bucket.objects == {}


def test_upload_too_large_413(
    make_client: MakeClient, fs: RouteFirestore, bucket: FakeBucket, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(fs)
    monkeypatch.setattr(vrs, "MAX_BYTES", 10_000)
    c = make_client()
    r = _post(c, data=b"x" * 10_001)
    assert r.status_code == 413 and r.json() == {"detail": "recording_too_large"}
    assert bucket.objects == {} and "voice_recordings" not in fs.cols
    assert _post(c, data=b"x" * 10_000).status_code == 201


def test_declared_oversize_413_before_reading(make_client: MakeClient, fs: RouteFirestore) -> None:
    _seed(fs)
    big = vrs.MAX_BYTES + 100_000
    r = make_client().post(
        "/api/voice/recordings",
        content=b"",
        headers={"content-length": str(big), "content-type": "multipart/form-data; boundary=x"},
    )
    assert r.status_code == 413


def test_chunked_oversize_413_without_content_length(
    make_client: MakeClient, fs: RouteFirestore, bucket: FakeBucket, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(fs)
    monkeypatch.setattr(vrs, "MAX_BYTES", 10_000)
    monkeypatch.setattr("backend.api.routes.voice_session.MULTIPART_OVERHEAD", 1_000)

    def body() -> Iterator[bytes]:  # a generator body is sent chunked: no content-length to trust
        yield b'--b\r\nContent-Disposition: form-data; name="file"; filename="r"\r\nContent-Type: audio/mp4\r\n\r\n'
        for _ in range(30):
            yield b"x" * 1000
        yield b"\r\n--b--\r\n"

    r = make_client().post(
        "/api/voice/recordings", content=body(), headers={"content-type": "multipart/form-data; boundary=b"}
    )
    assert r.status_code == 413 and bucket.objects == {}


def test_empty_file_422(make_client: MakeClient, fs: RouteFirestore) -> None:
    _seed(fs)
    assert _post(make_client(), data=b"").status_code == 422


def test_reupload_replaces(make_client: MakeClient, fs: RouteFirestore, bucket: FakeBucket) -> None:
    _seed(fs)
    c = make_client()
    assert _post(c, data=b"first").status_code == 201
    assert _post(c, data=b"second-take").status_code == 201
    assert bucket.objects == {f"voice-recordings/{USER}/s1/1.m4a": b"second-take"}
    assert len(fs.cols["voice_recordings"]) == 1 and fs.cols["voice_recordings"]["s1:1"]["bytes"] == 11
    # a re-upload in the other format removes the old object
    assert _post(c, data=b"third", ctype="audio/wav").status_code == 201
    assert list(bucket.objects) == [f"voice-recordings/{USER}/s1/1.wav"]
    assert fs.cols["voice_recordings"]["s1:1"]["gcs_path"].endswith("1.wav")


def test_other_users_session_403(make_client: MakeClient, fs: RouteFirestore, bucket: FakeBucket) -> None:
    _seed(fs, user_id=OTHER)
    r = _post(make_client(user_id=USER))
    assert r.status_code == 403 and r.json() == {"detail": "session_forbidden"}
    assert _post(make_client(), sid="nope").status_code == 403
    assert bucket.objects == {}


def test_unknown_attempt_404_and_other_users_attempt(make_client: MakeClient, fs: RouteFirestore) -> None:
    _seed(fs, attempts=1)
    c = make_client()
    assert _post(c, n=2).status_code == 404
    fs.cols["voice_attempts"]["s1:1"]["user_id"] = OTHER
    assert _post(c, n=1).status_code == 404


def test_form_validation_rejects_unknown_and_bad_fields(make_client: MakeClient, fs: RouteFirestore) -> None:
    _seed(fs)
    c = make_client()
    assert _post(c, pitch="1,2,3").status_code == 422  # unknown form field
    assert _post(c, n=0).status_code == 422
    assert _post(c, n=13).status_code == 422
    assert _post(c, duration_s="-1").status_code == 422
    assert c.post("/api/voice/recordings", data={"session_id": "s1"}).status_code == 422


def test_upload_gated_like_sessions_but_delete_and_count_are_not(
    make_client: MakeClient, fs: RouteFirestore, bucket: FakeBucket
) -> None:
    _seed(fs)
    assert _post(make_client(admin=True)).status_code == 201
    c = make_client(admin=False)  # flag off for this user
    r = _post(c, n=2)
    assert r.status_code == 403 and r.json() == {"detail": "voice_session_disabled"}
    assert c.get("/api/voice/recordings/count").json() == {"count": 1}
    assert c.delete("/api/voice/recordings").json() == {"deleted": 1}
    assert bucket.objects == {}


def test_count(make_client: MakeClient, fs: RouteFirestore) -> None:
    _seed(fs)
    _seed(fs, user_id=OTHER, sid="s2")
    c = make_client()
    assert c.get("/api/voice/recordings/count").json() == {"count": 0}
    _post(c, n=1)
    _post(c, n=2)
    make_client(user_id=OTHER).post(
        "/api/voice/recordings",
        data={"session_id": "s2", "attempt_n": "1", "duration_s": "3"},
        files={"file": ("r", AUDIO, "audio/wav")},
    )
    assert make_client().get("/api/voice/recordings/count").json() == {"count": 2}


def test_delete_all_removes_objects_docs_and_links(
    make_client: MakeClient, fs: RouteFirestore, bucket: FakeBucket
) -> None:
    _seed(fs)
    c = make_client()
    _post(c, n=1)
    _post(c, n=2, ctype="audio/wav")
    assert c.delete("/api/voice/recordings").json() == {"deleted": 2}
    assert bucket.objects == {}
    assert fs.cols["voice_recordings"] == {}
    assert all(a["recording_id"] is None for a in fs.cols["voice_attempts"].values())
    assert c.delete("/api/voice/recordings").json() == {"deleted": 0}  # idempotent
    assert c.get("/api/voice/recordings/count").json() == {"count": 0}


@pytest.mark.asyncio
async def test_delete_all_for_user_never_touches_other_prefixes(fs: RouteFirestore, bucket: FakeBucket) -> None:
    for uid in (USER, OTHER, SIMILAR):
        _seed(fs, user_id=uid, sid=f"sess-{uid}", attempts=1)
    svc = VoiceRecordingService(fs, bucket)  # type: ignore[arg-type]
    for uid in (USER, OTHER, SIMILAR):
        sid = f"sess-{uid}"
        await svc.upload(
            uid,
            session_id=sid,
            attempt_n=1,
            duration_s=1.0,
            device=None,
            content_type="audio/mp4",
            file=io.BytesIO(AUDIO),
        )
    assert await svc.delete_all_for_user(USER) == 1
    assert sorted(bucket.objects) == sorted(f"voice-recordings/{u}/sess-{u}/1.m4a" for u in (OTHER, SIMILAR))
    assert sorted(fs.cols["voice_recordings"]) == sorted([f"sess-{OTHER}:1", f"sess-{SIMILAR}:1"])
    assert fs.cols["voice_attempts"][f"sess-{OTHER}:1"]["recording_id"] == f"sess-{OTHER}:1"
    assert fs.cols["voice_attempts"][f"sess-{USER}:1"]["recording_id"] is None
    assert await svc.delete_all_for_user(USER) == 0


@pytest.mark.asyncio
async def test_delete_all_removes_orphaned_objects_and_docs(fs: RouteFirestore, bucket: FakeBucket) -> None:
    bucket.objects[f"voice-recordings/{USER}/old/1.m4a"] = b"x"  # object without a doc
    fs.cols["voice_recordings"] = {"d": {"user_id": USER, "gcs_path": f"voice-recordings/{USER}/gone/1.m4a"}}
    svc = VoiceRecordingService(fs, bucket)  # type: ignore[arg-type]
    assert await svc.delete_all_for_user(USER) == 2
    assert bucket.objects == {} and fs.cols["voice_recordings"] == {}


@pytest.mark.asyncio
async def test_delete_all_rejects_unsafe_user_ids(fs: RouteFirestore, bucket: FakeBucket) -> None:
    svc = VoiceRecordingService(fs, bucket)  # type: ignore[arg-type]
    for bad in ("", "a/b", ".."):
        with pytest.raises(ValueError):
            await svc.delete_all_for_user(bad)


def test_uploaded_recording_streams_through_admin_endpoint(
    make_client: MakeClient, fs: RouteFirestore, bucket: FakeBucket
) -> None:
    _seed(fs)
    c = make_client()
    rid = _post(c, ctype="audio/wav").json()["recording_id"]
    res = c.get(f"/api/admin/voice/recordings/{rid}/audio", headers={"X-Admin-Token": ADMIN_TOKEN})
    assert res.status_code == 200 and res.content == AUDIO
    assert res.headers["content-type"] == "audio/wav"


def test_local_dir_storage_roundtrip(tmp_path: Path, fs: RouteFirestore) -> None:
    settings = BackendSettings(
        environment="development", google_cloud_project="t", voice_recordings_local_dir=str(tmp_path / "rec")
    )
    bucket = get_recordings_bucket(settings)
    assert isinstance(bucket, LocalBucket)
    app.dependency_overrides[get_recordings_bucket] = lambda: bucket
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_firestore] = lambda: fs
    app.dependency_overrides[get_current_user] = lambda: User(id=USER, email="x@example.com", is_admin=True)
    try:
        _seed(fs)
        c = TestClient(app)
        assert _post(c).status_code == 201
        assert (tmp_path / "rec" / "voice-recordings" / USER / "s1" / "1.m4a").read_bytes() == AUDIO
        assert c.delete("/api/voice/recordings").json() == {"deleted": 1}
        assert list((tmp_path / "rec").rglob("*.m4a")) == []
    finally:
        app.dependency_overrides.clear()


def test_local_bucket_refuses_escaping_names(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        LocalBucket(tmp_path).path_for("../outside")


class RacingFirestore(RouteFirestore):
    """Runs a hook just before the first ``voice_recordings`` write (the upload is mid-flight)."""

    hook: Callable[[], Any] | None = None

    async def set_document(self, collection: str, doc_id: str, data: dict[str, Any], merge: bool = False) -> None:
        if collection == "voice_recordings" and self.hook is not None:
            hook, self.hook = self.hook, None
            await hook()
        await super().set_document(collection, doc_id, data, merge=merge)


@pytest.mark.asyncio
async def test_upload_racing_delete_all_undoes_itself(bucket: FakeBucket) -> None:
    rfs = RacingFirestore()
    _seed(rfs)
    svc = VoiceRecordingService(rfs, bucket)  # type: ignore[arg-type]
    rfs.hook = lambda: svc.delete_all_for_user(USER)  # between the object put and the doc write
    with pytest.raises(vrs.RecordingsDeletedError) as exc:
        await svc.upload(
            USER,
            session_id="s1",
            attempt_n=1,
            duration_s=1.0,
            device=None,
            content_type="audio/mp4",
            file=io.BytesIO(AUDIO),
        )
    assert exc.value.status_code == 409 and exc.value.detail == "recordings_deleted"
    assert bucket.objects == {}
    assert rfs.cols["voice_recordings"] == {}
    assert rfs.cols["voice_attempts"]["s1:1"]["recording_id"] is None
    # an upload queued before the delete and sent after it is refused: delete-all withdrew consent
    with pytest.raises(vrs.RecordingNotConsentedError):
        await svc.upload(
            USER,
            session_id="s1",
            attempt_n=1,
            duration_s=1.0,
            device=None,
            content_type="audio/mp4",
            file=io.BytesIO(AUDIO),
        )
    assert bucket.objects == {}
    assert rfs.cols["voice_sessions"]["s1"]["setup"]["record"] is False


def test_delete_all_withdraws_consent_until_patched(
    make_client: MakeClient, fs: RouteFirestore, bucket: FakeBucket
) -> None:
    """A queued pre-delete upload can't undo "Delete my recordings"; re-consenting with PATCH re-enables uploads."""
    _seed(fs)
    _seed(fs, sid="s2")
    _seed(fs, user_id=OTHER, sid="s3")
    c = make_client()
    assert _post(c, n=1).status_code == 201
    assert c.delete("/api/voice/recordings").json() == {"deleted": 1}
    sessions = fs.cols["voice_sessions"]
    assert sessions["s1"]["setup"]["record"] is False and sessions["s2"]["setup"]["record"] is False
    assert sessions["s1"]["setup"]["posture"] == "standing"  # the rest of the setup is kept
    assert sessions["s3"]["setup"]["record"] is True  # other users untouched
    r = _post(c, n=2)  # the app's queued upload, sent after the delete
    assert r.status_code == 403 and r.json() == {"detail": "recording_not_consented"}
    assert bucket.objects == {} and fs.cols["voice_recordings"] == {}
    assert c.patch("/api/voice/sessions/s1", json={"record": True}).status_code == 204
    assert _post(c, n=2).status_code == 201


@pytest.mark.asyncio
async def test_race_undo_failure_still_409_and_logs_warning(
    bucket: FakeBucket, caplog: pytest.LogCaptureFixture
) -> None:
    rfs = RacingFirestore()
    _seed(rfs)
    svc = VoiceRecordingService(rfs, bucket)  # type: ignore[arg-type]

    async def purge_then_break() -> None:
        await svc.delete_all_for_user(USER)

        def broken(name: str) -> None:
            raise RuntimeError("transient")

        svc._delete_blob = broken  # type: ignore[method-assign]

    rfs.hook = purge_then_break
    with caplog.at_level("WARNING"), pytest.raises(vrs.RecordingsDeletedError) as exc:
        await svc.upload(
            USER,
            session_id="s1",
            attempt_n=1,
            duration_s=1.0,
            device=None,
            content_type="audio/mp4",
            file=io.BytesIO(AUDIO),
        )
    assert exc.value.status_code == 409 and exc.value.detail == "recordings_deleted"
    assert any(r.levelname == "WARNING" and "undo" in r.getMessage() for r in caplog.records)


def test_race_undo_failure_maps_to_409_not_500(
    make_client: MakeClient, fs: RouteFirestore, bucket: FakeBucket, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(fs)
    fs.cols["voice_recording_purges"] = {USER: {"purged_at": datetime(2999, 1, 1, tzinfo=UTC)}}

    async def boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("firestore down")

    monkeypatch.setattr(fs, "delete_document", boom)
    r = _post(make_client())
    assert r.status_code == 409 and r.json() == {"detail": "recordings_deleted"}


def test_race_maps_to_409(make_client: MakeClient, fs: RouteFirestore, bucket: FakeBucket) -> None:
    _seed(fs)
    fs.cols["voice_recording_purges"] = {USER: {"purged_at": datetime(2999, 1, 1, tzinfo=UTC)}}
    r = _post(make_client())
    assert r.status_code == 409 and r.json() == {"detail": "recordings_deleted"}
    assert bucket.objects == {}


def test_replaced_object_cleanup_failure_does_not_fail_upload(
    make_client: MakeClient, fs: RouteFirestore, bucket: FakeBucket, caplog: pytest.LogCaptureFixture
) -> None:
    _seed(fs)
    c = make_client()
    assert _post(c, ctype="audio/mp4").status_code == 201
    old = f"voice-recordings/{USER}/s1/1.m4a"
    real_delete = FakeBlob.delete

    def flaky(self: FakeBlob) -> None:
        if self.name == old:
            raise RuntimeError("transient")
        real_delete(self)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(FakeBlob, "delete", flaky)
        with caplog.at_level("WARNING"):
            assert _post(c, ctype="audio/wav").status_code == 201
    assert fs.cols["voice_recordings"]["s1:1"]["gcs_path"].endswith("1.wav")
    assert any(r.levelname == "WARNING" for r in caplog.records)
    assert not any(r.levelname == "ERROR" for r in caplog.records)
    assert c.delete("/api/voice/recordings").json() == {"deleted": 2}  # orphan swept by delete-all
    assert bucket.objects == {}


@pytest.mark.asyncio
async def test_upload_undoes_itself_when_consent_withdrawn_mid_flight(bucket: FakeBucket) -> None:
    """Delete-all's purge marker can predate the upload's start; withdrawn consent alone must trigger the undo."""
    rfs = RacingFirestore()
    _seed(rfs)
    svc = VoiceRecordingService(rfs, bucket)  # type: ignore[arg-type]

    async def withdraw() -> None:
        await rfs.update_document("voice_sessions", "s1", {"setup": {"record": False}})

    rfs.hook = withdraw  # no purge marker is written at all
    with pytest.raises(vrs.RecordingsDeletedError):
        await svc.upload(
            USER,
            session_id="s1",
            attempt_n=1,
            duration_s=1.0,
            device=None,
            content_type="audio/mp4",
            file=io.BytesIO(AUDIO),
        )
    assert bucket.objects == {}
    assert rfs.cols["voice_recordings"] == {}
