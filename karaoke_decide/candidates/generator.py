"""Candidate generation pipeline.

Cheap-to-expensive ordering so the rate-limited APIs only ever see songs that
already survived everything else:

1. Last.fm top tracks, highest playcount first (this IS the ranking).
2. Free local eliminators (no per-song network):
   a. manual reject list,
   b. "already ours" — a fresh query of gen's Firestore ``jobs``,
   c. KaraokeNerds community versions (cached BigQuery dump).
3. Expensive per-song gates, walking survivors in playcount order, capped at
   ``max_checks`` per run, stopping once ``count`` are confirmed:
   a. LRCLIB lyrics (mandatory) + richness heuristic,
   b. flacfetch high-quality-FLAC check (hard gate; misses recorded).

Ranking is pure playcount; everything else is a pass/fail gate.
"""

from __future__ import annotations

import asyncio
import csv
import json
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from karaoke_decide.core.exceptions import ExternalServiceError
from karaoke_decide.services.bigquery_catalog import BigQueryCatalogService
from karaoke_decide.services.flacfetch import FlacfetchClient
from karaoke_decide.services.gen_jobs import GenJobsService
from karaoke_decide.services.lastfm import LastFmClient
from karaoke_decide.services.lrclib import LrclibClient

from .cache import CandidateCache
from .lyrics import LyricsStats, RichnessThresholds, analyze, is_rich
from .matching import (
    build_match_index,
    canonical_key,
    index_contains,
    strip_decorations,
)
from .rejects import RejectList

# Cache TTLs (seconds).
_MONTH = 30 * 24 * 3600
LASTFM_TTL = 7 * 24 * 3600
KARAOKENERDS_TTL = _MONTH
FLACFETCH_TTL = _MONTH
LRCLIB_TTL: float | None = None  # lyrics never change -> cache forever
TAGS_TTL: float | None = None

_ELECTRONIC_TAGS = {
    "electronic", "electronica", "drum and bass", "drum n bass", "dnb",
    "techno", "house", "edm", "dubstep", "trance", "ambient", "idm",
    "breakbeat", "trap", "instrumental", "downtempo", "chillout", "synthwave",
}


@dataclass
class Candidate:
    artist: str
    title: str
    playcount: int
    is_electronic: bool
    stats: LyricsStats
    flac: dict[str, Any]

    def as_row(self) -> dict[str, Any]:
        return {
            "artist": self.artist,
            "title": self.title,
            "playcount": self.playcount,
            "electronic": self.is_electronic,
            **{f"lyrics_{k}": v for k, v in self.stats.as_dict().items()},
            "flac_provider": self.flac.get("provider"),
            "flac_format": self.flac.get("format"),
            "flac_bit_depth": self.flac.get("bit_depth"),
            "flac_seeders": self.flac.get("seeders"),
            "flac_match_score": self.flac.get("match_score"),
        }

    def submit_line(self) -> dict[str, Any]:
        """Submit-ready record for feeding into the gen create-job flow."""
        return {
            "artist": self.artist,
            "title": strip_decorations(self.title),
            "brand_prefix": "NOMAD",
            "playcount": self.playcount,
        }


@dataclass
class Miss:
    artist: str
    title: str
    playcount: int
    reason: str
    stats: LyricsStats | None = None


@dataclass
class SuggestResult:
    confirmed: list[Candidate] = field(default_factory=list)
    misses: list[Miss] = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)
    considered: int = 0
    checks: int = 0
    no_karaoke: int = 0


