"""The fixtures entry builds the layout the search service's local mode loads."""

from __future__ import annotations

from pathlib import Path

from backend.search import index_loader
from karaoke_decide.song_search import fixtures


def test_build_produces_loadable_index_with_offline_manifest(tmp_path: Path) -> None:
    dest = tmp_path / "idx"
    dest.mkdir()
    (dest / "stale.txt").write_text("x")
    fixtures.build(dest)

    assert not (dest / "stale.txt").exists()
    assert {p.name for p in dest.iterdir()} == {"song_id.db", "availability.db", "offline.db.gz", "manifest.json"}
    paths = index_loader.load_local(dest)
    offline = index_loader.offline_manifest(paths)
    assert offline is not None
    assert offline["bytes"] == (dest / "offline.db.gz").stat().st_size
    loaded = index_loader.open_index(paths)
    try:
        assert loaded.identifier is not None
    finally:
        loaded.identifier.close()
        loaded.availability.close()
