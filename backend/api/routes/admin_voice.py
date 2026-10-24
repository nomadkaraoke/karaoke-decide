"""Admin routes for the voice session: import Andrew's sheet labels, and debug views
(pool coverage, a user's songs with markers / labels / attempts, recording audio).

Access: an admin decide user or the ``X-Admin-Token`` header (scripts/voice/import_sheet.py).
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import logging
from collections import Counter
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.api.deps import AdminAccess, AuthServiceDep, FirestoreServiceDep, Settings, get_voice_store
from backend.config import BackendSettings
from backend.services import catalog_store
from backend.services.catalog_store import CatalogUnavailable, get_catalog_store
from backend.services.recs_v2.service import voice_range
from backend.services.voice_data_store import (
    VoiceDataStore,
    VoiceDataUnavailable,
    VoiceReader,
)
from backend.services.voice_profile_service import VoiceProfileService
from backend.services.voice_recording_service import LocalBucket
from karaoke_decide.catalog.reader import CatalogReader
from karaoke_decide.song_search.text_normalize import song_key
from karaoke_decide.vocal_range.fit import UserVoiceRange
from karaoke_decide.voice_pool.queue import FETCH_COLLECTION, enqueue
from karaoke_decide.voice_session.labels import parse_good, row_tags
from karaoke_decide.voice_session.predict import PREDICT_WEIGHTS, features, held_out, predict_good, replay

logger = logging.getLogger(__name__)

router = APIRouter()

LABELS_COLLECTION = "voice_labels"
ATTEMPTS_COLLECTION = "voice_attempts"  # Task C3
RECORDINGS_COLLECTION = "voice_recordings"  # Task C4
MAX_ROWS = 500
BUDGET_TIMEOUT_S = 5.0
AUDIO_CHUNK = 256 * 1024


class SheetRowIn(BaseModel):
    artist: str
    title: str
    duet: bool | None = None
    good: str | None = None
    sung: bool | None = None
    notes: str = ""


class ImportLabelsRequest(BaseModel):
    email: str
    rows: list[SheetRowIn] = Field(max_length=MAX_ROWS)
    enqueue: bool = True


class UnmatchedRow(BaseModel):
    artist: str
    title: str


class ImportLabelsResponse(BaseModel):
    imported: int
    matched: int
    unmatched: list[UnmatchedRow]
    enqueued: int


async def get_voice_catalog() -> CatalogReader:
    """The startup-loaded catalog.db reader (tests override this)."""
    try:
        return get_catalog_store().require()
    except CatalogUnavailable as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="warming_up") from e


def label_doc_id(user_id: str, key: str) -> str:
    return f"{user_id}:{hashlib.sha1(key.encode()).hexdigest()[:24]}"


def _in_catalog(reader: CatalogReader, key: str) -> bool:
    return reader.conn().execute("SELECT 1 FROM songs WHERE song_key = ?", (key,)).fetchone() is not None


def _has_community_version(reader: CatalogReader, key: str) -> bool:
    if not reader.has_availability:
        return False
    row = (
        reader.conn()
        .execute(
            "SELECT 1 FROM av.versions WHERE song_key = ? AND kind = 'community' AND youtube_url IS NOT NULL LIMIT 1",
            (key,),
        )
        .fetchone()
    )
    return row is not None


@router.post("/labels/import", response_model=ImportLabelsResponse)
async def import_labels(
    body: ImportLabelsRequest,
    _access: AdminAccess,
    firestore: FirestoreServiceDep,
    auth: AuthServiceDep,
    catalog: Annotated[CatalogReader, Depends(get_voice_catalog)],
) -> ImportLabelsResponse:
    """Store sheet rows as ``voice_labels`` (idempotent per user + song) and queue matched songs."""
    user = await auth.resolve_gen_user(body.email)
    now = datetime.now(UTC)
    unmatched: list[UnmatchedRow] = []
    to_queue: list[dict[str, Any]] = []
    matched = 0
    for row in body.rows:
        key = song_key(row.artist, row.title)
        is_match = _in_catalog(catalog, key)
        await firestore.set_document(
            LABELS_COLLECTION,
            label_doc_id(user.id, key),
            {
                "user_id": user.id,
                "song_key": key,
                "artist": row.artist,
                "title": row.title,
                "duet": row.duet,
                "good": parse_good(row.good),
                "sung": row.sung,
                "notes": row.notes,
                "tags": sorted(row_tags(row.good, row.notes)),
                "matched": is_match,
                "source": "sheet",
                "imported_at": now,
            },
        )
        if not is_match:
            unmatched.append(UnmatchedRow(artist=row.artist, title=row.title))
            continue
        matched += 1
        if body.enqueue and _has_community_version(catalog, key):
            to_queue.append({"song_key": key, "artist": row.artist, "title": row.title, "source": "andrew_sheet"})
    enqueued = await enqueue(firestore, to_queue) if to_queue else 0
    return ImportLabelsResponse(imported=len(body.rows), matched=matched, unmatched=unmatched, enqueued=enqueued)


# ---------------------------------------------------------------- debug views


async def get_voice_reader(store: Annotated[VoiceDataStore, Depends(get_voice_store)]) -> VoiceReader:
    """voice.db's reader, or 503 ``warming_up`` (the voice session's dependency)."""
    try:
        return store.require()
    except VoiceDataUnavailable as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="warming_up") from e


def get_recordings_bucket(settings: Settings) -> Any:
    """The private recordings bucket (tests override this with a fake)."""
    if settings.voice_recordings_local_dir:
        return LocalBucket(settings.voice_recordings_local_dir)
    return catalog_store._storage_client().bucket(settings.voice_recordings_bucket)


async def fetch_spotify_budget(
    settings: BackendSettings, *, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any] | None:
    """flacfetch ``GET /spotify/budget``; None when flacfetch isn't configured here or the call fails."""
    base, key = settings.flacfetch_api_url.strip(), settings.flacfetch_api_key.strip()
    if not base or not key:
        return None
    try:
        async with httpx.AsyncClient(transport=transport, timeout=BUDGET_TIMEOUT_S) as client:
            res = await client.get(f"{base.rstrip('/')}/spotify/budget", headers={"X-API-Key": key})
            res.raise_for_status()
            body = res.json()
    except Exception as exc:  # noqa: BLE001  (a debug view: never fail on flacfetch)
        logger.warning("flacfetch /spotify/budget unavailable for voice coverage: %s", exc)
        return None
    return body if isinstance(body, dict) else None


