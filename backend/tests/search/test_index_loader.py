"""Index loading: GCS download + sha256 verification, local dir, read-only open."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from backend.search.index_loader import IndexLoadError, load, load_local, open_index

from .conftest import RUN_ID


class FakeBlob:
    def __init__(self, bucket: FakeBucket, name: str) -> None:
        self.bucket = bucket
        self.name = name

    def exists(self) -> bool:
        return self.name in self.bucket.objects

    def download_as_bytes(self) -> bytes:
        if self.name not in self.bucket.objects:
            raise FileNotFoundError(self.name)
        return self.bucket.objects[self.name]

    def download_to_filename(self, filename: str) -> None:
        self.bucket.downloads.append(self.name)
        Path(filename).write_bytes(self.download_as_bytes())


class FakeBucket:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.downloads: list[str] = []

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(self, name)


class FakeClient:
    def __init__(self, bucket: FakeBucket) -> None:
        self._bucket = bucket
        self.bucket_names: list[str] = []

    def bucket(self, name: str) -> FakeBucket:
        self.bucket_names.append(name)
        return self._bucket


@pytest.fixture
def published(index_dir: Path) -> FakeBucket:
    b = FakeBucket()
    for name in ("song_id.db", "availability.db", "offline.db.gz", "manifest.json"):
        b.objects[f"song-index/{RUN_ID}/{name}"] = (index_dir / name).read_bytes()
    b.objects["song-index/latest.json"] = json.dumps({"run": "20261018-000000", "token": "0123456789abcdef"}).encode()
    return b


def test_load_downloads_and_verifies(published: FakeBucket, tmp_path: Path, index_dir: Path) -> None:
    client = FakeClient(published)
    paths = load(RUN_ID, "nomadkaraoke-kn-data", tmp_path, client=client)
    assert client.bucket_names == ["nomadkaraoke-kn-data"]
    assert paths.run == RUN_ID
    assert paths.song_id.read_bytes() == (index_dir / "song_id.db").read_bytes()
    assert paths.availability.read_bytes() == (index_dir / "availability.db").read_bytes()
    assert paths.manifest is not None and paths.manifest["token"] == "0123456789abcdef"
    # The server never downloads the phone's offline file.
    assert sorted(published.downloads) == [f"song-index/{RUN_ID}/availability.db", f"song-index/{RUN_ID}/song_id.db"]
    assert not list(tmp_path.rglob("*.part"))


def test_empty_run_resolves_latest_json(published: FakeBucket, tmp_path: Path) -> None:
    assert load("", "b", tmp_path, client=FakeClient(published)).run == RUN_ID


def test_sha_mismatch_raises_and_leaves_no_file(published: FakeBucket, tmp_path: Path) -> None:
    key = f"song-index/{RUN_ID}/availability.db"
    published.objects[key] = published.objects[key][:-10]  # truncated
    with pytest.raises(IndexLoadError, match="availability.db"):
        load(RUN_ID, "b", tmp_path, client=FakeClient(published))
    assert not list(tmp_path.rglob("availability.db*"))


def test_missing_manifest_raises(published: FakeBucket, tmp_path: Path) -> None:
    del published.objects[f"song-index/{RUN_ID}/manifest.json"]
    with pytest.raises(IndexLoadError, match="manifest"):
        load(RUN_ID, "b", tmp_path, client=FakeClient(published))


def test_manifest_without_file_entry_raises(published: FakeBucket, tmp_path: Path) -> None:
    key = f"song-index/{RUN_ID}/manifest.json"
    m = json.loads(published.objects[key])
    del m["files"]["song_id.db"]
    published.objects[key] = json.dumps(m).encode()
    with pytest.raises(IndexLoadError, match="song_id.db"):
        load(RUN_ID, "b", tmp_path, client=FakeClient(published))


def test_missing_latest_json_raises(published: FakeBucket, tmp_path: Path) -> None:
    del published.objects["song-index/latest.json"]
    with pytest.raises(IndexLoadError, match="latest.json"):
        load("", "b", tmp_path, client=FakeClient(published))


def test_load_local_with_manifest(index_dir: Path) -> None:
    paths = load_local(index_dir)
    assert paths.run == RUN_ID
    assert paths.song_id == index_dir / "song_id.db"
    assert paths.manifest is not None


def test_load_local_without_manifest_uses_meta_run(index_dir: Path, tmp_path: Path) -> None:
    for name in ("song_id.db", "availability.db"):
        shutil.copy(index_dir / name, tmp_path / name)
    paths = load_local(tmp_path)
    assert paths.manifest is None
    assert paths.run == "20261018-000000"


def test_load_local_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(IndexLoadError, match="song_id.db"):
        load_local(tmp_path)


def test_open_index_reads_built_at(index_dir: Path) -> None:
    loaded = open_index(load_local(index_dir))
    try:
        assert loaded.info.run == RUN_ID
        assert loaded.info.built_at > 1_700_000_000
        assert loaded.identifier.identify("starlite muse")["status"] == "confident"
        assert loaded.availability.song("muse|starlight") is not None
    finally:
        loaded.identifier.close()
        loaded.availability.close()


def test_open_index_rejects_corrupt_db(index_dir: Path, tmp_path: Path) -> None:
    shutil.copy(index_dir / "availability.db", tmp_path / "availability.db")
    (tmp_path / "song_id.db").write_bytes(b"garbage" * 100)
    with pytest.raises(IndexLoadError):
        open_index(load_local(tmp_path))


def test_open_index_is_read_only(index_dir: Path) -> None:
    import sqlite3

    loaded = open_index(load_local(index_dir))
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            loaded.availability._conn().execute("DELETE FROM songs")
    finally:
        loaded.availability.close()
