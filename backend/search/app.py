"""The search service: a separate scale-to-zero Cloud Run app (spec §5.1, §5.5).

Run with ``uvicorn backend.search.app:search_app --host 0.0.0.0 --port 8000``.
It is NOT mounted into ``backend/main.py``.

Environment:
  SONG_INDEX_LOCAL_DIR             an already-built index dir; skips the download (local stack)
  SONG_INDEX_RUN                   published "<run>-<token>" dir to load (empty → latest.json)
  SONG_INDEX_BUCKET                bucket holding song-index/ (default nomadkaraoke-kn-data)
  SONG_INDEX_DIR                   where downloads go (default /tmp/song-index; in-memory on Cloud Run)
  SONG_INDEX_OFFLINE_URL_TEMPLATE  public offline.db.gz URL, with {run}
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from backend.search import index_loader
from backend.search.events import configure_logging, log_event
from backend.search.models import (
    Health,
    IndexInfo,
    OfflineIndexManifest,
    SearchEvent,
    SearchResponse,
    SongDetail,
    SongGroup,
)
from backend.search.ratelimit import SEARCH_LIMITS, RateLimiter, RateLimits, device_id, rate_limit
from backend.search.service import SearchService

logger = logging.getLogger(__name__)

DEFAULT_BUCKET = "nomadkaraoke-kn-data"
DEFAULT_DOWNLOAD_DIR = "/tmp/song-index"
DEFAULT_OFFLINE_URL_TEMPLATE = "https://decide.nomadkaraoke.com/offline-index/{run}/offline.db.gz"
MAX_SID_CHARS = 64


@dataclass
class SearchState:
    """What the routes need; ``service``/``index`` are None when the index failed to load."""

    service: SearchService | None = None
    index: index_loader.LoadedIndex | None = None
    offline_url_template: str = DEFAULT_OFFLINE_URL_TEMPLATE


def _load_index() -> index_loader.LoadedIndex | None:
    """Load at startup. Failure leaves the app up (health answers, search 503s)."""
    local_dir = os.environ.get("SONG_INDEX_LOCAL_DIR", "").strip()
    started = time.monotonic()
    if local_dir:
        try:
            loaded = index_loader.open_index(index_loader.load_local(Path(local_dir)))
        except index_loader.IndexLoadError as exc:
            logger.warning("search index not loaded from SONG_INDEX_LOCAL_DIR=%s: %s", local_dir, exc)
            return None
    else:
        try:
            paths = index_loader.load(
                os.environ.get("SONG_INDEX_RUN", ""),
                os.environ.get("SONG_INDEX_BUCKET", "").strip() or DEFAULT_BUCKET,
                Path(os.environ.get("SONG_INDEX_DIR", "").strip() or DEFAULT_DOWNLOAD_DIR),
            )
            loaded = index_loader.open_index(paths)
        except Exception:
            # Production can't search at all: a genuinely broken state.
            logger.exception("search index failed to load (SONG_INDEX_RUN=%r)", os.environ.get("SONG_INDEX_RUN"))
            return None
    logger.info(
        "search index %s loaded in %.1fs (built_at %s)",
        loaded.info.run,
        time.monotonic() - started,
        loaded.info.built_at,
    )
    return loaded


def _state(request: Request) -> SearchState:
    state: SearchState = request.app.state.search
    return state


def _service(request: Request) -> SearchService:
    state = _state(request)
    if state.service is None:
        raise HTTPException(status_code=503, detail="Search index unavailable")
    return state.service


def _loaded(request: Request) -> index_loader.LoadedIndex:
    state = _state(request)
    if state.index is None:
        raise HTTPException(status_code=503, detail="Search index unavailable")
    return state.index


def build_router(limiter: RateLimiter) -> APIRouter:
    router = APIRouter(prefix="/api/search")
    limited = [Depends(rate_limit(limiter))]

    @router.get("/health", response_model=Health)
    def health() -> Health:
        # Warm-up ping + uptime check: must not touch the index.
        return Health(ok=True)

    @router.get("", response_model=SearchResponse, dependencies=limited)
    def search(
        request: Request,
        q: str = "",
        limit: int = 20,
        sid: str | None = Query(default=None, max_length=MAX_SID_CHARS),
    ) -> SearchResponse:
        service = _service(request)
        loaded = _loaded(request)
        sid = sid or uuid.uuid4().hex
        started = time.perf_counter()
        result = service.search(q, limit)
        ms = round((time.perf_counter() - started) * 1000, 1)
        log_event(
            "search",
            sid=sid,
            device=device_id(request),
            q=q[:200],
            status=result.identification.status,
            kind=result.identification.kind,
            group_count=len(result.groups),
            ms=ms,
        )
        return SearchResponse(
            identification=result.identification,
            groups=result.groups,
            index=IndexInfo(run=loaded.info.run, built_at=loaded.info.built_at),
            sid=sid,
        )

    @router.get("/popular", response_model=list[SongGroup], dependencies=limited)
    def popular(request: Request, limit: int = 30) -> list[SongGroup]:
        return _service(request).popular(limit)

    @router.get("/song", response_model=SongDetail, dependencies=limited)
    def song(request: Request, key: str = Query(min_length=1, max_length=512)) -> SongDetail:
        detail = _service(request).song(key)
        if detail is None:
            raise HTTPException(status_code=404, detail="Unknown song")
        return detail

    # GET /api/search/resolve (Task A6) is added here with its own, tighter RateLimiter.

    @router.post("/event", status_code=204, response_class=Response, dependencies=limited)
    def event(request: Request, body: SearchEvent) -> Response:
        log_event(body.type, sid=body.sid, device=device_id(request), data=body.data)
        return Response(status_code=204)

    @router.get("/offline-index", response_model=OfflineIndexManifest, dependencies=limited)
    def offline_index(request: Request) -> OfflineIndexManifest:
        loaded = _loaded(request)
        m = index_loader.offline_manifest(loaded.paths)
        if m is None:
            raise HTTPException(status_code=404, detail="No offline index for this run")
        url = _state(request).offline_url_template.format(run=m["run"])
        return OfflineIndexManifest(url=url, **m)

    return router


def create_search_app(*, search_limits: RateLimits = SEARCH_LIMITS) -> FastAPI:
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        state = SearchState(
            offline_url_template=os.environ.get("SONG_INDEX_OFFLINE_URL_TEMPLATE", "").strip()
            or DEFAULT_OFFLINE_URL_TEMPLATE
        )
        # Blocking startup until the index is in: Cloud Run's startup probe (health)
        # only passes, and traffic only shifts to this revision, once it is.
        loaded = await asyncio.to_thread(_load_index)
        if loaded is not None:
            state.index = loaded
            state.service = SearchService(loaded.identifier, loaded.availability)
        app.state.search = state
        yield
        if state.service is not None:
            state.service.close()

    app = FastAPI(
        title="Nomad Karaoke Search API",
        description="Song identification + karaoke availability for the Nomad Karaoke app",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/search/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )
    app.state.search = SearchState()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "https://decide.nomadkaraoke.com"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.include_router(build_router(RateLimiter(search_limits)))
    return app


search_app = create_search_app()