def _per_dimension(rows: Sequence[tuple[str | None, bool]]) -> dict[str, dict[str, dict[str, int]]]:
    """``{dimension: {value: {songs, analysed}}}`` from ``dim=value|dim=value`` strata.

    Parts without ``=`` (e.g. the fixture's ``fixture|baritone``) count under ``other``.
    """
    out: dict[str, dict[str, dict[str, int]]] = {}
    for stratum, analysed in rows:
        for part in (stratum or "none").split("|"):
            dim, sep, value = part.partition("=")
            if not sep:
                dim, value = "other", part
            cell = out.setdefault(dim, {}).setdefault(value, {"songs": 0, "analysed": 0})
            cell["songs"] += 1
            cell["analysed"] += int(analysed)
    return out


class CoverageOut(BaseModel):
    run: str | None
    songs: int
    analysed: int
    per_stratum_dimension: dict[str, dict[str, dict[str, int]]]
    queue: dict[str, int]
    budget: dict[str, Any] | None


@router.get("/coverage", response_model=CoverageOut)
async def coverage(
    _access: AdminAccess,
    firestore: FirestoreServiceDep,
    settings: Settings,
    store: Annotated[VoiceDataStore, Depends(get_voice_store)],
) -> CoverageOut:
    """The published voice.db's pool by stratum dimension, the fetch queue by status, the Spotify budget."""
    reader = store.reader
    rows = await asyncio.to_thread(reader.stratum_rows) if reader is not None else []
    docs = await firestore.query_documents(FETCH_COLLECTION)
    return CoverageOut(
        run=store.run if reader is not None else None,
        songs=len(rows),
        analysed=sum(1 for _, a in rows if a),
        per_stratum_dimension=_per_dimension(rows),
        queue=dict(Counter(str(d.get("status")) for d in docs)),
        budget=await fetch_spotify_budget(settings),
    )


