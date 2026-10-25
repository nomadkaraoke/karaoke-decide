"""Private voice recordings (``/api/voice/recordings``).

Objects live in the private recordings bucket as ``voice-recordings/<user_id>/<session_id>/<n>.<ext>``
(``.m4a`` for ``audio/mp4``, ``.wav`` for ``audio/wav``); ``voice_recordings/{session_id}:{n}`` documents
index them. ``gcs_path`` is the plain object name, the form B2's admin audio endpoint accepts.

The bucket has soft delete off, so deleting an object is real erasure. ``delete_all_for_user`` is the
deletion contract: the route uses it for "Delete my recordings" and Phase 6's ``DELETE /api/users/me`` must
call it (docs/ARCHITECTURE.md § Personal data).

Storage is the small slice of the GCS bucket API used here (``blob``, ``list_blobs``; blobs with
``upload_from_file``, ``open``, ``delete``, ``metadata``), so tests and the local stack
(``voice_recordings_local_dir``, :class:`LocalBucket`) share the code path with production.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from backend.services.firestore_service import FirestoreService
from backend.services.voice_session_service import ATTEMPTS_COLLECTION, SESSIONS_COLLECTION
from backend.services.voice_session_service import RECORDINGS_COLLECTION as RECORDINGS_COLLECTION  # re-export

logger = logging.getLogger(__name__)

PURGES_COLLECTION = "voice_recording_purges"
RECORDINGS_PREFIX = "voice-recordings/"
MAX_BYTES = 20 * 1024 * 1024
CONTENT_TYPES = {"audio/mp4": "m4a", "audio/wav": "wav"}
DELETED_MARKER = "recording_deleted_at"  # on voice_attempts: the owner deleted this attempt's recording
AUDIO_CHUNK = 256 * 1024  # streamed playback reads the object this much at a time
_COPY = 1024 * 1024


class RecordingError(Exception):
    status_code = 400
    detail = "bad_request"


class RecordingForbiddenError(RecordingError):
    status_code, detail = 403, "session_forbidden"


class RecordingNotConsentedError(RecordingError):
    status_code, detail = 403, "recording_not_consented"


class AttemptNotFoundError(RecordingError):
    status_code, detail = 404, "attempt_not_found"


class UnsupportedMediaError(RecordingError):
    status_code, detail = 415, "unsupported_media_type"


class RecordingTooLargeError(RecordingError):
    status_code, detail = 413, "recording_too_large"


class RecordingsDeletedError(RecordingError):
    """The user deleted all recordings while this upload was in flight; the upload undid itself."""

    status_code, detail = 409, "recordings_deleted"


class RecordingDeletedError(RecordingError):
    """The user deleted this attempt's recording ("Delete this recording"); a queued or in-flight
    upload of it is refused, or undoes itself, so it can't come back."""

    status_code, detail = 409, "recording_deleted"


class EmptyRecordingError(RecordingError):
    status_code, detail = 422, "empty_recording"


class RecordingNotFoundError(RecordingError):
    """Not the user's recording, no such recording, or its object is gone: always 404 (never 403)."""

    status_code, detail = 404, "recording_not_found"


class RecordingObjectMissingError(Exception):
    """The recording's object is not in the bucket."""


def user_prefix(user_id: str) -> str:
    if not user_id or "/" in user_id or ".." in user_id:
        raise ValueError("invalid user id")
    return f"{RECORDINGS_PREFIX}{user_id}/"


def _is_not_found(exc: BaseException) -> bool:
    return isinstance(exc, FileNotFoundError) or getattr(exc, "code", None) == 404


def recording_object_name(gcs_path: str, bucket_name: str) -> str | None:
    """The object name inside the recordings bucket, or None for anything outside it
    (another bucket, outside ``voice-recordings/``, or a ``..`` segment)."""
    if gcs_path.startswith("gs://"):
        bucket, _, name = gcs_path[len("gs://") :].partition("/")
        if bucket != bucket_name:
            return None
    else:
        name = gcs_path
    if not name.startswith(RECORDINGS_PREFIX) or ".." in name.split("/"):
        return None
    return name


