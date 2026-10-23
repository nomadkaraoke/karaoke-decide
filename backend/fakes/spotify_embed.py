"""Fake Spotify embed page (``/fake/spotify/embed/playlist/{id}``).

Only ``FIXTURE_PLAYLIST_ID`` exists (other ids 404, like a private or missing playlist). Under
knob ``spotify_embed=changed`` the page keeps ``__NEXT_DATA__`` but moves the entity, so the
parser reports a format change (the route answers 422). Real playlist ids are 22 base62
characters, which the link parser enforces, so the fixture id is 22 characters too.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from backend.fakes.state import STATE

router = APIRouter()

FIXTURE_PLAYLIST_ID = "fixtureplaylist0000001"
FIXTURE_PLAYLIST_TITLE = "Fixture Karaoke Mix"
# Fixture-catalog songs; "Fixture Guest" shows a multi-artist subtitle (the first artist wins).
FIXTURE_TRACKS = [
    ("Hello", "Adele"),
    ("Uprising", "Muse,\xa0Fixture Guest"),
    ("Reptilia", "The Strokes"),
    ("Your Song", "Elton John"),
]


def _entity() -> dict[str, Any]:
    return {
        "type": "playlist",
        "name": FIXTURE_PLAYLIST_TITLE,
        "trackList": [
            {
                "uri": f"spotify:track:fixture{i}",
                "uid": f"u{i}",
                "title": title,
                "subtitle": subtitle,
                "isExplicit": False,
                "duration": 200000,
                "entityType": "track",
            }
            for i, (title, subtitle) in enumerate(FIXTURE_TRACKS)
        ],
    }


def fixture_html(changed: bool = False) -> str:
    state: dict[str, Any] = {"data": {"entity": _entity()}}
    if changed:
        state = {"data": {"playlistV2": _entity()}}
    next_data = json.dumps({"props": {"pageProps": {"state": state}}})
    return (
        '<!DOCTYPE html><html><head><title>Fixture</title></head><body><div id="__next"></div>'
        f'<script id="__NEXT_DATA__" type="application/json">{next_data}</script></body></html>'
    )


@router.get("/embed/playlist/{playlist_id}", response_class=HTMLResponse)
async def embed_playlist(playlist_id: str) -> HTMLResponse:
    if playlist_id != FIXTURE_PLAYLIST_ID:
        return HTMLResponse("<html><body>Page not found</body></html>", status_code=404)
    return HTMLResponse(fixture_html(changed=STATE.knob("spotify_embed") == "changed"))
