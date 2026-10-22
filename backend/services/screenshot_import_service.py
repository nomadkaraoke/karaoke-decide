"""Screenshot import: read artists/songs off music-app screenshots with Gemini Flash, then save the
ones the user confirms through the shared ``ImportIngestService``.

Gemini is reached with the AI Studio key only (``get_genai_client``), never Vertex. The per-user
daily image limit is reserved atomically in ``screenshot_imports/{user_id}`` and released when
Gemini fails, so a failed request never costs the user quota.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from backend.config import BackendSettings
from backend.services.catalog_lookup import CatalogLookup
from backend.services.firestore_service import FirestoreService
from backend.services.import_ingest_service import ImportIngestService, IngestArtist, IngestResult, IngestTrack
from karaoke_decide.catalog.keys import artist_key
from karaoke_decide.services.gemini_client import (
    get_genai_client,
    is_quota_or_billing_error,
    quota_exhausted_message,
)
from karaoke_decide.song_search.text_normalize import identity_normalize
from karaoke_decide.song_search.text_normalize import song_key as make_song_key

logger = logging.getLogger(__name__)

SCREENSHOT_IMAGES_PER_DAY = 10
MAX_IMAGES_PER_REQUEST = 5
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_CONFIRM_ITEMS = 200
GEMINI_TIMEOUT_MS = 45_000
RATE_COLLECTION = "screenshot_imports"

EXTRACT_PROMPT = (
    "This is a screenshot of a music app. List the artists and songs visible on screen. "
    'Return JSON only: {"items":[{"kind":"artist"|"song","artist":string,"title":string|null,'
    '"confidence":number}]}. Ignore playlist names, podcasts, UI labels and ads.'
)

_quota_logged = False


class ScreenshotUnavailableError(Exception):
    """Screenshot import is off, or Gemini failed (route: 503 ``screenshot_unavailable``)."""


class DailyLimitError(Exception):
    """The user is out of screenshot reads for today (route: 429 ``daily_limit``)."""


@dataclass(frozen=True)
class RawCandidate:
    kind: Literal["artist", "song"]
    artist: str
    title: str | None
    confidence: float


class Candidate(BaseModel):
    kind: Literal["artist", "song"]
    artist: str
    title: str | None = None
    confidence: float
    matched: bool
    song_key: str | None = None


class ConfirmItem(BaseModel):
    kind: Literal["artist", "song"]
    artist: str
    title: str | None = None


class ScreenshotExtractor(Protocol):
    async def extract(self, images: list[tuple[bytes, str]]) -> list[RawCandidate]: ...


def _log_quota_once(exc: BaseException) -> None:
    global _quota_logged
    if not _quota_logged:
        _quota_logged = True
        logger.error("%s (%s)", quota_exhausted_message(), type(exc).__name__)
    else:
        logger.warning("Gemini quota/billing error again: %s", type(exc).__name__)


def parse_items(text: str) -> list[RawCandidate]:
    """Tolerant parse of the model's JSON; anything unusable is dropped."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return []
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    out: list[RawCandidate] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        kind = it.get("kind")
        artist = it.get("artist")
        title = it.get("title")
        if kind not in ("artist", "song") or not isinstance(artist, str) or not artist.strip():
            continue
        title = title.strip() if isinstance(title, str) and title.strip() else None
        if kind == "song" and title is None:
            continue
        try:
            conf = float(it.get("confidence", 0.5))
        except (TypeError, ValueError):
            conf = 0.5
        out.append(RawCandidate(kind, artist.strip(), title if kind == "song" else None, min(1.0, max(0.0, conf))))
    return out