async def open_recording(bucket: Any, name: str) -> Iterator[bytes]:
    """The object's bytes in ``AUDIO_CHUNK`` pieces. The first chunk is read here, so a missing object
    raises :class:`RecordingObjectMissingError` before any response starts; the rest stream lazily."""
    blob = bucket.blob(name)

    def first_chunk() -> tuple[Any, bytes]:
        f = blob.open("rb")
        try:
            return f, f.read(AUDIO_CHUNK)
        except BaseException:
            f.close()
            raise

    try:
        src, head = await asyncio.to_thread(first_chunk)
    except Exception as exc:
        if _is_not_found(exc):
            raise RecordingObjectMissingError(name) from exc
        raise

    def chunks() -> Iterator[bytes]:
        try:
            yield head
            while chunk := src.read(AUDIO_CHUNK):
                yield chunk
        finally:
            src.close()

    return chunks()


# ---------------------------------------------------------------- local-dir bucket (local stack)


class LocalBlob:
    def __init__(self, bucket: LocalBucket, name: str) -> None:
        self.bucket, self.name = bucket, name
        self.metadata: dict[str, str] | None = None

    @property
    def _path(self) -> Path:
        return self.bucket.path_for(self.name)

    def upload_from_file(self, fileobj: IO[bytes], content_type: str | None = None, **_: Any) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("wb") as out:
            while chunk := fileobj.read(_COPY):
                out.write(chunk)

    def open(self, mode: str = "rb", **_: Any) -> IO[bytes]:
        return self._path.open(mode)

    def delete(self) -> None:
        self._path.unlink()


