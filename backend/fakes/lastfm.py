"""Fake Last.fm API (``/fake/lastfm/2.0/?method=...``) for the user methods the sync reads.

Seeded users have top tracks and top artists only: no loved tracks and no scrobbles.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from backend.fakes.state import STATE, FakeLastFmUser

router = APIRouter()

ERR_INVALID_METHOD = 3
ERR_INVALID_PARAMS = 6  # Last.fm answers an unknown user with error 6 "User not found"


def _error(code: int, message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": code, "message": message}, status_code=status)


def _page(items: list[dict[str, Any]], limit: int, page: int) -> tuple[list[dict[str, Any]], dict[str, str]]:
    start = (page - 1) * limit
    total_pages = max(1, -(-len(items) // limit))
    attr = {"page": str(page), "perPage": str(limit), "totalPages": str(total_pages), "total": str(len(items))}
    return items[start : start + limit], attr


def _top_tracks(user: FakeLastFmUser) -> list[dict[str, Any]]:
    return [
        {"name": t["title"], "playcount": str(t.get("playcount", 1)), "artist": {"name": t["artist"]}}
        for t in user.top_tracks
    ]


def _top_artists(user: FakeLastFmUser) -> list[dict[str, Any]]:
    return [{"name": a["name"], "playcount": str(a.get("playcount", 1))} for a in user.top_artists]


@router.get("/2.0/", response_model=None)
async def api(
    method: str,
    user: str = "",
    limit: int = Query(50, ge=1),
    page: int = Query(1, ge=1),
) -> dict[str, Any] | JSONResponse:
    method = method.lower()
    data = STATE.lastfm_users.get(user)
    if data is None:
        return _error(ERR_INVALID_PARAMS, "User not found", 404)
    if method == "user.getinfo":
        plays = sum(int(t.get("playcount", 1)) for t in data.top_tracks)
        return {"user": {"name": user, "playcount": str(plays), "realname": "", "url": ""}}
    if method == "user.gettoptracks":
        items, attr = _page(_top_tracks(data), limit, page)
        return {"toptracks": {"track": items, "@attr": {"user": user, **attr}}}
    if method == "user.gettopartists":
        items, attr = _page(_top_artists(data), limit, page)
        return {"topartists": {"artist": items, "@attr": {"user": user, **attr}}}
    if method == "user.getlovedtracks":
        return {"lovedtracks": {"track": [], "@attr": {"user": user, **_page([], limit, page)[1]}}}
    if method == "user.getrecenttracks":
        return {"recenttracks": {"track": [], "@attr": {"user": user, **_page([], limit, page)[1]}}}
    return _error(ERR_INVALID_METHOD, "Invalid Method - No method with that name in this package", 400)
