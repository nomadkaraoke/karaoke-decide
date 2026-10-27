"""Personal voice pool API models (``/api/voice/personal-pool``, ``/api/admin/voice/costs``).

Refusals are ``HTTPException`` details shaped ``{"reason": ..., ...}`` (see ``PersonalPoolError``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

EligibilityReason = Literal["paid", "history", "admin", "not_eligible"]
BatchStatus = Literal["queued", "running", "done"]


class EligibilityOut(BaseModel):
    eligible: bool
    reason: EligibilityReason
    listens: int  # sum of user_songs playcounts
    songs_with_plays: int  # user_songs rows with playcount >= 1


class PersonalBatchCreated(BaseModel):
    batch_id: str
    songs: list[str]  # song keys in the batch (newly queued, plus any queued by a concurrent batch)
    est_cost_usd: float  # songs newly queued here x the per-song estimate


class PersonalBatchOut(BaseModel):
    batch_id: str
    requested_at: datetime
    total: int
    ready: int  # analysed and in the current voice.db
    status: BatchStatus


class PersonalPoolOut(BaseModel):
    eligible: bool
    reason: EligibilityReason
    listens: int  # as EligibilityOut (history needs >= 1,000 listens or >= 200 songs played)
    songs_with_plays: int
    next_allowed_at: datetime | None  # set only while the cap is used up (never for admins)
    cap: int
    used: int  # songs requested in the rolling window
    batches: list[PersonalBatchOut]  # newest first


class VoiceCostsOut(BaseModel):
    month: str  # UTC YYYY-MM
    personal_batches: int
    personal_songs: int
    est_cost_usd: float
    budget_usd: float
    paused: bool  # one more song would exceed the budget
    shared_songs_analysed: int  # shared + Andrew's sheet songs analysed (all time)
    est_cost_per_song_usd: float
