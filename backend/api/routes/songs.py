"""Song routes: ``GET /api/songs/{song_key}/crowd``, how other singers found a song (spec 6.7).

``song_key`` holds ``/``, ``|``, ``#`` ... so it is matched with the ``path`` converter and
decoded once from the raw path (``my_songs._song_key``); clients percent-encode it.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.api.deps import CurrentUser, get_firestore
from backend.services.crowd_stats import CROWD_COLLECTION, SongCrowd, crowd_figure, voice_bucket
from backend.services.firestore_service import FirestoreService
from backend.services.voice_profile_service import FIELD as VOICE_PROFILE_FIELD
from backend.services.voice_profile_service import find_user_doc

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
    doc, _ = await find_user_doc(firestore, user.id)
    bucket = voice_bucket((doc or {}).get(VOICE_PROFILE_FIELD))
    stats = None if "/" in song_key else await firestore.get_document(CROWD_COLLECTION, song_key)
    return crowd_figure(stats, bucket)
