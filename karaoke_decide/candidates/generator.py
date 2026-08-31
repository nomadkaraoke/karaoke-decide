"""Candidate generation pipeline (v3: Spotify features + suitability score + LLM judge).

Cheap-to-expensive ordering so the slow/rate-limited steps only ever see songs
that survived everything cheaper:

1. Last.fm top tracks, highest playcount first (this IS the ranking).
2. Free local eliminators (no per-song network):
   a. manual reject list,
   b. "already ours" — a fresh query of gen's Firestore ``jobs``,
   c. KaraokeNerds community versions (cached BigQuery dump).
3. Spotify audio-features match (MANDATORY, batched BigQuery + cache) — we only
   propose tracks we can fully characterize; unmatched are dropped.
4. Expensive per-song gates, walking survivors in playcount order, capped at
   ``max_checks`` per run, stopping once ``count`` are confirmed:
   a. LRCLIB lyrics (mandatory),
   b. karaoke-suitability score (cheap pre-filter; recall-biased),
   c. LLM judge over lyrics + metadata (the real quality gate),
   d. flacfetch high-quality-FLAC check (hard gate; misses recorded).

Ranking is pure playcount; the score and LLM are pass/fail gates.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
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
from karaoke_decide.services.llm_judge import LlmJudge
from karaoke_decide.services.lrclib import LrclibClient
from karaoke_decide.services.spotify_features import SpotifyFeatures, SpotifyFeaturesService

from .cache import CandidateCache
from .lyrics import LyricsStats, analyze
from .matching import build_match_index, canonical_key, index_contains, strip_decorations
from .rejects import RejectList
from .scoring import ScoreWeights, suitability

# Cache TTLs (seconds).
_MONTH = 30 * 24 * 3600
LASTFM_TTL = 7 * 24 * 3600
KARAOKENERDS_TTL = _MONTH
FLACFETCH_TTL = _MONTH
LRCLIB_TTL: float | None = None  # lyrics never change -> cache forever
SPOTIFY_TTL: float | None = None  # audio features never change -> forever
LLM_TTL: float | None = None  # keyed by lyrics hash -> forever

# Stable CSV header for candidates.csv (also written when there are 0 rows).
_CSV_FIELDS = [
    "artist",
    "title",
    "playcount",
    "score",
    "instrumentalness",
    "duration_min",
    "speechiness",
    "popularity",
    "unique_lines",
    "unique_words",
    "llm_confidence",
    "llm_reason",
    "flac_provider",
    "flac_bit_depth",
    "flac_seeders",
    "flac_match_score",
]


def _csv_safe(value: Any) -> Any:
    """Neutralize spreadsheet formula injection in text cells."""
    if isinstance(value, str) and value and value[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


@dataclass
class Candidate:
    artist: str
    title: str
    playcount: int
    stats: LyricsStats
    features: SpotifyFeatures
    score: float
    llm: dict[str, Any]
    flac: dict[str, Any]

    def as_row(self) -> dict[str, Any]:
        return {
            "artist": self.artist,
            "title": self.title,
            "playcount": self.playcount,
            "score": round(self.score, 1),
            "instrumentalness": round(self.features.instrumentalness, 3),
            "duration_min": round(self.features.duration_min, 2),
            "speechiness": round(self.features.speechiness, 3),
            "popularity": self.features.popularity,
            "unique_lines": self.stats.unique_lines,
            "unique_words": self.stats.unique_words,
            "llm_confidence": self.llm.get("confidence"),
            "llm_reason": self.llm.get("reason"),
            "flac_provider": self.flac.get("provider"),
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
            "score": round(self.score, 1),
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
        spotify: SpotifyFeaturesService,
        llm: LlmJudge,
        username: str,
        weights: ScoreWeights | None = None,
        min_score: float = 45.0,
        spotify_batch_cap: int = 500,
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
        self.spotify = spotify
        self.llm = llm
        self.username = username
        self.weights = weights or ScoreWeights()
        self.min_score = min_score
        self.spotify_batch_cap = spotify_batch_cap
        self.flacfetch_min_interval = flacfetch_min_interval
        self.lrclib_min_interval = lrclib_min_interval
        self._last_flac = 0.0

    # ------------------------------------------------------------ data loads
    async def load_lastfm_tracks(self, max_tracks: int = 5000, refresh: bool = False) -> list[dict[str, Any]]:
        """Merged, playcount-desc top tracks (cached)."""
        cached = None if refresh else self.cache.get_blob("lastfm_toptracks", LASTFM_TTL)
        if cached is None:
            raw = await self.lastfm.get_all_top_tracks(self.username, period="overall", max_tracks=max_tracks)
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

    def load_karaokenerds_index(self, refresh: bool = False) -> set[tuple[str, str]]:
        rows = None if refresh else self.cache.get_blob("karaokenerds", KARAOKENERDS_TTL)
        if rows is None:
            sql = (
                f"SELECT Artist, Title FROM " f"`{self.catalog.PROJECT_ID}.{self.catalog.DATASET_ID}.karaokenerds_raw`"
            )
            rows = [[r["Artist"] or "", r["Title"] or ""] for r in self.catalog.client.query(sql).result()]
            self.cache.set_blob("karaokenerds", rows)
        return build_match_index([(a, t) for a, t in rows])

    # ------------------------------------------------------------ per-song
    def _spotify_features(self, artist: str, title: str) -> SpotifyFeatures | None:
        """Cached Spotify features for one track ({} sentinel = confirmed-absent)."""
        cached = self.cache.get_item("spotify", f"{artist}::{title}", SPOTIFY_TTL)
        if cached is None:
            return None  # caller must batch-load first
        return SpotifyFeatures.from_dict(cached) if cached else None

    def batch_load_spotify(self, tracks: list[dict[str, Any]]) -> None:
        """Batch-fetch Spotify features for uncached tracks (one BigQuery scan)."""
        misses = [
            (t["artist"], t["title"])
            for t in tracks
            if self.cache.get_item("spotify", f'{t["artist"]}::{t["title"]}', SPOTIFY_TTL) is None
        ]
        if not misses:
            return
        found = self.spotify.lookup(misses)
        for artist, title in misses:
            feats = found.get((artist, title))
            self.cache.set_item("spotify", f"{artist}::{title}", feats.as_dict() if feats else {})

    async def _lyrics(self, artist: str, title: str) -> dict[str, Any] | None:
        cache_key = f"{artist}::{title}"
        cached = self.cache.get_item("lrclib", cache_key, LRCLIB_TTL)
        if cached is not None:
            return cached if cached else None
        await asyncio.sleep(self.lrclib_min_interval)
        result = await self.lrclib.best_lyrics(artist, title)
        self.cache.set_item("lrclib", cache_key, result or {})
        return result

    def _judge(self, artist: str, title: str, lyrics_text: str, metadata: dict[str, Any]) -> dict[str, Any]:
        lyrics_hash = hashlib.sha1(lyrics_text.encode("utf-8")).hexdigest()[:12]
        cache_key = f"{artist}::{title}::{lyrics_hash}"
        cached = self.cache.get_item("llm", cache_key, LLM_TTL)
        if cached is not None:
            return dict(cached)
        verdict = self.llm.judge(artist, title, lyrics_text, metadata).as_dict()
        self.cache.set_item("llm", cache_key, verdict)
        return verdict

    async def _flac(self, artist: str, title: str) -> dict[str, Any] | None:
        cache_key = f"{artist}::{title}"
        cached = self.cache.get_item("flacfetch", cache_key, FLACFETCH_TTL)
        if cached is not None:
            return cached if cached else None
        wait = self.flacfetch_min_interval - (time.monotonic() - self._last_flac)
        if wait > 0:
            await asyncio.sleep(wait)
        results = await self.flacfetch.search(artist, strip_decorations(title))
        self._last_flac = time.monotonic()
        hit = self.flacfetch.best_flac(results)
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
        catalog_index = await asyncio.to_thread(self.load_karaokenerds_index, refresh_catalog)

        # Survivors of the free eliminators, in playcount order.
        survivors = [
            t
            for t in tracks
            if canonical_key(t["artist"], t["title"]) not in reject_keys
            and canonical_key(t["artist"], t["title"]) not in produced_keys
            and not index_contains(catalog_index, t["artist"], t["title"])
        ]
        result = SuggestResult()
        result.skipped["rejected"] = sum(1 for t in tracks if canonical_key(t["artist"], t["title"]) in reject_keys)
        result.skipped["already_ours"] = sum(
            1 for t in tracks if canonical_key(t["artist"], t["title"]) in produced_keys
        )
        result.skipped["community_version"] = (
            len(tracks) - len(survivors) - (result.skipped["rejected"] + result.skipped["already_ours"])
        )

        # Mandatory Spotify features: batch the top survivors in one scan.
        await asyncio.to_thread(self.batch_load_spotify, survivors[: self.spotify_batch_cap])

        for t in survivors:
            if len(result.confirmed) >= count or result.checks >= max_checks:
                break
            artist, title, plays = t["artist"], t["title"], t["playcount"]

            features = self._spotify_features(artist, title)
            if features is None:
                result.skipped["no_spotify_features"] += 1
                continue

            result.no_karaoke += 1
            result.considered += 1
            result.checks += 1

            try:
                lyrics = await self._lyrics(artist, title)
            except ExternalServiceError:
                result.skipped["lrclib_error"] += 1
                continue
            if not lyrics or lyrics.get("instrumental") or not (lyrics.get("plain") or lyrics.get("synced")):
                result.skipped["no_lrclib_lyrics"] += 1
                continue

            text = lyrics.get("plain") or lyrics.get("synced") or ""
            stats = analyze(text)
            score = suitability(features, stats, self.weights).score
            if score < self.min_score:
                result.skipped["low_score"] += 1
                result.misses.append(Miss(artist, title, plays, "low_score", stats))
                continue

            metadata = {
                **features.as_dict(),
                "unique_lines": stats.unique_lines,
                "unique_words": stats.unique_words,
                "suitability_score": round(score, 1),
            }
            try:
                verdict = self._judge(artist, title, text, metadata)
            except ExternalServiceError:
                result.skipped["llm_error"] += 1
                continue
            if not verdict.get("keep", True):
                result.skipped["llm_reject"] += 1
                result.misses.append(Miss(artist, title, plays, f"llm: {verdict.get('reason', '')}", stats))
                continue

            try:
                flac = await self._flac(artist, title)
            except ExternalServiceError:
                result.skipped["flacfetch_error"] += 1
                continue
            if not flac:
                result.skipped["unsourceable"] += 1
                result.misses.append(Miss(artist, title, plays, "unsourceable", stats))
                continue

            cand = Candidate(artist, title, plays, stats, features, score, verdict, flac)
            result.confirmed.append(cand)
            if progress is not None:
                progress(cand)

        return result

    # ------------------------------------------------------------ outputs
    def write_reports(self, result: SuggestResult) -> dict[str, Path]:
        out = self.base_dir / "output"
        out.mkdir(parents=True, exist_ok=True)

        csv_path = out / "candidates.csv"
        with csv_path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
            w.writeheader()
            for c in result.confirmed:
                w.writerow({k: _csv_safe(v) for k, v in c.as_row().items()})

        json_path = out / "candidates.json"
        json_path.write_text(json.dumps([c.submit_line() for c in result.confirmed], indent=2))

        md_path = out / "candidates.md"
        lines = [
            "# Karaoke Candidates",
            "",
            f"{len(result.confirmed)} confirmed "
            f"(considered {result.considered}; {len(result.misses)} rejected/unsourceable).",
            "",
            "| # | Plays | Artist | Title | Score | Inst | Dur | LLM reason |",
            "|---|-------|--------|-------|-------|------|-----|-----------|",
        ]
        for i, c in enumerate(result.confirmed, 1):
            lines.append(
                f"| {i} | {c.playcount} | {c.artist} | {c.title} "
                f"| {c.score:.0f} | {c.features.instrumentalness:.2f} "
                f"| {c.features.duration_min:.1f}m | {c.llm.get('reason', '')} |"
            )
        md_path.write_text("\n".join(lines) + "\n")

        misses_path = out / "rejected_misses.csv"
        with misses_path.open("w", newline="") as f:
            mw = csv.writer(f)
            mw.writerow(["playcount", "artist", "title", "reason"])
            for m in sorted(result.misses, key=lambda x: -x.playcount):
                mw.writerow([m.playcount, _csv_safe(m.artist), _csv_safe(m.title), m.reason])

        return {"csv": csv_path, "json": json_path, "md": md_path, "misses": misses_path}
