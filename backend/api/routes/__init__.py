"""API routes for Karaoke Decide."""

from fastapi import APIRouter

from backend.api.routes.auth import router as auth_router
from backend.api.routes.catalog import router as catalog_router
from backend.api.routes.health import router as health_router
from backend.api.routes.internal import router as internal_router
from backend.api.routes.playlists import router as playlists_router
from backend.api.routes.quiz import router as quiz_router
from backend.api.routes.recommendations import router as recommendations_router
from backend.api.routes.services import router as services_router

router = APIRouter()

# Include all route modules
router.include_router(health_router, tags=["health"])
router.include_router(auth_router, prefix="/auth", tags=["auth"])
router.include_router(catalog_router, prefix="/catalog", tags=["catalog"])
router.include_router(services_router, prefix="/services", tags=["services"])
router.include_router(quiz_router, prefix="/quiz", tags=["quiz"])
router.include_router(recommendations_router, prefix="/my", tags=["my"])
router.include_router(playlists_router, prefix="/playlists", tags=["playlists"])

# Internal routes (Cloud Tasks callbacks) - separate prefix outside /api
internal_api_router = internal_router
