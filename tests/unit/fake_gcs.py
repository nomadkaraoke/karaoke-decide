"""Minimal in-memory stand-in for a google.cloud.storage bucket (tests only)."""

from __future__ import annotations

import io
import re
from typing import Any


class FakeBlob:
    def __init__(self, bucket: FakeBucket, name: str):
        self.bucket = bucket
        self.name = name
        self.metadata: dict[str, str] | None = None

    def exists(self) -> bool:
        return self.name in self.bucket.objects

    def reload(self) -> None:
        self.metadata = self.bucket.metadata.get(self.name)

    def patch(self) -> None:
        self.bucket.metadata[self.name] = dict(self.metadata or {})

    def delete(self) -> None:
        self.bucket.objects.pop(self.name, None)
        self.bucket.metadata.pop(self.name, None)

    def open(self, mode: str, **kwargs: Any) -> io.BytesIO:
        if mode == "rb":
            self.bucket.reads.append(self.name)
            return io.BytesIO(self.bucket.objects[self.name])
        buf = io.BytesIO()
        close = buf.close
        objects = self.bucket.objects
        writes = self.bucket.writes
        name = self.name

        def finish() -> None:
            objects[name] = buf.getvalue()
            writes.append(name)
            close()

        buf.close = finish  # type: ignore[method-assign]
        return buf


class FakeBucket:
    name = "fake-bucket"

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.metadata: dict[str, dict[str, str]] = {}
        self.reads: list[str] = []
        self.writes: list[str] = []  # object names in completed-write order

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(self, name)

    def list_blobs(self, prefix: str = "", match_glob: str | None = None) -> list[FakeBlob]:
        names = [n for n in sorted(self.objects) if n.startswith(prefix)]
        if match_glob is not None:
            # GCS globs: `*` does not cross `/`
            rx = re.compile("^" + "[^/]*".join(re.escape(p) for p in match_glob.split("*")) + "$")
            names = [n for n in names if rx.match(n)]
        return [FakeBlob(self, n) for n in names]
