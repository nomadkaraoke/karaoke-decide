"""In-memory state behind the ``/fake`` routes: knobs, seeded users, OAuth codes/tokens, imports.

One process-wide ``STATE``. ``reset()`` restores the defaults (no knobs, the seeded
``fixture-user`` on ListenBrainz and Last.fm); the test API (``PUT /api/test/fake-externals``)
calls it before every Maestro flow. Song data comes from the decide fixture catalog only.
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from typing import Any

FIXTURE_USER = "fixture-user"

# Knobs (``PUT /api/test/fake-externals {"knobs": {...}}``):
#   metabrainz=deny         /oauth2/authorize redirects with error=access_denied
#   metabrainz_user=<name>  the username MetaBrainz /oauth2/userinfo reports (default fixture-user)
#   spotify_embed=changed   the embed page no longer has the expected __NEXT_DATA__ shape (422)
#   screenshot=fail         FakeScreenshotExtractor raises (route answers 503)
KNOWN_KNOBS = frozenset({"metabrainz", "metabrainz_user", "spotify_embed", "screenshot"})


@dataclass
class FakeListen:
    artist: str
    title: str
    listened_at: int


@dataclass
class FakeLastFmUser:
    top_tracks: list[dict[str, Any]] = field(default_factory=list)  # {artist, title, playcount}
    top_artists: list[dict[str, Any]] = field(default_factory=list)  # {name, playcount}


@dataclass
class OAuthCode:
    challenge: str
    redirect_uri: str
    username: str


@dataclass
class FakeImport:
    username: str
    status_checks: int = 0


def _default_listens(now: int) -> list[FakeListen]:
    songs = [
        ("Muse", "Uprising"),
        ("Adele", "Hello"),
        ("Muse", "Uprising"),
        ("Queen", "Bohemian Rhapsody"),
        ("Toto", "Africa"),
    ]
    return [FakeListen(a, t, now - (i + 1) * 3600) for i, (a, t) in enumerate(songs)]


def _default_lastfm() -> FakeLastFmUser:
    return FakeLastFmUser(
        top_tracks=[
            {"artist": "Muse", "title": "Hysteria", "playcount": 12},
            {"artist": "Adele", "title": "Someone Like You", "playcount": 8},
            {"artist": "ABBA", "title": "Waterloo", "playcount": 5},
        ],
        top_artists=[
            {"name": "Muse", "playcount": 40},
            {"name": "Adele", "playcount": 20},
            {"name": "ABBA", "playcount": 9},
        ],
    )


class FakeState:
    def __init__(self) -> None:
        self.reset()

    def reset(self, knobs: dict[str, str] | None = None) -> None:
        self.knobs: dict[str, str] = dict(knobs or {})
        self.lb_users: dict[str, list[FakeListen]] = {FIXTURE_USER: _default_listens(int(time.time()))}
        self.lastfm_users: dict[str, FakeLastFmUser] = {FIXTURE_USER: _default_lastfm()}
        self.codes: dict[str, OAuthCode] = {}
        self.tokens: dict[str, str] = {}  # access token -> username
        self.refresh_tokens: dict[str, str] = {}  # refresh token -> username
        self.imports: dict[str, FakeImport] = {}
        self._ids = itertools.count(1)

    def next_id(self) -> int:
        return next(self._ids)

    def knob(self, name: str) -> str | None:
        return self.knobs.get(name)

    @property
    def metabrainz_user(self) -> str:
        return self.knobs.get("metabrainz_user") or FIXTURE_USER


STATE = FakeState()
