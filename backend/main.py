"""FastAPI application for Karaoke Decide."""

import asyncio
import importlib
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import internal_api_router, router
from backend.config import BackendSettings, get_backend_settings
from backend.logging_config import configure_logging
from backend.services.catalog_store import get_catalog_store
from backend.services.ingest_executor import log_pool_sizes
from karaoke_decide.core.lazy_import import preload

configure_logging()

logger = logging.getLogger(__name__)

# The lifespan's outer bound on the catalog load, beyond the store's own budget.
LIFESPAN_SLACK_S = 5.0

# Client libraries the services import lazily (cold start). The lifespan imports them in a
# thread alongside the catalog load, so the first requests don't pay for them.
PRELOAD_MODULES = (
    "google.cloud.firestore",
    "google.api_core.exceptions",
    "jose.jwt",
    "google.cloud.tasks_v2",
    "google.protobuf.timestamp_pb2",
)
PRELOAD_WAIT_S = 20.0


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler."""
    # Startup
    settings = get_backend_settings()
    logger.info(f"Starting Karaoke Decide API ({settings.environment})")
    log_pool_sizes()

    preloading = preload(PRELOAD_MODULES)

    # catalog.db + availability.db for recommendations v2 and sync matching. Loaded before
    # the port opens (Cloud Run throttles CPU after), behind the /api/ready startup probe;
    # in production backend.serve already started the download before importing this app.
    # A missing, corrupt or hung catalog never blocks startup: the store stays warming_up.
    store = get_catalog_store()
    try:
        await asyncio.wait_for(
            asyncio.to_thread(store.load_at_startup, settings), settings.catalog_load_timeout_s + LIFESPAN_SLACK_S
        )
    except TimeoutError:
        logger.warning("Recommendations catalog load overran its startup budget; starting warming_up")
    # Usually done by now (it ran alongside the catalog load); bounded so it can't hold startup.
    await asyncio.to_thread(preloading.join, PRELOAD_WAIT_S)

    yield

    # Shutdown
    logger.info("Shutting down Karaoke Decide API")
    store.close()


def _mount_test_support(app: FastAPI, settings: BackendSettings) -> None:
    """Task C1's local-stack test API: only with DECIDE_TEST_API on, never in production."""
    if not getattr(settings, "decide_test_api", False) or settings.is_production:
        return
    test_support = importlib.import_module("backend.api.routes.test_support")
    app.include_router(test_support.router, prefix="/api/test", tags=["test"])


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_backend_settings()

    app = FastAPI(
        title="Nomad Karaoke Decide API",
        description="Help people discover and choose the perfect karaoke songs",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs" if not settings.is_production else None,
        redoc_url="/api/redoc" if not settings.is_production else None,
    )

    # CORS configuration
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "https://decide.nomadkaraoke.com",
            "https://status.nomadkaraoke.com",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include API routes
    app.include_router(router, prefix="/api")

    # Include internal routes (for Cloud Tasks callbacks)
    app.include_router(internal_api_router, prefix="/internal", tags=["internal"])

    _mount_test_support(app, settings)

    return app


app = create_app()
