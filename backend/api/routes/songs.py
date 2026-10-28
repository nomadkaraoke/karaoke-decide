"""Song routes: ``GET /api/songs/{song_key}/crowd``, how other singers found a song (spec 6.7).

``song_key`` holds ``/``, ``|``, ``#`` ... so it is matched with the ``path`` converter and
decoded once from the raw path (``my_songs._song_key``); clients percent-encode it.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.api.deps import CurrentUser, get_firestore
from backend.services.crowd_stats import CROWD_COLLECTION, SongCrowd, crowd_doc_id, crowd_figure, voice_bucket
from backend.services.firestore_service import FirestoreService
from backend.services.voice_profile_service import FIELD as VOICE_PROFILE_FIELD
from backend.services.voice_profile_service import find_user_doc

logger = logging.getLogger(__name__)

router = APIRouter()

FirestoreDep = Annotated[FirestoreService, Depends(get_firestore)]

_MARKER = "/songs/"
_ACTION = "/crowd"


def _song_key(request: Request, song_key: str) -> str:
    """The exact song_key, percent-decoded once from the raw request path (see ``my_songs._song_key``)."""
    raw = request.scope.get("raw_path")
    if raw:
        text = raw.decode("latin-1").split("?", 1)[0]
        start = text.find(_MARKER)
        if start >= 0:
            body = text[start + len(_MARKER) :]
            if body.endswith(_ACTION):
                body = body[: -len(_ACTION)]
            song_key = unquote(body)
    if not song_key:
        raise HTTPException(status_code=404, detail="unknown_song")
    return song_key


@router.get("/{song_key:path}/crowd", response_model=SongCrowd)
async def song_crowd(request: Request, song_key: str, user: CurrentUser, firestore: FirestoreDep) -> SongCrowd:
    song_key = _song_key(request, song_key)
    bucket, stats = await asyncio.gather(
        _viewer_bucket(firestore, user.id), firestore.get_document(CROWD_COLLECTION, crowd_doc_id(song_key))
    )
    return crowd_figure(stats, bucket)


async def _viewer_bucket(firestore: FirestoreService, user_id: str) -> str | None:
    """The viewer's voice bucket; a failed lookup degrades to none (the overall figure still shows)."""
    try:
        doc, _ = await find_user_doc(firestore, user_id)
    except Exception:
        logger.warning("user doc lookup failed for %s; crowd figure without a bucket", user_id, exc_info=True)
        return None
    return voice_bucket((doc or {}).get(VOICE_PROFILE_FIELD))
