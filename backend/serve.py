"""Production ASGI entrypoint (Dockerfile CMD ``uvicorn backend.serve:app``).

Cold start: start the catalog.db + availability.db download *before* importing the app, so
it runs while Python imports FastAPI, the routes and their services, instead of after. The
lifespan (``backend.main``) then only waits for whatever is left of it. Everything else
(tests, the local stack) imports ``backend.main:app``, whose lifespan loads the catalog itself.
"""

from backend.logging_config import configure_logging

configure_logging()

from backend.config import get_backend_settings  # noqa: E402
from backend.services.catalog_store import get_catalog_store  # noqa: E402

get_catalog_store().start_load(get_backend_settings())

from backend.main import app  # noqa: E402

__all__ = ["app"]