class LabelOut(BaseModel):
    good: str | None
    tags: list[str]
    notes: str


class AttemptDebugOut(BaseModel):
    session_id: str | None
    at: datetime | str | None
    quiz: dict[str, Any] | None
    context: dict[str, Any] | None
    pitch: dict[str, Any] | None
    recording_id: str | None


class PredictionOut(BaseModel):
    good: bool
    p: float


class UserSongOut(BaseModel):
    song_key: str
    artist: str | None
    title: str | None
    markers: dict[str, Any] | None
    label: LabelOut | None
    attempts: list[AttemptDebugOut]
    prediction: PredictionOut | None = None  # Task E1


_LEAD_GENDER = {
    "bass": "male",
    "baritone": "male",
    "tenor": "male",
    "alto": "female",
    "mezzo": "female",
    "soprano": "female",
}


async def _user_id(firestore: Any, auth: Any, email: str) -> str:
    """The decide user id for an email (lookup only), or 404."""
    profile = await firestore.get_document(auth.USERS_COLLECTION, auth._hash_email(email.strip().lower()))
    user_id = profile.get("user_id") if profile else None
    if not user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user_not_found")
    return str(user_id)


async def _voice(firestore: Any, user_id: str) -> tuple[UserVoiceRange | None, str | None]:
    """The user's saved voice range and, from its voice type, a male/female register hint
    (predict uses the hint only when the comfort centre sits between the registers)."""
    profile = await VoiceProfileService(firestore).get(user_id)
    if profile is None:
        return None, None
    return voice_range(profile), _LEAD_GENDER.get(profile.voice_type or "")


def _prediction(voice: UserVoiceRange | None, analysis: Any, lead_gender: str | None) -> PredictionOut | None:
    if voice is None or analysis is None:
        return None
    pred = predict_good(voice, analysis, lead_gender)
    return PredictionOut(good=pred[0], p=pred[1]) if pred is not None else None


def _sort_time(value: Any) -> str:
    return value.isoformat() if isinstance(value, datetime) else str(value or "")


@router.get("/users/{email}/songs", response_model=list[UserSongOut])
async def user_songs(
    email: str,
    _access: AdminAccess,
    firestore: FirestoreServiceDep,
    auth: AuthServiceDep,
    store: Annotated[VoiceDataStore, Depends(get_voice_store)],
) -> list[UserSongOut]:
    """Every song the user labelled or attempted, with voice.db markers and, when the user has a
    voice profile, the good/no prediction (lookup only: no profile is created)."""
    user_id = await _user_id(firestore, auth, email)
    voice, lead_gender = await _voice(firestore, user_id)
    labels = {
        d["song_key"]: d
        for d in await firestore.query_documents(LABELS_COLLECTION, filters=[("user_id", "==", user_id)])
    }
    attempts: dict[str, list[dict[str, Any]]] = {}
    for d in await firestore.query_documents(ATTEMPTS_COLLECTION, filters=[("user_id", "==", user_id)]):
        attempts.setdefault(d["song_key"], []).append(d)
    reader = store.reader
    keys = list(dict.fromkeys([*labels, *attempts]))

    def build() -> list[UserSongOut]:
        out = []
        for key in keys:
            song = reader.song(key) if reader is not None else None
            analysis = reader.analysis(key) if reader is not None else None
            label = labels.get(key)
            tries = sorted(attempts.get(key, []), key=lambda a: (_sort_time(a.get("started_at")), a.get("n") or 0))
            out.append(
                UserSongOut(
                    song_key=key,
                    artist=song.artist if song else (label or {}).get("artist"),
                    title=song.title if song else (label or {}).get("title"),
                    markers=dataclasses.asdict(analysis) if analysis else None,
                    prediction=_prediction(voice, analysis, lead_gender),
                    label=LabelOut(
                        good=label.get("good"), tags=list(label.get("tags") or []), notes=label.get("notes") or ""
                    )
                    if label
                    else None,
                    attempts=[
                        AttemptDebugOut(
                            session_id=a.get("session_id"),
                            at=a.get("started_at"),
                            quiz=a.get("quiz"),
                            context=a.get("context"),
                            pitch=a.get("pitch"),
                            recording_id=a.get("recording_id"),
                        )
                        for a in tries
                    ],
                )
            )
        out.sort(key=lambda s: ((s.artist or "").lower(), (s.title or "").lower(), s.song_key))
        return out

    return await asyncio.to_thread(build)


