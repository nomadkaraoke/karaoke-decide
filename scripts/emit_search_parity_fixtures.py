#!/usr/bin/env python
"""Emit the song-search parity fixtures the mobile TS matcher is tested against.

Writes (default ``tests/fixtures/song_search/``):

  offline_fixture.db  ``offline.build_offline_db`` over the decide fixtures
                      (songs_fixture.tsv → song_id.db; kn_*_fixture.json →
                      availability.db), run ``"fixture"``, ``meta.built_at = 0``.
  parity.json         what the Python matcher answers over offline_fixture.db with
                      ``margin_bonus = OFFLINE_MARGIN_BONUS`` (0.03), for every
                      song_id_eval.jsonl query (file order) then the Review Focus 1
                      inputs.

The mobile repo copies both (``scripts/sync-search-fixtures.mjs``); its jest parity
test runs the TS port over the .db and compares. ``test_parity_fixture_is_fresh``
fails when these files are stale: re-run this script and commit.

parity.json schema — a JSON array, one object per query:

  {
    "q": str,                         the raw query, passed to identify unchanged
    "identify": {                     SongIdentifier.identify(q, limit=5), in full:
      "status": "confident" | "candidates" | "none",
      "best": null | {
        "artist": str, "title": str,  display spelling (offline songs row)
        "score": float,               rounded to 3 places (compare to 1e-6)
        "popularity": int | null,
        "karaoke": bool,              false = a rival (no karaoke version)
        "song_key": str,              text_normalize.song_key(artist, title) = songs.song_key
        "detail": {str: number}       the scoring breakdown (title_cov, q_cov, …)
      },
      "candidates": [                 ranked best-first (best is candidates[0]); ≤ 5;
                                      exact score ties: popularity desc, artist, title;
        {artist, title, score, popularity, karaoke, song_key}   no detail
      ]
    },
    "kind": null | "same" | "cosmetic" | "completed" | "content",
                                      identify_kind(q, best.artist, best.title)
                                      whenever best is not null (any status).
                                      Clients must IGNORE kind unless
                                      identify.status == "confident" (no banner
                                      for candidates / none); it is emitted for
                                      every best only so the TS port is tested.
    "groups": [                       offline.offline_groups(db, identify):
      {                               confident → the best song; candidates → each
        "song_key": str,              candidate with versions, identify order;
        "artist": str, "title": str,  none → []. Rivals never produce a group.
        "karaoke": true,
        "version_count": int,
        "versions": [                 playable (YouTube URL) first, then rank, then rowid
          {"brand_code": str, "brand_name": str, "kind": "community" | "commercial",
           "youtube_url": str | null, "rank": int}
        ]
      }
    ]
  }

Usage: python scripts/emit_search_parity_fixtures.py [--out-dir DIR]
"""

from __future__ import annotations

import argparse
import gzip
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from karaoke_decide.song_search.availability import build_availability_db  # noqa: E402
from karaoke_decide.song_search.build_song_id import build as build_song_id  # noqa: E402
from karaoke_decide.song_search.eval import load_cases  # noqa: E402
from karaoke_decide.song_search.identify import SongIdentifier  # noqa: E402
from karaoke_decide.song_search.identify_kind import identify_kind  # noqa: E402
from karaoke_decide.song_search.offline import OFFLINE_MARGIN_BONUS, build_offline_db, offline_groups  # noqa: E402
from karaoke_decide.song_search.text_normalize import song_key  # noqa: E402

FIXTURES = REPO / "tests" / "fixtures" / "song_search"

# Review Focus 1: queries the matcher has never seen (status none, no groups, no throw).
REVIEW_FOCUS_1 = ["", " ", "karaoke", "x", "🎤🎤", "a" * 250, "שיר", "歌"]


def _gz(src: Path, dst: Path) -> Path:
    with open(src, "rb") as f, gzip.open(dst, "wb") as g:
        shutil.copyfileobj(f, g)
    return dst


def build_fixture_db(out: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        song_id_db = d / "song_id.db"
        build_song_id([FIXTURES / "songs_fixture.tsv"], song_id_db, run="fixture")
        availability_db = d / "availability.db"
        build_availability_db(
            _gz(FIXTURES / "kn_full_fixture.json", d / "full.json.gz"),
            _gz(FIXTURES / "kn_community_fixture.json", d / "community.json.gz"),
            song_id_db,
            availability_db,
            run="fixture",
        )
        build_offline_db(song_id_db, availability_db, out, run="fixture")
    conn = sqlite3.connect(out)
    try:
        conn.execute("UPDATE meta SET value = '0' WHERE key = 'built_at'")  # reproducible content
        conn.commit()
        conn.execute("VACUUM")
    finally:
        conn.close()


def _with_key(m: dict[str, Any]) -> dict[str, Any]:
    return m | {"song_key": song_key(m["artist"], m["title"])}


def parity_cases(db: Path, queries: list[str]) -> list[dict[str, Any]]:
    ident = SongIdentifier(db, margin_bonus=OFFLINE_MARGIN_BONUS)
    out = []
    for q in queries:
        r = ident.identify(q)
        best = r["best"]
        out.append(
            {
                "q": q,
                "identify": {
                    "status": r["status"],
                    "best": _with_key(best) if best else None,
                    "candidates": [_with_key(c) for c in r["candidates"]],
                },
                "kind": identify_kind(q, best["artist"], best["title"]) if best else None,
                "groups": offline_groups(db, r),
            }
        )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out-dir", type=Path, default=FIXTURES)
    args = parser.parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    db = args.out_dir / "offline_fixture.db"
    build_fixture_db(db)
    queries = [c["q"] for c in load_cases(FIXTURES / "song_id_eval.jsonl")] + REVIEW_FOCUS_1
    cases = parity_cases(db, queries)
    (args.out_dir / "parity.json").write_text(json.dumps(cases, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{db} ({db.stat().st_size} bytes), parity.json ({len(cases)} queries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
