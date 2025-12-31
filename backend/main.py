"""FastAPI application for Karaoke Decide."""

import logging
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import internal_api_router, router
from backend.config import get_backend_settings

# Configure logging to output to stdout for Cloud Run
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:%(name)s:%(message)s",
    stream=sys.stdout,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler."""
    # Startup
    settings = get_backend_settings()
    print(f"Starting Karaoke Decide API ({settings.environment})")

    yield

    # Shutdown
    print("Shutting down Karaoke Decide API")


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