class GeminiScreenshotExtractor:
    def __init__(self, model: str) -> None:
        self._model = model

    async def _one(self, client: Any, data: bytes, mime: str) -> list[RawCandidate]:
        from google.genai import types

        resp = await client.aio.models.generate_content(
            model=self._model,
            contents=[types.Part.from_bytes(data=data, mime_type=mime), EXTRACT_PROMPT],
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        return parse_items(resp.text or "")

    async def extract(self, images: list[tuple[bytes, str]]) -> list[RawCandidate]:
        client = get_genai_client(timeout_ms=GEMINI_TIMEOUT_MS)
        results = await asyncio.gather(*(self._one(client, d, m) for d, m in images))
        return [c for r in results for c in r]


def get_screenshot_extractor(settings: BackendSettings) -> ScreenshotExtractor:
    if settings.decide_fake_externals:
        from backend.services.fake_screenshot_extractor import FakeScreenshotExtractor  # Task 13

        return FakeScreenshotExtractor()
    return GeminiScreenshotExtractor(settings.screenshot_llm_model)


class ScreenshotImportService:
    def __init__(
        self,
        firestore: FirestoreService,
        ingest: ImportIngestService,
        catalog: CatalogLookup,
        extractor: ScreenshotExtractor | None,
    ) -> None:
        """``extractor`` is None when screenshot import is disabled."""
        self.db = firestore
        self.ingest = ingest
        self.catalog = catalog
        self.extractor = extractor

    async def _reserve(self, user_id: str, n: int) -> None:
        today = datetime.now(UTC).date().isoformat()

        def reserve(cur: dict[str, Any] | None) -> dict[str, Any]:
            cur = cur or {}
            used = cur.get("count", 0) if cur.get("day") == today else 0
            if used + n > SCREENSHOT_IMAGES_PER_DAY:
                raise DailyLimitError
            return {"day": today, "count": used + n}

        await self.db.transform_document_atomically(RATE_COLLECTION, user_id, reserve)

    async def _release(self, user_id: str, n: int) -> None:
        today = datetime.now(UTC).date().isoformat()

        def release(cur: dict[str, Any] | None) -> dict[str, Any] | None:
            if not cur or cur.get("day") != today:
                return cur
            return {"day": today, "count": max(0, cur.get("count", 0) - n)}

        try:
            await self.db.transform_document_atomically(RATE_COLLECTION, user_id, release)
        except Exception:
            logger.warning("Could not release screenshot reservation", exc_info=True)

    async def extract(self, user_id: str, images: list[tuple[bytes, str]]) -> list[Candidate]:
        if self.extractor is None:
            raise ScreenshotUnavailableError("disabled")
        await self._reserve(user_id, len(images))
        try:
            raw = await self.extractor.extract(images)
        except Exception as exc:
            await self._release(user_id, len(images))
            if is_quota_or_billing_error(exc):
                _log_quota_once(exc)
            else:
                logger.warning("Screenshot extraction failed: %s", type(exc).__name__, exc_info=True)
            raise ScreenshotUnavailableError("gemini_error") from exc
        return await self._match(_dedupe(raw))

    async def _match(self, raw: list[RawCandidate]) -> list[Candidate]:
        song_keys = {c: make_song_key(c.artist, c.title or "") for c in raw if c.kind == "song"}
        artist_keys = {c: artist_key(c.artist) for c in raw if c.kind == "artist"}
        known_songs = await asyncio.to_thread(self.catalog.lookup_song_keys, list(song_keys.values()))
        known_artists = await asyncio.to_thread(self.catalog.lookup_artist_keys, list(artist_keys.values()))
        out: list[Candidate] = []
        for c in raw:
            if c.kind == "song":
                key = song_keys[c]
                out.append(
                    Candidate(
                        kind="song",
                        artist=c.artist,
                        title=c.title,
                        confidence=c.confidence,
                        matched=key in known_songs,
                        song_key=key if key in known_songs else None,
                    )
                )
            else:
                name = known_artists.get(artist_keys[c])
                out.append(
                    Candidate(
                        kind="artist",
                        artist=name or c.artist,
                        confidence=c.confidence,
                        matched=name is not None,
                    )
                )
        out.sort(key=lambda x: (not x.matched, -x.confidence))
        return out

    async def confirm(self, user_id: str, items: list[ConfirmItem]) -> IngestResult:
        tracks = [IngestTrack(artist=i.artist, title=i.title) for i in items if i.kind == "song" and i.title]
        artists = [IngestArtist(name=i.artist) for i in items if i.kind == "artist"]
        result = await self.ingest.ingest(user_id, "screenshot", tracks, artists or None)
        await self.ingest.record_import(user_id, "screenshot", status="ok", result=result)
        return result


def _dedupe(raw: list[RawCandidate]) -> list[RawCandidate]:
    best: dict[tuple[str, str, str], RawCandidate] = {}
    for c in raw:
        k = (c.kind, identity_normalize(c.artist), identity_normalize(c.title or ""))
        if k not in best or c.confidence > best[k].confidence:
            best[k] = c
    return list(best.values())