# ---------------------------------------------------------------- agreement (sign-off gate)


class AgreementRowOut(BaseModel):
    song_key: str
    artist: str | None
    title: str | None
    label: str
    features: dict[str, float]
    p: float
    pred: bool


class AgreementOut(BaseModel):
    n: int
    agree: int
    rate: float
    confusion: dict[str, int]
    ready_for_signoff: bool
    per_marker: dict[str, dict[str, float | None]]
    weights: dict[str, float]
    skipped_no_profile: int  # held-out songs not scored because the user has no voice profile
    skipped_no_range: int  # held-out songs whose analysis row has no measured range
    rows: list[AgreementRowOut]


@router.get("/agreement", response_model=AgreementOut)
async def agreement_report(
    email: str,
    _access: AdminAccess,
    firestore: FirestoreServiceDep,
    auth: AuthServiceDep,
    reader: Annotated[VoiceReader, Depends(get_voice_reader)],
) -> AgreementOut:
    """The good/no prediction vs the user's yes/no labels on held-out songs (labelled, analysed,
    never attempted in a session). ``rows`` carry each song's features for offline weight trials
    (``scripts/voice/agreement.py --export``)."""
    user_id = await _user_id(firestore, auth, email)
    voice, lead_gender = await _voice(firestore, user_id)
    labels = await firestore.query_documents(LABELS_COLLECTION, filters=[("user_id", "==", user_id)])
    attempts = await firestore.query_documents(ATTEMPTS_COLLECTION, filters=[("user_id", "==", user_id)])
    good = {d["song_key"]: d.get("good") for d in labels}

    def build() -> tuple[list[dict[str, Any]], int, int]:
        analyses = {k: a for k in good if (a := reader.analysis(k)) is not None}
        keys = held_out(labels, attempts, analyses)
        if voice is None:
            return [], len(keys), 0
        rows, no_range = [], 0
        for key in keys:
            x = features(voice, analyses[key], lead_gender)
            if x is None:
                no_range += 1
                continue
            song = reader.song(key)
            rows.append(
                {
                    "song_key": key,
                    "artist": song.artist if song else None,
                    "title": song.title if song else None,
                    "label": good[key],
                    "features": x,
                }
            )
        return rows, 0, no_range

    rows, no_profile, no_range = await asyncio.to_thread(build)
    return AgreementOut(**replay(rows, PREDICT_WEIGHTS), skipped_no_profile=no_profile, skipped_no_range=no_range)


RECORDINGS_PREFIX = "voice-recordings/"


def _object_name(gcs_path: str, bucket_name: str) -> str | None:
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


@router.get("/recordings/{recording_id}/audio")
async def recording_audio(
    recording_id: str,
    _access: AdminAccess,
    firestore: FirestoreServiceDep,
    settings: Settings,
    bucket: Annotated[Any, Depends(get_recordings_bucket)],
) -> StreamingResponse:
    """Stream a private recording (admin listening while tuning) with its stored content type."""
    doc = await firestore.get_document(RECORDINGS_COLLECTION, recording_id)
    if not doc or not doc.get("gcs_path"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="recording_not_found")
    name = _object_name(str(doc["gcs_path"]), settings.voice_recordings_bucket)
    if name is None:
        logger.warning("voice_recordings/%s has a gcs_path outside the recordings bucket", recording_id)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="recording_not_found")
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
        if catalog_store._is_not_found(exc):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="recording_not_found") from exc
        raise

    def chunks() -> Iterator[bytes]:
        try:
            yield head
            while chunk := src.read(AUDIO_CHUNK):
                yield chunk
        finally:
            src.close()

    return StreamingResponse(chunks(), media_type=str(doc.get("content_type") or "application/octet-stream"))
