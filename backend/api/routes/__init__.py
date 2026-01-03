"""API routes for Karaoke Decide."""

from fastapi import APIRouter

from backend.api.routes.admin import router as admin_router
from backend.api.routes.auth import router as auth_router
from backend.api.routes.catalog import router as catalog_router
from backend.api.routes.health import router as health_router
from backend.api.routes.internal import router as internal_router
from backend.api.routes.known_songs import router as known_songs_router
from backend.api.routes.my_data import router as my_data_router
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
router.include_router(my_data_router, prefix="/my/data", tags=["my-data"])
router.include_router(playlists_router, prefix="/playlists", tags=["playlists"])
router.include_router(known_songs_router, prefix="/known-songs", tags=["known-songs"])
router.include_router(admin_router, prefix="/admin", tags=["admin"])

# Internal routes (Cloud Tasks callbacks) - separate prefix outside /api
internal_api_router = internal_router
