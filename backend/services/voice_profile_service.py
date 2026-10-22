"""Voice profile: the singer's range and voice type, stored as a map on their decide_users doc.

Mic audio never reaches the server: the app sends only the derived numbers, and
``VoiceProfileIn`` forbids any extra key. Phase 3d will add ``method: 'session'`` and the
optional ``evidence_count`` / ``session_id`` fields (extend the Literal and add fields here).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.services.firestore_service import FirestoreService

USERS_COLLECTION = "decide_users"
FIELD = "voice_profile"

VoiceType = Literal["bass", "baritone", "tenor", "alto", "mezzo", "soprano"]
Midi = Field(ge=24, le=96)


class VoiceProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    low_midi: int = Midi
    high_midi: int = Midi
    comfort_low: int = Midi
    comfort_high: int = Midi
    voice_type: VoiceType | None
    voice_type_between: VoiceType | None = None
    register: Literal["lower", "middle", "higher"] | None
    confidence: Literal["low", "medium", "high"]
    method: Literal["mic", "self"]
    measured_at: datetime

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if not (self.low_midi <= self.comfort_low <= self.comfort_high <= self.high_midi):
            raise ValueError("require low_midi <= comfort_low <= comfort_high <= high_midi")
        if (self.voice_type is None) == (self.register is None):
            raise ValueError("exactly one of voice_type / register must be set")
        if self.method == "self" and self.confidence != "low":
            raise ValueError("self-reported profiles must have confidence 'low'")
        return self


class VoiceProfileOut(VoiceProfileIn):
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

    async def get(self, user_id: str) -> VoiceProfileOut | None:
        doc, _ = await self._doc(user_id)
        raw = (doc or {}).get(FIELD)
        return VoiceProfileOut.model_validate(raw) if raw else None

    async def put(self, user_id: str, profile: VoiceProfileIn) -> VoiceProfileOut:
        """Overwrite the stored profile; the last explicit save wins, whatever its measured_at."""
        _, doc_id = await self._doc(user_id)
        if doc_id is None:
            raise VoiceProfileUserNotFoundError(user_id)
        out = VoiceProfileOut(**profile.model_dump(), updated_at=datetime.now(UTC))
        await self.firestore.update_document(USERS_COLLECTION, doc_id, {FIELD: out.model_dump(mode="json")})
        return out

    async def delete(self, user_id: str) -> None:
        _, doc_id = await self._doc(user_id)
        if doc_id is not None:
            await self.firestore.update_document(USERS_COLLECTION, doc_id, {FIELD: None})
