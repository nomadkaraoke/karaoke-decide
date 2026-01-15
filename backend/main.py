"""FastAPI application for Karaoke Decide."""

import logging
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import internal_api_router, router
from backend.config import get_backend_settings
from backend.services.catalog_lookup import get_catalog_lookup
from karaoke_decide.services.bigquery_catalog import BigQueryCatalogService

# Configure logging to output to stdout for Cloud Run
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:%(name)s:%(message)s",
    stream=sys.stdout,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler."""
    # Startup
    settings = get_backend_settings()
    logger.info(f"Starting Karaoke Decide API ({settings.environment})")

    # Pre-load karaoke catalog into memory for instant matching
    # This makes sync ~100x faster by avoiding BigQuery queries per track
    try:
        catalog_lookup = get_catalog_lookup()
        bigquery_service = BigQueryCatalogService()
        catalog_lookup.load_from_bigquery(bigquery_service)
        logger.info(f"Catalog loaded: {catalog_lookup.entry_count:,} songs ready for instant matching")
    except Exception as e:
        logger.error(f"Failed to load catalog: {e}")
        # Don't fail startup - sync will fall back to BigQuery queries

    yield

    # Shutdown
    logger.info("Shutting down Karaoke Decide API")


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

    return app


app = create_app()
