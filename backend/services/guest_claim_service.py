"""Migrate a guest decide profile onto a gen-authenticated user's profile."""

import logging
from dataclasses import dataclass
from typing import Any

from backend.services.auth_service import AuthenticationError, AuthService
from backend.services.firestore_service import FirestoreService
from karaoke_decide.core.models import User

logger = logging.getLogger(__name__)

USERS = "decide_users"

QUIZ_FIELDS = (
    "quiz_completed_at",
    "quiz_songs_known",
    "quiz_artists_known",
    "quiz_artist_mbids",
    "quiz_artist_affinities",
    "quiz_decade_pref",
    "quiz_decades",
    "quiz_energy_pref",
    "quiz_genres",
    "quiz_vocal_comfort_pref",
    "quiz_crowd_pleaser_pref",
    "quiz_manual_artists",
)


@dataclass
class ClaimResult:
    """Counts of what a claim moved onto the target profile."""

    songs_moved: int
    artists_moved: int
    services_moved: int
    playlists_moved: int
    quiz_copied: bool


class GuestClaimError(Exception):
    """A claim was refused; carries the HTTP status and a machine-readable detail."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _union_lists(target: list[Any], guest: list[Any]) -> list[Any]:
    """Target order first, then guest-only items (dicts compared by value)."""
    merged = list(target)
    merged.extend(item for item in guest if item not in merged)
    return merged


def _union_affinities(target: list[Any], guest: list[Any]) -> list[Any]:
    """Union affinity dicts by artist_name; the target's level wins on conflict."""
    seen = {a.get("artist_name") for a in target if isinstance(a, dict)}
    merged = list(target)
    for a in guest:
        if isinstance(a, dict) and a.get("artist_name") not in seen:
            merged.append(a)
            seen.add(a.get("artist_name"))
    return merged


def _earliest(a: Any, b: Any) -> Any:
    """Earliest of two ISO timestamps (either may be missing)."""
    if not a or not b:
        return a or b
    return min(a, b)