class CandidateGenerator:
    """Orchestrates the full candidate pipeline."""

    def __init__(
        self,
        *,
        base_dir: Path,
        lastfm: LastFmClient,
        lrclib: LrclibClient,
        flacfetch: FlacfetchClient,
        gen_jobs: GenJobsService,
        catalog: BigQueryCatalogService,
        username: str,
        thresholds: RichnessThresholds | None = None,
        min_seeders: int = 1,
        min_match_score: float = 0.8,
        flacfetch_min_interval: float = 12.0,
        lrclib_min_interval: float = 0.3,
    ):
        self.base_dir = Path(base_dir)
        self.cache = CandidateCache(self.base_dir)
        self.rejects = RejectList(self.base_dir / "rejects.jsonl")
        self.lastfm = lastfm
        self.lrclib = lrclib
        self.flacfetch = flacfetch
        self.gen_jobs = gen_jobs
        self.catalog = catalog
        self.username = username
        self.thresholds = thresholds or RichnessThresholds()
        self.min_seeders = min_seeders
        self.min_match_score = min_match_score
        self.flacfetch_min_interval = flacfetch_min_interval
        self.lrclib_min_interval = lrclib_min_interval
        self._last_flac = 0.0

    # ------------------------------------------------------------ data loads
    async def load_lastfm_tracks(
        self, max_tracks: int = 5000, refresh: bool = False
    ) -> list[dict[str, Any]]:
        """Merged, playcount-desc top tracks (cached)."""
        cached = None if refresh else self.cache.get_blob("lastfm_toptracks", LASTFM_TTL)
        if cached is None:
            raw = await self.lastfm.get_all_top_tracks(
                self.username, period="overall", max_tracks=max_tracks
            )
            cached = [
                {
                    "artist": (t.get("artist") or {}).get("name", "")
                    if isinstance(t.get("artist"), dict)
                    else t.get("artist", ""),
                    "title": t.get("name", ""),
                    "playcount": int(t.get("playcount", 0) or 0),
                }
                for t in raw
            ]
            self.cache.set_blob("lastfm_toptracks", cached)
        return self._merge_duplicates(cached)

    @staticmethod
    def _merge_duplicates(tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: dict[tuple[str, str], dict[str, Any]] = {}
        for t in tracks:
            key = canonical_key(t["artist"], t["title"])
            if key in merged:
                merged[key]["playcount"] += t["playcount"]
                if len(t["title"]) < len(merged[key]["title"]):
                    merged[key]["title"] = t["title"]
            else:
                merged[key] = dict(t)
        return sorted(merged.values(), key=lambda x: -x["playcount"])

    def load_karaokenerds_index(
        self, refresh: bool = False
    ) -> set[tuple[str, str]]:
        rows = None if refresh else self.cache.get_blob("karaokenerds", KARAOKENERDS_TTL)
        if rows is None:
            sql = (
                f"SELECT Artist, Title FROM "
                f"`{self.catalog.PROJECT_ID}.{self.catalog.DATASET_ID}.karaokenerds_raw`"
            )
            rows = [
                [r["Artist"] or "", r["Title"] or ""]
                for r in self.catalog.client.query(sql).result()
            ]
            self.cache.set_blob("karaokenerds", rows)
        return build_match_index([(a, t) for a, t in rows])

    # ------------------------------------------------------------ per-song
    async def _artist_is_electronic(self, artist: str) -> bool:
        """Genre check via Last.fm artist top tags (cached per artist).

        Artist-level tags are used because track-level tags are usually empty.
        """
        tags = self.cache.get_item("lastfm_artist_tags", artist, TAGS_TTL)
        if tags is None:
            try:
                tags = (await self.lastfm.get_artist_top_tags(artist))[:8]
            except Exception:  # noqa: BLE001 - tags are best-effort
                tags = []
            self.cache.set_item("lastfm_artist_tags", artist, tags)
        return any(any(et in tag for et in _ELECTRONIC_TAGS) for tag in tags)

    async def _lyrics(self, artist: str, title: str) -> dict[str, Any] | None:
        cache_key = f"{artist}::{title}"
        cached = self.cache.get_item("lrclib", cache_key, LRCLIB_TTL)
        if cached is not None:
            return cached if cached else None  # {} sentinel = confirmed-absent
        await asyncio.sleep(self.lrclib_min_interval)
        # Errors propagate to the caller (recorded as a skip, not cached, so the
        # song is retried on the next run).
        result = await self.lrclib.best_lyrics(artist, title)
        self.cache.set_item("lrclib", cache_key, result or {})
        return result

    async def _flac(self, artist: str, title: str) -> dict[str, Any] | None:
        cache_key = f"{artist}::{title}"
        cached = self.cache.get_item("flacfetch", cache_key, FLACFETCH_TTL)
        if cached is not None:
            return cached if cached else None
        # rate-limit real calls only
        wait = self.flacfetch_min_interval - (time.monotonic() - self._last_flac)
        if wait > 0:
            await asyncio.sleep(wait)
        results = await self.flacfetch.search(artist, strip_decorations(title))
        self._last_flac = time.monotonic()
        hit = self.flacfetch.best_flac(
            results, min_seeders=self.min_seeders, min_match_score=self.min_match_score
        )
        payload = hit.as_dict() if hit else {}
        self.cache.set_item("flacfetch", cache_key, payload)
        return payload or None

    # ------------------------------------------------------------ main
    async def suggest(
        self,
        count: int = 5,
        min_plays: int = 6,
        max_checks: int = 120,
        refresh_lastfm: bool = False,
        refresh_catalog: bool = False,
        progress: Any | None = None,
    ) -> SuggestResult:
        tracks = await self.load_lastfm_tracks(refresh=refresh_lastfm)
        tracks = [t for t in tracks if t["playcount"] >= min_plays]

        reject_keys = self.rejects.key_set()
        produced_keys = await asyncio.to_thread(self.gen_jobs.produced_keys)
        catalog_index = await asyncio.to_thread(
            self.load_karaokenerds_index, refresh_catalog
        )

        result = SuggestResult()
        for t in tracks:
            if len(result.confirmed) >= count or result.checks >= max_checks:
                break
            artist, title, plays = t["artist"], t["title"], t["playcount"]
            key = canonical_key(artist, title)

            if key in reject_keys:
                result.skipped["rejected"] += 1
                continue
            if key in produced_keys:
                result.skipped["already_ours"] += 1
                continue
            if index_contains(catalog_index, artist, title):
                result.skipped["community_version"] += 1
                continue

            # survivor -> expensive checks
            result.no_karaoke += 1
            result.considered += 1
            result.checks += 1

            try:
                lyrics = await self._lyrics(artist, title)
            except ExternalServiceError:
                result.skipped["lrclib_error"] += 1
                continue
            if not lyrics or lyrics.get("instrumental") or not (
                lyrics.get("plain") or lyrics.get("synced")
            ):
                result.skipped["no_lrclib_lyrics"] += 1
                continue

            stats = analyze(lyrics.get("plain") or lyrics.get("synced") or "")
            is_elec = await self._artist_is_electronic(artist)
            passes, reason = is_rich(stats, self.thresholds, is_elec)
            if not passes:
                result.skipped[reason] += 1
                continue

            try:
                flac = await self._flac(artist, title)
            except ExternalServiceError:
                result.skipped["flacfetch_error"] += 1
                continue
            if not flac:
                result.skipped["unsourceable"] += 1
                result.misses.append(
                    Miss(artist, title, plays, "unsourceable", stats)
                )
                continue

            cand = Candidate(artist, title, plays, is_elec, stats, flac)
            result.confirmed.append(cand)
            if progress is not None:
                progress(cand)

        return result

    async def calibrate(
        self,
        sample: int = 200,
        min_plays: int = 6,
        refresh_catalog: bool = False,
    ) -> list[dict[str, Any]]:
        """Fetch lyrics for a sample of no-karaoke survivors and return per-song
        richness stats (regardless of pass/fail), so thresholds can be tuned.

        This exercises exactly the songs the lyrics gate would see: after the
        cheap eliminators, in playcount order. flacfetch is NOT called.
        """
        tracks = await self.load_lastfm_tracks()
        tracks = [t for t in tracks if t["playcount"] >= min_plays]
        reject_keys = self.rejects.key_set()
        produced_keys = await asyncio.to_thread(self.gen_jobs.produced_keys)
        catalog_index = await asyncio.to_thread(
            self.load_karaokenerds_index, refresh_catalog
        )

        rows: list[dict[str, Any]] = []
        for t in tracks:
            if len(rows) >= sample:
                break
            artist, title, plays = t["artist"], t["title"], t["playcount"]
            key = canonical_key(artist, title)
            if key in reject_keys or key in produced_keys:
                continue
            if index_contains(catalog_index, artist, title):
                continue

            try:
                lyrics = await self._lyrics(artist, title)
            except ExternalServiceError:
                continue  # transient; skip from the calibration sample
            is_elec = await self._artist_is_electronic(artist)
            if not lyrics or lyrics.get("instrumental") or not (
                lyrics.get("plain") or lyrics.get("synced")
            ):
                rows.append(
                    {
                        "artist": artist, "title": title, "playcount": plays,
                        "electronic": is_elec, "has_lyrics": False, "stats": None,
                    }
                )
                continue
            stats = analyze(lyrics.get("plain") or lyrics.get("synced") or "")
            rows.append(
                {
                    "artist": artist, "title": title, "playcount": plays,
                    "electronic": is_elec, "has_lyrics": True, "stats": stats,
                }
            )
        return rows

    # ------------------------------------------------------------ outputs
    def write_reports(self, result: SuggestResult) -> dict[str, Path]:
        out = self.base_dir / "output"
        out.mkdir(parents=True, exist_ok=True)

        csv_path = out / "candidates.csv"
        rows = [c.as_row() for c in result.confirmed]
        if rows:
            with csv_path.open("w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                w.writeheader()
                for i, row in enumerate(rows, 1):
                    w.writerow(row)

        json_path = out / "candidates.json"
        json_path.write_text(
            json.dumps([c.submit_line() for c in result.confirmed], indent=2)
        )

        md_path = out / "candidates.md"
        lines = [
            "# Karaoke Candidates",
            "",
            f"{len(result.confirmed)} confirmed "
            f"(considered {result.considered}, {result.no_karaoke} had no existing "
            f"karaoke; {len(result.misses)} unsourceable).",
            "",
            "| # | Plays | Artist | Title | Uniq lines | Uniq words | FLAC |",
            "|---|-------|--------|-------|-----------|-----------|------|",
        ]
        for i, c in enumerate(result.confirmed, 1):
            lines.append(
                f"| {i} | {c.playcount} | {c.artist} | {c.title} "
                f"| {c.stats.unique_lines} | {c.stats.unique_words} "
                f"| {c.flac.get('provider')} {c.flac.get('seeders')}s |"
            )
        md_path.write_text("\n".join(lines) + "\n")

        misses_path = out / "unsourceable_misses.csv"
        with misses_path.open("w", newline="") as f:
            mw = csv.writer(f)
            mw.writerow(["playcount", "artist", "title", "unique_lines", "unique_words"])
            for m in sorted(result.misses, key=lambda x: -x.playcount):
                sw = m.stats.unique_words if m.stats else ""
                sl = m.stats.unique_lines if m.stats else ""
                mw.writerow([m.playcount, m.artist, m.title, sl, sw])

        return {
            "csv": csv_path,
            "json": json_path,
            "md": md_path,
            "misses": misses_path,
        }
