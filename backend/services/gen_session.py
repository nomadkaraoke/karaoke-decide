"""Validate karaoke-gen session tokens against gen's Firestore collections.

Mirrors karaoke-gen/backend/services/user_service.py ``validate_session``
(same Firestore project), plus one decide-only rule: tenant sessions are
rejected. gen tokens contain no dots; decide JWTs always do.
"""

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from backend.config import get_backend_settings
from backend.services.firestore_service import FirestoreService

logger = logging.getLogger(__name__)

GEN_SESSIONS_COLLECTION = "sessions"
GEN_USERS_COLLECTION = "gen_users"
GEN_SESSION_IDLE_DAYS = 7
SLIDE_INTERVAL_S = 3600
POSITIVE_TTL_S = 60
NEGATIVE_TTL_S = 10

Reason = Literal["invalid", "revoked", "expired", "idle", "tenant", "user_not_found", "user_disabled", "error"]


def is_gen_token(token: str) -> bool:
    """gen tokens (secrets.token_urlsafe) have no dots; decide JWTs always do."""
    return "." not in token


@dataclass(frozen=True)
class GenIdentity:
    email: str  # lowercased


class GenSessionError(Exception):
    def __init__(self, reason: Reason):
        super().__init__(reason)
        self.reason: Reason = reason


def _as_utc(value: datetime | str | None) -> datetime | None:
    """Parse a Firestore timestamp (datetime or ISO string); naive means UTC."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class GenSessionValidator:
    def __init__(
        self,
        firestore: FirestoreService,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self._fs = firestore
        self._now = now
        # sha256(token) -> (expires_at, result)
        self._cache: dict[str, tuple[datetime, GenIdentity | GenSessionError]] = {}
        # sha256(token) -> time of our last last_activity_at write
        self._last_write: dict[str, datetime] = {}

    def clear_cache(self) -> None:
        self._cache.clear()
        self._last_write.clear()

    async def validate(self, token: str) -> GenIdentity:
        key = _key(token)
        now = self._now()
        cached = self._cache.get(key)
        if cached and cached[0] > now:
            if isinstance(cached[1], GenSessionError):
                raise cached[1]
            return cached[1]

        try:
            identity = await self._validate_uncached(token, key, now)
        except GenSessionError as err:
            self._cache[key] = (now + timedelta(seconds=NEGATIVE_TTL_S), err)
            raise
        except Exception as exc:
            logger.exception("gen session validation failed")
            raise GenSessionError("error") from exc

        self._cache[key] = (now + timedelta(seconds=POSITIVE_TTL_S), identity)
        return identity

    async def _validate_uncached(self, token: str, key: str, now: datetime) -> GenIdentity:
        doc = await self._fs.get_document(GEN_SESSIONS_COLLECTION, token)
        if not doc:
            raise GenSessionError("invalid")
        if not doc.get("is_active", True):
            raise GenSessionError("revoked")

        expires_at = _as_utc(doc.get("expires_at"))
        if expires_at is None or now > expires_at:
            raise GenSessionError("expired")

        last_activity = _as_utc(doc.get("last_activity_at"))
        if last_activity is None or last_activity < now - timedelta(days=GEN_SESSION_IDLE_DAYS):
            raise GenSessionError("idle")

        if doc.get("tenant_id"):
            raise GenSessionError("tenant")

        email = str(doc.get("user_email") or "").lower()
        if not email:
            raise GenSessionError("invalid")

        user = await self._fs.get_document(GEN_USERS_COLLECTION, email)
        if not user:
            # Legacy hash-keyed user docs carry the email as a field.
            matches = await self._fs.query_documents(GEN_USERS_COLLECTION, filters=[("email", "==", email)], limit=1)
            user = matches[0] if matches else None
        if not user:
            raise GenSessionError("user_not_found")
        if not user.get("is_active", True):
            raise GenSessionError("user_disabled")

        await self._slide_window(token, key, now, last_activity)
        return GenIdentity(email=email)

    async def _slide_window(self, token: str, key: str, now: datetime, last_activity: datetime) -> None:
        """Write last_activity_at at most once per hour per token (best effort)."""
        last_write = self._last_write.get(key)
        newest = max(last_activity, last_write) if last_write else last_activity
        if (now - newest).total_seconds() < SLIDE_INTERVAL_S:
            return
        try:
            await self._fs.update_document(GEN_SESSIONS_COLLECTION, token, {"last_activity_at": now.isoformat()})
            self._last_write[key] = now
        except Exception:
            logger.warning("Failed to slide gen session window", exc_info=True)


_validator: GenSessionValidator | None = None


def get_gen_session_validator() -> GenSessionValidator:
    global _validator
    if _validator is None:
        _validator = GenSessionValidator(FirestoreService(get_backend_settings()))
    return _validator
