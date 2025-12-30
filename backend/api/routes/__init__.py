"""API routes for Karaoke Decide."""

from fastapi import APIRouter

from backend.api.routes.auth import router as auth_router
from backend.api.routes.catalog import router as catalog_router
from backend.api.routes.health import router as health_router
from backend.api.routes.services import router as services_router

router = APIRouter()

# Include all route modules
router.include_router(health_router, tags=["health"])
router.include_router(auth_router, prefix="/auth", tags=["auth"])
router.include_router(catalog_router, prefix="/catalog", tags=["catalog"])
router.include_router(services_router, prefix="/services", tags=["services"])

# TODO: Add more routes as implemented
# router.include_router(my_songs_router, prefix="/my", tags=["my"])
# router.include_router(playlists_router, prefix="/playlists", tags=["playlists"])
