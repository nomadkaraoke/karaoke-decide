"""Layered, TTL-aware JSON cache for the candidate pipeline.

Two shapes:
- **Blob cache** (`get_blob`/`set_blob`): one JSON file per logical dataset
  (Last.fm top tracks, KaraokeNerds dump), with an embedded timestamp so the
  caller can apply a max-age.
- **Item cache** (`get_item`/`set_item`): one small JSON file per (namespace,
  key) — used for per-song lookups (LRCLIB lyrics, flacfetch sourceability,
  Last.fm tags). ``max_age`` of ``None`` means "cache forever".

Nothing here knows about TTLs by policy — callers pass ``max_age`` so the
policy (LRCLIB ∞, flacfetch ~30d, KaraokeNerds ~30d) lives with the generator.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    return _SLUG_RE.sub("-", text.lower()).strip("-")[:150] or "_"


class CandidateCache:
    """Filesystem JSON cache rooted at ``base_dir/cache``."""

    def __init__(self, base_dir: Path):
        self.root = Path(base_dir) / "cache"
        self.root.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- blobs
    def _blob_path(self, name: str) -> Path:
        return self.root / f"{name}.json"

    def get_blob(self, name: str, max_age: float | None) -> Any | None:
        """Return the cached value if present and within ``max_age`` seconds."""
        path = self._blob_path(name)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        if max_age is not None and (time.time() - payload.get("ts", 0)) > max_age:
            return None
        return payload.get("value")

    def set_blob(self, name: str, value: Any) -> None:
        self._blob_path(name).write_text(json.dumps({"ts": time.time(), "value": value}))

    # ---------------------------------------------------------------- items
    def _item_path(self, namespace: str, key: str) -> Path:
        ns_dir = self.root / namespace
        ns_dir.mkdir(parents=True, exist_ok=True)
        return ns_dir / f"{_slug(key)}.json"

    def get_item(self, namespace: str, key: str, max_age: float | None) -> Any | None:
        path = self._item_path(namespace, key)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        if max_age is not None and (time.time() - payload.get("ts", 0)) > max_age:
            return None
        return payload.get("value")

    def set_item(self, namespace: str, key: str, value: Any) -> None:
        self._item_path(namespace, key).write_text(json.dumps({"ts": time.time(), "value": value}))
