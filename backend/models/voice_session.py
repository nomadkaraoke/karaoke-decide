"""Voice session API models (``/api/voice/*``).

Every request model forbids extra keys: the app sends quiz answers and an on-device pitch
summary, never audio or raw pitch frames.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.services.recs_v2.service import RecSong
from backend.services.voice_profile_service import VoiceProfileOut
from karaoke_decide.voice_session.select import ReasonType
from karaoke_decide.voice_session.stopping import MAX_ATTEMPTS, ConfidenceLabel, DoneReason, ImproveHint

Posture = Literal["standing", "sitting", "lying"]
Warmth = Literal["cold", "bit_warm", "warm"]
SessionStateName = Literal["active", "abandoned", "done"]
SkipReason = Literal["dont_know", "something_else", "not_embeddable"]
Feel = Literal["loved", "fine", "not_for_me"]
Position = Literal["too_low", "comfortable", "too_high", "both_ends"]
MidiFloat = Field(ge=24, le=96)


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionSetup(_Request):
    posture: Posture
    warmth: Warmth
    record: bool
    headphones: bool
    device: str | None = Field(default=None, max_length=80)


class SessionPatch(_Request):
    """The mid-session context chip: any subset of the setup."""

    posture: Posture | None = None
    warmth: Warmth | None = None
    record: bool | None = None
    headphones: bool | None = None
    device: str | None = Field(default=None, max_length=80)


class SessionCreated(BaseModel):
    session_id: str
    state: SessionStateName


class VersionOut(BaseModel):
    video_id: str
    brand_code: str
    brand_name: str
    # False only for the link-out offer (no embeddable version left): the app opens YouTube instead.
    embeddable: bool = True


class SongRangeOut(BaseModel):
    low: float
    high: float
    p10: float
    p50: float
    p90: float


class NextSongOut(BaseModel):
    kind: Literal["song"] = "song"
    song_key: str
    artist: str
    title: str
    duration_s: int | None
    reason_type: ReasonType
    version: VersionOut
    alternatives: list[VersionOut] = Field(max_length=2)
    song_range: SongRangeOut | None
    attempt_n: int  # the 1-based number an attempt on this song would get
    max_attempts: int = MAX_ATTEMPTS


class NextDoneOut(BaseModel):
    kind: Literal["done"] = "done"
    reason: DoneReason


class SkipIn(_Request):
    song_key: str = Field(max_length=500)
    reason: SkipReason
    video_id: str | None = Field(default=None, max_length=64)


class QuizIn(_Request):
    """Mirrors ``voice_session.model.Quiz``; Q1 ``feel`` and Q2 ``position`` are required."""

    feel: Feel
    position: Position
    difficulty: Literal["easy", "stretch", "couldnt"] | None = None
    knew: Literal["well", "partly", "barely"] | None = None
    would_sing: Literal["yes", "yes_other_key", "no"] | None = None
    would_work_if: list[Literal["warmer", "other_key", "other_octave", "practice", "fine"]] = Field(
        default_factory=list, max_length=5
    )
    voice_now: Literal["fresh", "warm", "tired"] | None = None
    version_quality: Literal["good", "not_great"] | None = None
    version_issues: list[Literal["no_intro", "wrong_lyrics", "bad_sync", "low_quality"]] = Field(
        default_factory=list, max_length=4
    )
    tags: list[Literal["fast_lyrics", "long_gaps", "needed_warmup", "crowd_song", "mood_only"]] = Field(
        default_factory=list, max_length=5
    )


class PitchSummaryIn(_Request):
    """On-device pitch summary (MIDI note numbers); no frames, no audio."""

    voiced_s: float = Field(ge=0, le=3600)
    held_count: int = Field(ge=0, le=10_000)
    held_low: float = MidiFloat
    held_high: float = MidiFloat
    p10: float = MidiFloat
    p50: float = MidiFloat
    p90: float = MidiFloat

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.held_low > self.held_high:
            raise ValueError("require held_low <= held_high")
        if not (self.p10 <= self.p50 <= self.p90):
            raise ValueError("require p10 <= p50 <= p90")
        return self


class AttemptIn(_Request):
    song_key: str = Field(max_length=500)
    video_id: str = Field(max_length=64)
    started_at: datetime
    ended_at: datetime
    stopped_early: bool
    quiz: QuizIn
    pitch: PitchSummaryIn | None = None

    @model_validator(mode="after")
    def _ended_after_start(self) -> Self:
        if self.ended_at < self.started_at:
            raise ValueError("require ended_at >= started_at")
        return self


class AttemptOut(BaseModel):
    attempt_id: str
    n: int
    done: bool
    done_reason: DoneReason | None
    top_p: float


class LovedSongOut(BaseModel):
    song_key: str
    artist: str
    title: str


class ResultOut(BaseModel):
    voice_type: str
    between: str | None
    confidence_label: ConfidenceLabel
    top_p: float
    low: int
    high: int
    comfort_low: int
    comfort_high: int
    attempts: int
    partial: bool
    improve_hint: ImproveHint | None
    done_reason: DoneReason | None
    profile_saved: bool
    suited: list[RecSong] = Field(max_length=15)
    loved: list[LovedSongOut]


class SessionOut(BaseModel):
    """A session as it stands (``GET /api/voice/sessions/{id}``), for the app to resume it."""

    session_id: str
    state: SessionStateName
    setup: SessionSetup
    attempts: int
    top_p: float
    done_reason: DoneReason | None
    # The song offered last and not yet sung or skipped (what the app should show), else null.
    last_offer: NextSongOut | None
    profile_saved: bool


class VoiceFlagsOut(BaseModel):
    session_enabled: bool
    is_admin: bool
    voice_data: bool
    active_session_id: str | None = None


# ---------------------------------------------------------------- your voice data (history, results)


class SessionResultSummaryOut(BaseModel):
    """A session's result as last computed (``voice_sessions.result_summary``): written by every
    ``GET .../result`` and by a "Don't count this one" recompute."""

    session_id: str
    voice_type: str
    between: str | None
    confidence_label: ConfidenceLabel
    top_p: float
    low: int
    high: int
    comfort_low: int
    comfort_high: int
    attempts: int
    evidence_attempts: int  # attempts with range evidence that count (excluded ones don't)
    profile_saved: bool
    computed_at: datetime


class HistoryRecordingOut(BaseModel):
    recording_id: str
    duration_s: float | None
    content_type: str
    bytes: int | None


class HistoryAttemptOut(BaseModel):
    attempt_id: str
    n: int
    song_key: str
    artist: str
    title: str
    sung_at: datetime | None
    feel: Feel | None
    position: Position | None
    excluded: bool = False
    recording: HistoryRecordingOut | None
    falsetto_notes: int = 0


class HistorySessionOut(BaseModel):
    session_id: str
    created_at: datetime | None
    state: SessionStateName
    result: SessionResultSummaryOut | None
    attempts: list[HistoryAttemptOut]


class VoiceHistoryOut(BaseModel):
    sessions: list[HistorySessionOut]


ProfileOutcome = Literal["unchanged", "updated", "reverted_to_mic", "removed"]


class AttemptPatchIn(_Request):
    """``PATCH /api/voice/attempts/{attempt_id}``: "Don't count this one" (and its undo)."""

    excluded: bool


class AttemptPatchOut(BaseModel):
    attempt_id: str
    excluded: bool
    result: SessionResultSummaryOut | None
    # What happened to the saved voice profile: re-derived ("updated"), back to the mic result
    # ("reverted_to_mic"), deleted ("removed"), or untouched (it isn't this session's).
    profile: ProfileOutcome


class VoiceProfileResponse(BaseModel):
    """``GET /api/my/voice``: the saved profile, the last mic-test result kept beside it, and the latest
    session result with evidence (saved or not), so Me can show both."""

    profile: VoiceProfileOut | None
    mic: VoiceProfileOut | None = None
    latest_session: SessionResultSummaryOut | None = None
