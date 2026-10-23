"""Fake ListenBrainz API (``/fake/listenbrainz/1``), shaped like the real responses the client reads."""

from __future__ import annotations

from collections import Counter
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request

from backend.fakes.state import STATE, FakeImport, FakeListen

router = APIRouter()


def _listens(username: str) -> list[FakeListen]:
    listens = STATE.lb_users.get(username)
    if listens is None:
        raise HTTPException(404, {"code": 404, "error": "Cannot find user: " + username})
    return listens


def _token_user(authorization: str | None) -> str:
    """The user an ``Authorization: Token <tok>`` header belongs to (tokens from fake MetaBrainz)."""
    scheme, _, token = (authorization or "").partition(" ")
    username = STATE.tokens.get(token.strip()) if scheme.lower() == "token" else None
    if username is None:
        raise HTTPException(401, {"code": 401, "error": "You need to provide an Authorization header."})
    return username


@router.get("/user/{username}/listen-count")
async def listen_count(username: str) -> dict[str, Any]:
    return {"payload": {"count": len(_listens(username))}}


@router.get("/user/{username}/listens")
async def listens(
    username: str,
    count: int = Query(25, ge=1),
    min_ts: int | None = None,
    max_ts: int | None = None,
) -> dict[str, Any]:
    """Like LB: listens strictly between the bounds, newest first. With only ``min_ts`` the page is the
    ``count`` listens just after it; otherwise the ``count`` newest ones."""
    rows = [x for x in _listens(username) if (min_ts is None or x.listened_at > min_ts)]
    rows = [x for x in rows if max_ts is None or x.listened_at < max_ts]
    rows.sort(key=lambda x: x.listened_at)
    page = rows[:count] if min_ts is not None and max_ts is None else rows[-count:]
    page.reverse()
    return {
        "payload": {
            "count": len(page),
            "user_id": username,
            "listens": [
                {
                    "listened_at": x.listened_at,
                    "track_metadata": {"artist_name": x.artist, "track_name": x.title, "additional_info": {}},
                }
                for x in page
            ],
        }
    }


@router.get("/stats/user/{username}/artists")
async def top_artists(username: str, count: int = Query(25, ge=1)) -> dict[str, Any]:
    counts = Counter(x.artist for x in _listens(username))
    return {
        "payload": {
            "artists": [
                {"artist_name": name, "listen_count": n, "artist_mbid": None} for name, n in counts.most_common(count)
            ]
        }
    }


@router.get("/stats/user/{username}/recordings")
async def top_recordings(username: str, count: int = Query(25, ge=1)) -> dict[str, Any]:
    counts = Counter((x.artist, x.title) for x in _listens(username))
    return {
        "payload": {
            "recordings": [
                {"artist_name": a, "track_name": t, "listen_count": n, "recording_mbid": None}
                for (a, t), n in counts.most_common(count)
            ]
        }
    }


@router.get("/validate-token")
async def validate_token(authorization: str | None = Header(None)) -> dict[str, Any]:
    """Like LB, MetaBrainz OAuth tokens are not LB user tokens; a ``fake-lb-<username>`` token for a
    seeded user is."""
    _, _, token = (authorization or "").partition(" ")
    username = token.strip().removeprefix("fake-lb-")
    if token.strip().startswith("fake-lb-") and username in STATE.lb_users:
        return {"code": 200, "message": "Token valid.", "valid": True, "user_name": username}
    return {"code": 200, "message": "Token invalid.", "valid": False}


@router.post("/import-listens/")
async def submit_import(request: Request, authorization: str | None = Header(None)) -> dict[str, Any]:
    username = _token_user(authorization)
    form = await request.form()
    upload = form.get("file")
    filename = str(getattr(upload, "filename", "") or "")
    if not filename.lower().endswith(".zip") or form.get("service") != "spotify":
        raise HTTPException(400, {"code": 400, "error": "Expected a .zip file and service=spotify."})
    import_id = str(STATE.next_id())
    STATE.imports[import_id] = FakeImport(username=username)
    return {"import_id": int(import_id), "service": "spotify", "status": {"state": "waiting"}}


@router.get("/import-listens/{import_id}/")
async def import_status(import_id: str, authorization: str | None = Header(None)) -> dict[str, Any]:
    """``in_progress`` on the first check, ``completed`` from the second on."""
    username = _token_user(authorization)
    job = STATE.imports.get(import_id)
    if job is None or job.username != username:
        raise HTTPException(404, {"code": 404, "error": "Import not found"})
    job.status_checks += 1
    status = "in_progress" if job.status_checks < 2 else "completed"
    return {
        "import_id": int(import_id),
        "service": "spotify",
        "metadata": {"status": status, "progress": "Importing" if status == "in_progress" else "Done"},
    }
