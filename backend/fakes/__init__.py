"""DECIDE_FAKE_EXTERNALS: in-process fakes of ListenBrainz, MetaBrainz OAuth, the Spotify embed page,
Last.fm and Gemini, for the local stack (mobile Maestro flows). Mounted at ``/fake`` by
``backend.main`` only when the flag is on, and the app refuses to start with it in production.
Point the clients at them with LISTENBRAINZ_API_BASE, METABRAINZ_BASE_URL, SPOTIFY_EMBED_BASE and
LASTFM_API_BASE (``scripts/local-stack.sh`` does). State and knobs: ``backend.fakes.state``.
"""

from fastapi import APIRouter

from backend.fakes import lastfm, listenbrainz, metabrainz, spotify_embed


def build_router() -> APIRouter:
    router = APIRouter()
    router.include_router(listenbrainz.router, prefix="/listenbrainz/1")
    router.include_router(metabrainz.router, prefix="/metabrainz")
    router.include_router(spotify_embed.router, prefix="/spotify")
    router.include_router(lastfm.router, prefix="/lastfm")
    return router
