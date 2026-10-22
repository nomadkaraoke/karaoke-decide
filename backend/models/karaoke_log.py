"""Karaoke log: one record per song a Nomad Karaoke user sang (or planned / requested).

Written by kjbox server-to-server for Nomad nights (``kj_`` ids) and by the mobile app for
any other bar (``app_`` ids). Firestore collection ``karaoke_log``; timestamps are stored
as ISO strings. See the Phase 5 "Tonight" spec section 7.1.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

LogSource = Literal["kjbox", "app"]
LogState = Literal["planned", "requested", "sung", "sung_unplayed", "skipped"]
SongKeyStatus = Literal["resolved", "unresolved", "ambiguous"]

NOTES_MAX_CHARS = 2000


class Survey(BaseModel):
    """The optional post-song survey. Every answer is optional."""

    model_config = ConfigDict(extra="forbid")

    feel: Literal["great", "ok", "bad"] | None = None
    key: Literal["too_low", "right", "too_high"] | None = None
    crowd_pleaser: bool | None = None
    notes: str | None = Field(default=None, max_length=NOTES_MAX_CHARS)
    answered_at: datetime | None = None


class LogNight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    kind: Literal["nomad", "other"] | None = None
    venue_name: str | None = None
    date: str | None = None


class LogMedia(BaseModel):
    model_config = ConfigDict(extra="forbid")

    brand_code: str | None = None
    gen_job_id: str | None = None
    source_type: str | None = None


class KaraokeLogEntry(BaseModel):
    """A karaoke-log record as the API returns it.

    ``state`` is None only for a placeholder the app created by writing a survey to a
    ``kj_`` id before kjbox delivered the facts; ``list_for_user`` leaves those out.
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    user_id: str
    email_hash: str | None = None
    source: LogSource
    state: LogState | None = None
    artist: str = ""
    title: str = ""
    song_key: str | None = None
    song_key_status: SongKeyStatus = "unresolved"
    night: LogNight | None = None
    sung_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    survey: Survey | None = None
    media: LogMedia | None = None
    deleted: bool = False
    # kjbox extras (Nomad nights only)
    box_id: str | None = None
    entry_id: str | None = None
    singers: list[str] = Field(default_factory=list)


class KaraokeLogPage(BaseModel):
    entries: list[KaraokeLogEntry]
    next_cursor: str | None = None
