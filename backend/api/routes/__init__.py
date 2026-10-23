"""API routes for Karaoke Decide."""

from fastapi import APIRouter

from backend.api.routes.admin import router as admin_router
from backend.api.routes.admin_voice import router as admin_voice_router
from backend.api.routes.auth import router as auth_router
from backend.api.routes.catalog import router as catalog_router
from backend.api.routes.health import router as health_router
from backend.api.routes.internal import router as internal_router
from backend.api.routes.karaoke_log import partner_router as kjbox_partner_router
from backend.api.routes.karaoke_log import router as karaoke_log_router
from backend.api.routes.known_songs import router as known_songs_router
from backend.api.routes.my_data import router as my_data_router
from backend.api.routes.my_songs import router as my_songs_router
from backend.api.routes.playlists import router as playlists_router
from backend.api.routes.quiz import router as quiz_router
from backend.api.routes.recommendations import router as recommendations_router
from backend.api.routes.services import router as services_router
from backend.api.routes.voice import router as voice_router
from backend.api.routes.voice_session import router as voice_session_router

router = APIRouter()

# Include all route modules
router.include_router(health_router, tags=["health"])
router.include_router(auth_router, prefix="/auth", tags=["auth"])
router.include_router(catalog_router, prefix="/catalog", tags=["catalog"])
router.include_router(services_router, prefix="/services", tags=["services"])
router.include_router(quiz_router, prefix="/quiz", tags=["quiz"])
router.include_router(my_songs_router, prefix="/my/songs", tags=["my-songs"])
router.include_router(recommendations_router, prefix="/my", tags=["my"])
router.include_router(my_data_router, prefix="/my/data", tags=["my-data"])
router.include_router(playlists_router, prefix="/playlists", tags=["playlists"])
router.include_router(known_songs_router, prefix="/known-songs", tags=["known-songs"])
router.include_router(karaoke_log_router, prefix="/my/karaoke-log", tags=["karaoke-log"])
router.include_router(kjbox_partner_router, prefix="/partner/kjbox/karaoke-log", tags=["partner"])
router.include_router(voice_router, prefix="/my", tags=["voice"])
router.include_router(voice_session_router, prefix="/voice", tags=["voice-session"])
router.include_router(admin_router, prefix="/admin", tags=["admin"])
router.include_router(admin_voice_router, prefix="/admin/voice", tags=["admin-voice"])

# Internal routes (Cloud Tasks callbacks) - separate prefix outside /api
internal_api_router = internal_router
