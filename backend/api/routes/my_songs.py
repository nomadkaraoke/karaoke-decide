"""Song feedback routes: "Not for me" (hide) and "Love it" (manual known song).

``song_key`` holds ``/``, ``|``, ``#`` ... so it is matched with the ``path`` converter
followed by the literal action; clients percent-encode it (``encodeURIComponent``).
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from backend.api.deps import CurrentUser, get_catalog_store_dep, get_song_feedback_service
from backend.services.catalog_store import CatalogStore, CatalogUnavailable
from backend.services.song_feedback_service import FeedbackSong, SongFeedbackService

router = APIRouter()

FeedbackDep = Annotated[SongFeedbackService, Depends(get_song_feedback_service)]
StoreDep = Annotated[CatalogStore, Depends(get_catalog_store_dep)]


class FeedbackBody(BaseModel):
    artist: str | None = None
    title: str | None = None


class FeedbackSongsResponse(BaseModel):
    songs: list[FeedbackSong]


def _lookup(store: CatalogStore, song_key: str) -> tuple[str, str]:
    try:
        reader = store.require()
    except CatalogUnavailable:
        raise HTTPException(status_code=503, detail="warming_up", headers={"Retry-After": "30"}) from None
    row = reader.conn().execute("SELECT artist, title FROM songs WHERE song_key = ?", (song_key,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="unknown_song")
    return row["artist"] or "", row["title"] or ""


def _names(store: CatalogStore, song_key: str, body: FeedbackBody | None) -> tuple[str, str]:
    if body and body.artist and body.title:
        return body.artist, body.title
    return _lookup(store, song_key)


_MARKER = "/songs/"


def _song_key(request: Request, song_key: str) -> str:
    """The exact song_key, percent-decoded once from the raw request path.

    ``scope["path"]`` is already decoded by the server; some stacks (Starlette's TestClient)
    decode twice, which would corrupt keys like ``x/y|z%20``. The raw path is unambiguous.
    A trailing ``/hide`` or ``/love`` is stripped by the route; ``song_key`` is the fallback.
    """
    raw = request.scope.get("raw_path")
    if raw:
        text = raw.decode("latin-1").split("?", 1)[0]
        start = text.find(_MARKER)
        if start >= 0:
            body = text[start + len(_MARKER) :]
            for action in ("/hide", "/love"):
                if body.endswith(action):
                    body = body[: -len(action)]
                    break
            song_key = unquote(body)
    if not song_key:
        raise HTTPException(status_code=404, detail="unknown_song")
    return song_key


@router.get("/hidden", response_model=FeedbackSongsResponse)
async def list_hidden(user: CurrentUser, feedback: FeedbackDep) -> FeedbackSongsResponse:
    return FeedbackSongsResponse(songs=await feedback.list_hidden(user.id))


@router.get("/loved", response_model=FeedbackSongsResponse)
async def list_loved(user: CurrentUser, feedback: FeedbackDep) -> FeedbackSongsResponse:
    return FeedbackSongsResponse(songs=await feedback.list_loved(user.id))


@router.post("/{song_key:path}/hide", status_code=status.HTTP_204_NO_CONTENT)
async def hide_song(
    request: Request,
    song_key: str,
    user: CurrentUser,
    feedback: FeedbackDep,
    store: StoreDep,
    body: FeedbackBody | None = None,
) -> Response:
    song_key = _song_key(request, song_key)
    artist, title = _names(store, song_key, body)
    await feedback.hide(user.id, song_key, artist, title)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{song_key:path}/hide", status_code=status.HTTP_204_NO_CONTENT)
async def unhide_song(request: Request, song_key: str, user: CurrentUser, feedback: FeedbackDep) -> Response:
    song_key = _song_key(request, song_key)
    await feedback.unhide(user.id, song_key)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{song_key:path}/love", status_code=status.HTTP_204_NO_CONTENT)
async def love_song(
    request: Request,
    song_key: str,
    user: CurrentUser,
    feedback: FeedbackDep,
    store: StoreDep,
    body: FeedbackBody | None = None,
) -> Response:
    song_key = _song_key(request, song_key)
    artist, title = _names(store, song_key, body)
    await feedback.love(user.id, song_key, artist, title)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{song_key:path}/love", status_code=status.HTTP_204_NO_CONTENT)
async def unlove_song(request: Request, song_key: str, user: CurrentUser, feedback: FeedbackDep) -> Response:
    song_key = _song_key(request, song_key)
    await feedback.unlove(user.id, song_key)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
