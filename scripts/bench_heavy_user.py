#!/usr/bin/env python
"""Benchmark a heavy user's launch against the Firestore emulator; fails when p95 is over budget.

    FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 GOOGLE_CLOUD_PROJECT=bench-heavy \\
      PYTHONPATH=. python scripts/bench_heavy_user.py --rows 50000 --catalog-dir .local-stack/catalog \\
      --index-dir .local-stack/song-index

Seeds one synthetic user, then runs the app's launch calls (recs v2, get-made, the sources summary's
known-songs count) in separate "warm" rounds, with the 60 s row cache emptied between rounds as a new
minute or another instance would see it. Refuses to run without the emulator: it writes.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import statistics
import sys
import time
from pathlib import Path
from typing import Any

BUDGET_P95_S = 2.0
USER_ID = "user_bench_heavy"


def synthetic_row(i: int, rng: random.Random) -> tuple[str, dict[str, Any]]:
    """Shaped like a real Last.fm import row (invented names only)."""
    artist = f"Bench Artist {rng.randrange(max(1, i // 5 + 1)):05d}"
    row = {
        "user_id": USER_ID,
        "song_id": f"lastfm:{i}",
        "artist": artist,
        "title": f"Invented Song Number {i}",
        "source": "lastfm",
        "sources": ["lastfm"],
        "playcount": rng.randrange(1, 400),
        "rank": i + 1,
        "times_sung": 0,
    }
    return f"{USER_ID}:lastfm:{i}", row


async def seed(fs: Any, rows: int) -> None:
    rng = random.Random(42)
    have = await fs.count_documents("user_songs", filters=[("user_id", "==", USER_ID)])
    if have == rows:
        return
    started = time.perf_counter()
    await fs.batch_set("user_songs", dict(synthetic_row(i, rng) for i in range(rows)))
    print(f"seeded {rows} rows in {time.perf_counter() - started:.1f} s")


def p95(values: list[float]) -> float:
    return sorted(values)[max(0, round(len(values) * 0.95) - 1)]


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=50_000)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--catalog-dir", required=True)
    parser.add_argument("--index-dir", required=True)
    args = parser.parse_args()
    if not os.environ.get("FIRESTORE_EMULATOR_HOST"):
        print("FIRESTORE_EMULATOR_HOST is not set: refusing to seed anything but the emulator", file=sys.stderr)
        return 2

    from backend.config import BackendSettings
    from backend.services.firestore_service import FirestoreService
    from backend.services.get_made_service import GetMadeService
    from backend.services.music_service_service import MusicServiceService
    from backend.services.recs_v2.filters import RecFilters
    from backend.services.recs_v2.service import RecommendationServiceV2
    from backend.services.user_songs_cache import USER_SONGS
    from karaoke_decide.catalog.reader import CatalogReader
    from karaoke_decide.core.models import User

    settings = BackendSettings()
    fs = FirestoreService(settings)
    await seed(fs, args.rows)

    reader = CatalogReader(Path(args.catalog_dir) / "catalog.db", Path(args.index_dir) / "availability.db")

    class Store:
        run = "bench"

        def require(self) -> CatalogReader:
            return reader

    class Hidden:
        async def hidden_keys(self, user_id: str) -> set[str]:
            return set()

    import backend.services.catalog_store as catalog_store

    catalog_store.get_catalog_store = lambda: Store()  # type: ignore[assignment]  # what the summary reads
    recs = RecommendationServiceV2(fs, Store(), Hidden())
    made = GetMadeService(fs, Store(), recs, Hidden())
    music = MusicServiceService(settings, fs)
    user = User(id=USER_ID)

    async def timed(work: Any) -> float:
        started = time.perf_counter()
        await work
        return time.perf_counter() - started

    results: dict[str, list[float]] = {"recs": [], "get_made": [], "known_songs": [], "launch": []}
    for n in range(args.rounds):
        USER_SONGS._entries.pop(USER_ID, None)  # as a new minute, or another instance, would find it
        started = time.perf_counter()
        r, g, k = await asyncio.gather(
            timed(recs.recommend(user, RecFilters(), "best")),
            timed(made.list(USER_ID)),
            timed(music.count_known_songs(USER_ID)),
        )
        results["recs"].append(r)
        results["get_made"].append(g)
        results["known_songs"].append(k)
        results["launch"].append(time.perf_counter() - started)
        print(f"round {n}: launch {results['launch'][-1]:.2f} s (recs {r:.2f}, get-made {g:.2f}, known {k:.2f})")

    warm = results["launch"][1:] or results["launch"]  # round 0 may build; the target is warm
    for name, values in results.items():
        print(f"{name:12s} median {statistics.median(values):6.2f} s  p95 {p95(values):6.2f} s")
    print(f"warm launch p95 {p95(warm):.2f} s, budget {BUDGET_P95_S:.1f} s")
    return 0 if p95(warm) <= BUDGET_P95_S else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