class LocalBucket:
    """The GCS bucket API over a directory (``voice_recordings_local_dir``)."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def path_for(self, name: str) -> Path:
        p = (self.root / name).resolve()
        if not p.is_relative_to(self.root):
            raise ValueError("object name escapes the recordings directory")
        return p

    def blob(self, name: str) -> LocalBlob:
        return LocalBlob(self, name)

    def list_blobs(self, prefix: str = "") -> Iterator[LocalBlob]:
        if not self.root.exists():
            return
        for p in sorted(self.root.rglob("*")):
            name = p.relative_to(self.root).as_posix()
            if p.is_file() and name.startswith(prefix):
                yield LocalBlob(self, name)


# ---------------------------------------------------------------- service


class VoiceRecordingService:
    def __init__(self, firestore: FirestoreService, bucket: Any) -> None:
        self._fs = firestore
        self._bucket = bucket

    async def upload(
        self,
        user_id: str,
        *,
        session_id: str,
        attempt_n: int,
        duration_s: float,
        device: str | None,
        content_type: str,
        file: IO[bytes],
    ) -> str:
        """Store one attempt's audio and return the recording id. ``file`` is a seekable binary stream."""
        started = datetime.now(UTC)
        ext = CONTENT_TYPES.get(content_type)
        if ext is None:
            raise UnsupportedMediaError(content_type)
        session = await self._fs.get_document(SESSIONS_COLLECTION, session_id)
        if not session or session.get("user_id") != user_id:
            raise RecordingForbiddenError(session_id)
        if (session.get("setup") or {}).get("record") is not True:
            raise RecordingNotConsentedError(session_id)
        attempt = await self._fs.get_document(ATTEMPTS_COLLECTION, f"{session_id}:{attempt_n}")
        if not attempt or attempt.get("user_id") != user_id or attempt.get("session_id") != session_id:
            raise AttemptNotFoundError(f"{session_id}:{attempt_n}")
        if attempt.get(DELETED_MARKER):
            logger.warning("voice upload refused: recording %s:%s was deleted by its owner", session_id, attempt_n)
            raise RecordingDeletedError(f"{session_id}:{attempt_n}")

        size = await asyncio.to_thread(_measure, file)  # streams in chunks; raises when over the cap
        if size == 0:
            raise EmptyRecordingError(session_id)

        recording_id = f"{session_id}:{attempt_n}"
        name = f"{user_prefix(user_id)}{session_id}/{attempt_n}.{ext}"
        context = attempt.get("context") or {}
        metadata = {
            k: str(v)
            for k, v in {
                "song_key": attempt.get("song_key"),
                "video_id": attempt.get("video_id"),
                "posture": context.get("posture"),
                "warmth": context.get("warmth"),
                "headphones": context.get("headphones"),
                "device": device if device is not None else context.get("device"),
            }.items()
            if v is not None
        }
        previous = await self._fs.get_document(RECORDINGS_COLLECTION, recording_id)

        def put() -> None:
            blob = self._bucket.blob(name)
            blob.metadata = metadata
            blob.upload_from_file(file, content_type=content_type, rewind=True)
            old = previous.get("gcs_path") if previous else None
            if old and old != name and old.startswith(user_prefix(user_id)):  # same attempt, other format
                try:
                    self._delete_blob(old)
                except Exception:  # best effort: the next delete-all removes the orphan
                    logger.warning("could not remove replaced recording object %s", old, exc_info=True)

        await asyncio.to_thread(put)
        await self._fs.set_document(
            RECORDINGS_COLLECTION,
            recording_id,
            {
                "user_id": user_id,
                "session_id": session_id,
                "attempt_n": attempt_n,
                "gcs_path": name,
                "content_type": content_type,
                "bytes": size,
                "duration_s": duration_s,
                "created_at": datetime.now(UTC),
            },
        )
        attempt_id = f"{session_id}:{attempt_n}"
        await self._fs.update_document(ATTEMPTS_COLLECTION, attempt_id, {"recording_id": recording_id})

        # A delete-all that ran while this upload was in flight may have missed the object, so undo it.
        purge = await self._fs.get_document(PURGES_COLLECTION, user_id)
        purged_at = _as_utc(purge.get("purged_at")) if purge else None
        # delete-all withdraws consent before it lists objects, so consent gone now means our put may have been missed.
        current = await self._fs.get_document(SESSIONS_COLLECTION, session_id) or {}
        withdrawn = (current.get("setup") or {}).get("record") is not True
        # A delete-one of this very recording marks the attempt first, so the marker now means it ran mid-flight.
        deleted_one = bool((await self._fs.get_document(ATTEMPTS_COLLECTION, attempt_id) or {}).get(DELETED_MARKER))
        purged = withdrawn or (purged_at is not None and purged_at >= started)
        if purged or deleted_one:
            try:
                await asyncio.to_thread(self._delete_blob, name)
                await self._fs.delete_document(RECORDINGS_COLLECTION, recording_id)
                await self._fs.update_document(ATTEMPTS_COLLECTION, attempt_id, {"recording_id": None})
            except Exception:  # never a 500: the user's next delete sweeps what is left
                logger.warning("could not fully undo recording %s after a delete", recording_id, exc_info=True)
            if purged:
                raise RecordingsDeletedError(recording_id)
            logger.warning("voice upload undone: recording %s was deleted by its owner mid-upload", recording_id)
            raise RecordingDeletedError(recording_id)
        return recording_id

    async def delete_one(self, user_id: str, recording_id: str) -> None:
        """Erase one of the user's recordings: object (real erasure), doc and attempt link. Idempotent.

        Order: mark the attempt (``recording_deleted_at``) first, so an upload of it that is queued or in
        flight is refused or undoes itself (see ``upload``); then the object, the doc, the attempt's
        ``recording_id``. Consent for the session's other takes stays. A repeat delete finds the marker
        and succeeds; anything that isn't the user's is RecordingNotFoundError (404, never 403)."""
        doc = await self._fs.get_document(RECORDINGS_COLLECTION, recording_id)
        if doc is None:
            attempt = await self._fs.get_document(ATTEMPTS_COLLECTION, recording_id)
            if attempt and attempt.get("user_id") == user_id and attempt.get(DELETED_MARKER):
                return  # already deleted
            raise RecordingNotFoundError(recording_id)
        if doc.get("user_id") != user_id:
            raise RecordingNotFoundError(recording_id)
        attempt_id = f"{doc.get('session_id')}:{doc.get('attempt_n')}"
        attempt = await self._fs.get_document(ATTEMPTS_COLLECTION, attempt_id) or {}
        owned_attempt = attempt.get("user_id") == user_id
        if owned_attempt:
            await self._fs.update_document(ATTEMPTS_COLLECTION, attempt_id, {DELETED_MARKER: datetime.now(UTC)})
        name = recording_object_name(str(doc.get("gcs_path") or ""), str(getattr(self._bucket, "name", "") or ""))
        if name is not None and name.startswith(user_prefix(user_id)):
            await asyncio.to_thread(self._delete_blob, name)
        else:
            logger.warning("voice_recordings/%s has a gcs_path outside its owner's prefix; object left", recording_id)
        await self._fs.delete_document(RECORDINGS_COLLECTION, recording_id)
        if owned_attempt:
            await self._fs.update_document(ATTEMPTS_COLLECTION, attempt_id, {"recording_id": None})

    async def open_own(self, user_id: str, recording_id: str, bucket_name: str) -> tuple[str, Iterator[bytes]]:
        """(content type, audio chunks) of one of the user's own recordings; RecordingNotFoundError otherwise.

        A stored ``gcs_path`` outside ``voice-recordings/<user_id>/`` is never served (WARNING: the doc is
        inconsistent); a missing object is a 404 too (WARNING: the doc outlived its object)."""
        doc = await self._fs.get_document(RECORDINGS_COLLECTION, recording_id)
        if not doc or doc.get("user_id") != user_id or not doc.get("gcs_path"):
            raise RecordingNotFoundError(recording_id)
        name = recording_object_name(str(doc["gcs_path"]), bucket_name)
        if name is None or not name.startswith(user_prefix(user_id)):
            logger.warning("voice_recordings/%s has a gcs_path outside its owner's prefix; not served", recording_id)
            raise RecordingNotFoundError(recording_id)
        try:
            chunks = await open_recording(self._bucket, name)
        except RecordingObjectMissingError as exc:
            logger.warning("voice_recordings/%s: recording object already gone", recording_id)
            raise RecordingNotFoundError(recording_id) from exc
        return str(doc.get("content_type") or "application/octet-stream"), chunks

    async def count(self, user_id: str) -> int:
        docs = await self._fs.query_documents(RECORDINGS_COLLECTION, filters=[("user_id", "==", user_id)])
        return len(docs)

    async def delete_all_for_user(self, user_id: str) -> int:
        """Erase every recording of ``user_id``: objects (by prefix), docs, attempt links. Idempotent.

        Also withdraws recording consent (``setup.record = false``) on every session of the user, so an
        upload the app queued before the delete is refused (403 ``recording_not_consented``) instead of
        re-creating a recording; the user can consent again with PATCH. Returns the number of recordings
        erased (0 when there were none). Touches only this user's prefix (the prefix ends in ``/``, so
        ``user_1`` never matches ``user_10``).
        """
        prefix = user_prefix(user_id)
        # Written first: an upload in flight sees it afterwards and undoes itself (see ``upload``).
        await self._fs.set_document(PURGES_COLLECTION, user_id, {"purged_at": datetime.now(UTC)})
        sessions = await self._fs.query_documents(SESSIONS_COLLECTION, filters=[("user_id", "==", user_id)])
        for s in sessions:
            setup = s.get("setup") or {}
            if setup.get("record") is True:
                await self._fs.update_document(
                    SESSIONS_COLLECTION,
                    str(s["id"]),
                    {"setup": {**setup, "record": False}, "updated_at": datetime.now(UTC)},
                )

        def erase_objects() -> set[str]:
            names = [b.name for b in self._bucket.list_blobs(prefix=prefix)]
            for name in names:
                self._delete_blob(name)
            return set(names)

        erased = await asyncio.to_thread(erase_objects)  # a failure raises before any doc is removed: retry-safe
        docs = await self._fs.query_documents(RECORDINGS_COLLECTION, filters=[("user_id", "==", user_id)])
        orphans = sum(1 for d in docs if d.get("gcs_path") not in erased)
        for d in docs:
            await self._fs.delete_document(RECORDINGS_COLLECTION, str(d["id"]))
        attempts = await self._fs.query_documents(ATTEMPTS_COLLECTION, filters=[("user_id", "==", user_id)])
        for a in attempts:
            if a.get("recording_id"):
                await self._fs.update_document(ATTEMPTS_COLLECTION, str(a["id"]), {"recording_id": None})
        return len(erased) + orphans

    def _delete_blob(self, name: str) -> None:
        try:
            self._bucket.blob(name).delete()
        except Exception as exc:
            if _is_not_found(exc):
                logger.info("recording object already gone: %s", name)
                return
            raise


def _measure(file: IO[bytes]) -> int:
    """Bytes in ``file`` read in chunks (never whole in memory); RecordingTooLargeError past MAX_BYTES."""
    file.seek(0)
    total = 0
    while chunk := file.read(_COPY):
        total += len(chunk)
        if total > MAX_BYTES:
            raise RecordingTooLargeError(str(total))
    file.seek(0)
    return total


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
