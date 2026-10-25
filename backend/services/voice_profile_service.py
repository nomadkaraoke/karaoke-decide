"""Voice profile: the singer's range and voice type, stored as a map on their decide_users doc.

Mic audio never reaches the server: the app sends only the derived numbers, and
``VoiceProfileIn`` forbids any extra key. Phase 3d will add ``method: 'session'`` and the
optional ``evidence_count`` / ``session_id`` fields (extend the Literal and add fields here).
``method: 'session'`` must be added both to ``VoiceProfileIn.method`` and to ``RecVoice.method`` in
``backend/services/recs_v2/service.py``. ``VoiceProfileOut`` (the read model) ignores unknown stored
keys, so a field written by a newer build never makes an older build treat the profile as unset.

``method: 'session'`` (Phase 3d) is a profile measured by a voice session: ``evidence_count`` is the
session's attempts with range evidence (at least 1) and ``session_id`` the session it came from.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from backend.services.firestore_service import FirestoreService

logger = logging.getLogger(__name__)

USERS_COLLECTION = "decide_users"
FIELD = "voice_profile"
MIC_FIELD = "voice_profile_mic"  # the last mic-test result, kept beside a session result (Phase 3d batch 1)

VoiceType = Literal["bass", "baritone", "tenor", "alto", "mezzo", "soprano"]
Experience = Literal["none", "some", "trained"]
UsesFalsetto = Literal["yes", "sometimes", "no", "unsure"]
Midi = Field(ge=24, le=96)


class VoiceProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    low_midi: int = Midi
    high_midi: int = Midi
    comfort_low: int = Midi
    comfort_high: int = Midi
    voice_type: VoiceType | None
    voice_type_between: VoiceType | None = None
    # Python name avoids shadowing BaseModel.register; JSON and Firestore key stay "register".
    register_: Literal["lower", "middle", "higher"] | None = Field(alias="register")
    confidence: Literal["low", "medium", "high"]
    method: Literal["mic", "self", "session"]
    measured_at: datetime
    evidence_count: int | None = Field(default=None, ge=0)
    session_id: str | None = Field(default=None, max_length=64)
    # Phase 3d batch 1, Part C (all optional; profiles stored before v0.33.0 read null). The falsetto range is
    # kept apart from the chest range (low_midi..high_midi), which never includes falsetto notes.
    falsetto_high: int | None = Field(default=None, ge=24, le=96)
    falsetto_low: int | None = Field(default=None, ge=24, le=96)
    experience: Experience | None = None  # singing background from the session setup
    uses_falsetto: UsesFalsetto | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if not (self.low_midi <= self.comfort_low <= self.comfort_high <= self.high_midi):
            raise ValueError("require low_midi <= comfort_low <= comfort_high <= high_midi")
        if self.falsetto_high is not None and self.falsetto_high < self.low_midi:
            raise ValueError("require falsetto_high >= low_midi")
        if self.falsetto_low is not None and self.falsetto_high is not None and self.falsetto_low > self.falsetto_high:
            raise ValueError("require falsetto_low <= falsetto_high")
        if (self.voice_type is None) == (self.register_ is None):
            raise ValueError("exactly one of voice_type / register must be set")
        if self.method == "self" and self.confidence != "low":
            raise ValueError("self-reported profiles must have confidence 'low'")
        if self.method == "session" and (self.evidence_count or 0) < 1:
            raise ValueError("session profiles need evidence_count >= 1")
        return self


class VoiceProfileOut(VoiceProfileIn):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    updated_at: datetime


class VoiceProfileUserNotFoundError(Exception):
    """The user has no decide_users doc to hold a profile."""


class VoiceProfileService:
    def __init__(self, firestore: FirestoreService) -> None:
        self.firestore = firestore

    async def _doc(self, user_id: str) -> tuple[dict[str, Any] | None, str | None]:
        """The decide_users doc for a user: guests are keyed by user_id, verified users by email hash."""
        if user_id.startswith("guest_"):
            doc = await self.firestore.get_document(USERS_COLLECTION, user_id)
            return (doc, user_id) if doc else (None, None)
        docs = await self.firestore.query_documents(USERS_COLLECTION, filters=[("user_id", "==", user_id)], limit=1)
        return (docs[0], docs[0]["id"]) if docs else (None, None)

    async def _read(self, user_id: str, field: str) -> VoiceProfileOut | None:
        doc, _ = await self._doc(user_id)
        raw = (doc or {}).get(field)
        if not raw:
            return None
        try:
            return VoiceProfileOut.model_validate(raw)
        except ValidationError:
            logger.warning("%s for %s no longer validates; treating as unset", field, user_id)
            return None

    async def get(self, user_id: str) -> VoiceProfileOut | None:
        return await self._read(user_id, FIELD)

    async def get_mic(self, user_id: str) -> VoiceProfileOut | None:
        """The last mic-test result (``voice_profile_mic``), whatever the current profile is. A mic result
        saved before v0.31.0 has no copy yet: then it is ``voice_profile`` itself, while that is a mic one."""
        doc, _ = await self._doc(user_id)
        if not (doc or {}).get(MIC_FIELD):
            current = await self._read(user_id, FIELD)
            return current if current is not None and current.method == "mic" else None
        return await self._read(user_id, MIC_FIELD)

    async def put(self, user_id: str, profile: VoiceProfileIn) -> VoiceProfileOut:
        """Overwrite the stored profile; the last explicit save wins, whatever its measured_at.

        A mic profile is also kept as ``voice_profile_mic``, so a later session result can sit beside it
        (and "Don't count this one" can fall back to it); other methods leave that copy alone."""
        doc, doc_id = await self._doc(user_id)
        if doc is None or doc_id is None:
            raise VoiceProfileUserNotFoundError(user_id)
        out = VoiceProfileOut(**profile.model_dump(by_alias=True), updated_at=datetime.now(UTC))
        stored = out.model_dump(mode="json", by_alias=True)
        fields: dict[str, Any] = {FIELD: stored}
        if out.method == "mic":
            fields[MIC_FIELD] = stored
        elif not doc.get(MIC_FIELD) and (doc.get(FIELD) or {}).get("method") == "mic":
            fields[MIC_FIELD] = doc[FIELD]  # a pre-v0.31.0 mic result had no copy: keep it before overwriting
        await self.firestore.update_document(USERS_COLLECTION, doc_id, fields)
        return out

    async def delete(self, user_id: str) -> None:
        """Clear the profile and the kept mic result."""
        _, doc_id = await self._doc(user_id)
        if doc_id is not None:
            await self.firestore.update_document(USERS_COLLECTION, doc_id, {FIELD: None, MIC_FIELD: None})