class GuestClaimService:
    """Moves a guest's songs, artists, services, playlists and quiz data to a verified user."""

    def __init__(self, firestore: FirestoreService, auth_service: AuthService):
        self.firestore = firestore
        self.auth_service = auth_service

    async def claim(self, target: User, guest_token: str) -> ClaimResult:
        guest_id = self._guest_id_from_token(guest_token)

        guest_doc = await self.firestore.get_document(USERS, guest_id)
        if guest_doc is None or not guest_doc.get("is_guest", False):
            raise GuestClaimError(404, "guest_not_found")

        target_doc = await self._target_doc(target)
        target_id = target.id

        songs_moved = await self._move_songs(guest_id, target_id)
        artists_moved = await self._move_prefixed("user_artists", guest_id, target_id, ":")
        services_moved = await self._move_prefixed("music_services", guest_id, target_id, "_")
        playlists_moved = await self._move_playlists(guest_id, target_id)
        quiz_copied = await self._merge_quiz(target_doc, guest_doc)
        await self._move_quiz_progress(guest_id, target_id)

        # The guest doc goes last: a failure part-way leaves the claim retryable.
        await self.firestore.delete_document(USERS, guest_id)

        logger.info(
            "Claimed guest %s onto %s: %d songs, %d artists, %d services, %d playlists, quiz_copied=%s",
            guest_id,
            target_id,
            songs_moved,
            artists_moved,
            services_moved,
            playlists_moved,
            quiz_copied,
        )
        return ClaimResult(songs_moved, artists_moved, services_moved, playlists_moved, quiz_copied)

    def _guest_id_from_token(self, guest_token: str) -> str:
        try:
            claims = self.auth_service.validate_jwt(guest_token)
        except AuthenticationError as e:
            raise GuestClaimError(400, "not_a_guest_token") from e
        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub.startswith("guest_"):
            raise GuestClaimError(400, "not_a_guest_token")
        return sub

    async def _target_doc(self, target: User) -> dict[str, Any]:
        doc = None
        if target.email:
            doc = await self.firestore.get_document(USERS, self.auth_service._hash_email(target.email))
        if doc is None:
            docs = await self.firestore.query_documents(USERS, filters=[("user_id", "==", target.id)], limit=1)
            doc = docs[0] if docs else None
        if doc is None:
            raise GuestClaimError(404, "target_not_found")
        return doc

    async def _move_songs(self, guest_id: str, target_id: str) -> int:
        guest_songs = await self.firestore.query_documents("user_songs", filters=[("user_id", "==", guest_id)])
        if not guest_songs:
            return 0
        existing = {
            d["id"]: d
            for d in await self.firestore.query_documents("user_songs", filters=[("user_id", "==", target_id)])
        }
        writes: dict[str, dict[str, Any]] = {}
        for song in guest_songs:
            old_id = song["id"]
            song_id = song.get("song_id") or old_id.split(":", 1)[-1]
            new_id = f"{target_id}:{song_id}"
            data = {k: v for k, v in song.items() if k != "id"}
            data["user_id"] = target_id
            prior = existing.get(new_id)
            if prior is not None:
                data = {k: v for k, v in prior.items() if k != "id"}
                data["play_count"] = prior.get("play_count", 0) + song.get("play_count", 0)
                data["created_at"] = _earliest(prior.get("created_at"), song.get("created_at"))
                if data["created_at"] is None:
                    del data["created_at"]
            writes[new_id] = data
        await self.firestore.batch_set("user_songs", writes)
        await self.firestore.batch_delete("user_songs", [s["id"] for s in guest_songs])
        return len(guest_songs)

    async def _move_prefixed(self, collection: str, guest_id: str, target_id: str, sep: str) -> int:
        """Move docs whose id is `{guest}{sep}rest` to `{target}{sep}rest`; the target's doc wins."""
        docs = await self.firestore.query_documents(collection, filters=[("user_id", "==", guest_id)])
        prefix = f"{guest_id}{sep}"
        writes: dict[str, dict[str, Any]] = {}
        for doc in docs:
            old_id = doc["id"]
            rest = old_id[len(prefix) :] if old_id.startswith(prefix) else old_id
            new_id = f"{target_id}{sep}{rest}"
            if await self.firestore.get_document(collection, new_id) is not None:
                continue
            data = {k: v for k, v in doc.items() if k != "id"}
            data["user_id"] = target_id
            writes[new_id] = data
        if writes:
            await self.firestore.batch_set(collection, writes)
        if docs:
            await self.firestore.batch_delete(collection, [d["id"] for d in docs])
        return len(writes)

    async def _move_playlists(self, guest_id: str, target_id: str) -> int:
        docs = await self.firestore.query_documents("playlists", filters=[("user_id", "==", guest_id)])
        for doc in docs:
            await self.firestore.update_document("playlists", doc["id"], {"user_id": target_id})
        return len(docs)

    async def _merge_quiz(self, target_doc: dict[str, Any], guest_doc: dict[str, Any]) -> bool:
        update: dict[str, Any] = {}
        copied = False
        if not target_doc.get("quiz_completed_at"):
            if guest_doc.get("quiz_completed_at"):
                update = {f: guest_doc[f] for f in QUIZ_FIELDS if guest_doc.get(f) is not None}
                copied = True
        else:
            for field in ("quiz_genres", "quiz_decades"):
                merged = _union_lists(target_doc.get(field) or [], guest_doc.get(field) or [])
                if merged != (target_doc.get(field) or []):
                    update[field] = merged
            merged_aff = _union_affinities(
                target_doc.get("quiz_artist_affinities") or [], guest_doc.get("quiz_artist_affinities") or []
            )
            if merged_aff != (target_doc.get("quiz_artist_affinities") or []):
                update["quiz_artist_affinities"] = merged_aff
        if update:
            await self.firestore.update_document(USERS, target_doc["id"], update)
        return copied

    async def _move_quiz_progress(self, guest_id: str, target_id: str) -> None:
        progress = await self.firestore.get_document("quiz_progress", guest_id)
        if progress is None:
            return
        if await self.firestore.get_document("quiz_progress", target_id) is None:
            data = {k: v for k, v in progress.items() if k != "id"}
            if "user_id" in data:
                data["user_id"] = target_id
            await self.firestore.set_document("quiz_progress", target_id, data)
        await self.firestore.delete_document("quiz_progress", guest_id)
