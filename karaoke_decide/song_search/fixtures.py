"""Build the small fixture index the search local stack serves (Task A8).

    python -m karaoke_decide.song_search.fixtures build <dir>

Writes ``song_id.db``, ``availability.db``, ``offline.db.gz`` and ``manifest.json`` into
``<dir>`` (recreated), exactly the layout ``backend/search/index_loader.load_local`` expects.
It reuses the song-index-build job's local dry run, so the fixture index is built the
same way as a real one (and passes the same eval gates). Its ``offline.db`` carries the invented
vocal ranges of ``tests/fixtures/vocal_range/*_fixture.tsv`` (``meta.vocal_range_run = "fixture"``).
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from karaoke_decide.etl import song_index_build

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "song_search"
FIXTURE_RUN = "local"


def build(dest: Path) -> dict[str, object]:
    """Recreate ``dest`` and build the fixture index into it. Raises if the eval gates fail."""
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    out = song_index_build.dry_run(
        dest,
        [FIXTURES_DIR / "songs_fixture.tsv"],
        FIXTURES_DIR / "kn_full_fixture.json",
        FIXTURES_DIR / "kn_community_fixture.json",
        FIXTURE_RUN,
        song_index_build.fixture_vocal_range(),  # vr_* for the mobile flows' fit badges
    )
    if not out.get("published"):
        raise RuntimeError(f"fixture index failed the eval gates: {out.get('failures')}")
    shutil.rmtree(dest / "inputs", ignore_errors=True)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fixture index for the search local stack")
    sub = parser.add_subparsers(dest="command", required=True)
    p_build = sub.add_parser("build", help="Build the fixture index into a directory")
    p_build.add_argument("dir", type=Path)
    args = parser.parse_args(argv)
    out = build(args.dir)
    print(f"built fixture index {out['run']}-{out['token']} in {args.dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
