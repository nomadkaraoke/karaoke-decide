"""The search service: a separate scale-to-zero Cloud Run app (spec §5.1, §5.5).

Run with ``uvicorn backend.search.app:search_app --host 0.0.0.0 --port 8000``.
It is NOT mounted into ``backend/main.py``.

Probes:
  GET /api/search/health  always 200 and never touches the index: the 60 s uptime
                          ping that keeps an instance warm, and the app's warm-up ping.
  GET /api/search/ready   200 only once both indexes are loaded, else 503. The Cloud
                          Run startup probe uses this one, so a revision whose index
                          failed to load never takes traffic.

Environment:
  SONG_INDEX_LOCAL_DIR             an already-built index dir; skips the download (local stack).
                                   Also mounts GET /api/search/_local/offline.db.gz (never in prod)
  SONG_INDEX_RUN                   published "<run>-<token>" dir to load (empty → latest.json)
  SONG_INDEX_BUCKET                bucket holding song-index/ (default nomadkaraoke-kn-data)
  SONG_INDEX_DIR                   where downloads go (default /tmp/song-index; in-memory on Cloud Run)
  SONG_INDEX_OFFLINE_URL_TEMPLATE  public offline.db.gz URL, with {run}
  GEN_API_URL                      gen base URL for the resolve fallback (default https://api.nomadkaraoke.com)
  DECIDE_GEN_PARTNER_SECRET        partner secret for gen's resolve; unset → /resolve answers {}
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

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from backend.search import index_loader
from backend.search.events import configure_logging, log_event
from backend.search.models import (
    Health,
    IndexInfo,
    OfflineIndexManifest,
    ResolveResponse,
    SearchEvent,
    SearchResponse,
    SongDetail,
    SongGroup,
)
from backend.search.ratelimit import SEARCH_LIMITS, RateLimiter, RateLimits, device_id, rate_limit
from backend.search.resolve import RESOLVE_LIMITS, GenResolver
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
    resolver: GenResolver | None = None


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


def build_router(limiter: RateLimiter, resolve_limiter: RateLimiter) -> APIRouter:
    router = APIRouter(prefix="/api/search")
    limited = [Depends(rate_limit(limiter))]
    resolve_limited = [Depends(rate_limit(resolve_limiter))]

    @router.get("/health", response_model=Health)
    def health() -> Health:
        # Warm-up ping + uptime check: must not touch the index.
        return Health(ok=True)

    @router.get("/ready", response_model=Health, responses={503: {"description": "Index not loaded"}})
    def ready(request: Request) -> Health:
        # Startup probe: ready only when both databases are open.
        state = _state(request)
        if state.service is None or state.index is None:
            raise HTTPException(status_code=503, detail="Search index not loaded")
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

    @router.get(
        "/resolve",
        response_model=ResolveResponse,
        response_model_exclude_unset=True,
        dependencies=resolve_limited,
    )
    async def resolve(
        request: Request,
        q: str = "",
        sid: str | None = Query(default=None, max_length=MAX_SID_CHARS),
    ) -> ResolveResponse:
        """Gemini fallback via gen (spec §6). ``{}`` whenever there is nothing to offer."""
        resolver = _state(request).resolver
        started = time.perf_counter()
        if resolver is None:
            response, kind = ResolveResponse(), "skipped"
        else:
            response, kind = await resolver.resolve(q)
        log_event(
            "resolve",
            sid=sid,
            device=device_id(request),
            q=q[:200],
            kind=kind,
            ms=round((time.perf_counter() - started) * 1000, 1),
        )
        return response

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


def build_local_router(index_dir: Path) -> APIRouter:
    """Serves the local index's ``offline.db.gz``, standing in for the public bucket the
    offline manifest points at in production. Mounted only when SONG_INDEX_LOCAL_DIR is set."""
    router = APIRouter(prefix="/api/search/_local", include_in_schema=False)

    @router.get(f"/{index_loader.OFFLINE_FILE}")
    def local_offline_file() -> FileResponse:
        path = index_dir / index_loader.OFFLINE_FILE
        if not path.is_file():
            raise HTTPException(status_code=404, detail="No offline index in the local index dir")
        return FileResponse(path, media_type="application/gzip")

    return router


def create_search_app(
    *,
    search_limits: RateLimits = SEARCH_LIMITS,
    resolve_limits: RateLimits = RESOLVE_LIMITS,
    gen_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        state = SearchState(
            offline_url_template=os.environ.get("SONG_INDEX_OFFLINE_URL_TEMPLATE", "").strip()
            or DEFAULT_OFFLINE_URL_TEMPLATE
        )
        # Blocking startup until the index is in; Cloud Run's startup probe (/ready)
        # passes, and traffic shifts to this revision, only once it loaded.
        loaded = await asyncio.to_thread(_load_index)
        if loaded is not None:
            state.index = loaded
            state.service = SearchService(loaded.identifier, loaded.availability)
        state.resolver = GenResolver(state.service, transport=gen_transport)
        app.state.search = state
        yield
        await state.resolver.aclose()
        if state.service is not None:
            state.service.close()

    app = FastAPI(
        title="Nomad Karaoke Search API",
        description="Song identification + karaoke availability for the Nomad Karaoke app",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/search/docs",
        redoc_url=None,
        openapi_url="/api/search/openapi.json",
    )
    app.state.search = SearchState()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "https://decide.nomadkaraoke.com"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.include_router(build_router(RateLimiter(search_limits), RateLimiter(resolve_limits)))
    local_dir = os.environ.get("SONG_INDEX_LOCAL_DIR", "").strip()
    if local_dir:  # local stack only: never mounted in production
        app.include_router(build_local_router(Path(local_dir)))
    return app


search_app = create_search_app()
