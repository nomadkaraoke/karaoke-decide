"""Local-stack test API: reset, seed and read a user's decide profile.

Mounted by ``backend.main._mount_test_support`` only when DECIDE_TEST_API is on and the
environment is not production. No auth: anyone who can reach the server can rewrite any
profile, which is why production can never mount it. Users are gen users, so the profile
doc is ``decide_users/{sha256(lowercase email)}`` (via ``AuthService.resolve_gen_user``).
"""

from __future__ import annotations

import io
import math
import struct
import uuid
import wave
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, get_args

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from backend.api.deps import (
    get_auth_service_dep,
    get_catalog_store_dep,
    get_firestore,
    get_import_ingest_service_dep,
    get_karaoke_log_service,
    get_music_service_service_dep,
    get_settings,
    get_voice_store,
)
from backend.api.routes.admin_voice import get_recordings_bucket
from backend.config import BackendSettings
from backend.fakes.state import KNOWN_KNOBS, STATE, FakeLastFmUser, FakeListen
from backend.models.karaoke_log import KaraokeLogEntry
from backend.models.voice_session import NoteLabelIn, PitchSummaryIn, QuizIn, SessionResultSummaryOut, SessionStateName
from backend.services.auth_service import AuthService
from backend.services.crowd_stats import CROWD_COLLECTION, KEY_ANSWERS, VOICE_BUCKETS, crowd_doc_id
from backend.services.firestore_service import FirestoreService
from backend.services.get_made_service import DISMISSED_COLLECTION as MAKE_DISMISSED
from backend.services.import_ingest_service import ImportIngestService
from backend.services.karaoke_log_service import (
    LOG_COLLECTION,
    UNRESOLVED_COLLECTION,
    KaraokeLogError,
    KaraokeLogService,
    log_doc_id,
)
from backend.services.listenbrainz_poller import ListenBrainzPoller, PollReport
from backend.services.music_service_service import MusicServiceService
from backend.services.playlist_service import PlaylistService
from backend.services.song_feedback_service import (
    HIDDEN_COLLECTION,
    USER_SONGS_COLLECTION,
    SongFeedbackService,
    loved_doc_id,
)
from backend.services.user_deletion import erase_voice_data
from backend.services.voice_data_store import VoiceDataStore, VoiceDataUnavailable
from backend.services.voice_pool_service import BATCHES_COLLECTION as PERSONAL_BATCHES
from backend.services.voice_pool_service import BUDGET_COLLECTION as PERSONAL_BUDGET
from backend.services.voice_pool_service import HISTORY_LISTENS, month_key
from backend.services.voice_pool_service import LAST_BATCH_COLLECTION as PERSONAL_LAST_BATCH
from backend.services.voice_profile_service import VoiceProfileIn, VoiceProfileOut
from backend.services.voice_recording_service import RECORDINGS_COLLECTION, user_prefix
from backend.services.voice_session_service import ATTEMPTS_COLLECTION, SESSIONS_COLLECTION
from karaoke_decide.catalog import fixtures as catalog_fixtures
from karaoke_decide.song_search.text_normalize import song_key as make_song_key
from karaoke_decide.voice_pool import fixtures as voice_fixtures
from karaoke_decide.voice_pool.queue import FETCH_COLLECTION

router = APIRouter()

Affinity = Literal["occasionally", "like", "love"]
USERS = AuthService.USERS_COLLECTION
ARTISTS_COLLECTION = "user_artists"
QUIZ_PROGRESS_COLLECTION = "quiz_progress"
# Every per-user collection keyed by a user_id field, cleared by reset and by seed.
USER_OWNED = (
    USER_SONGS_COLLECTION,
    ARTISTS_COLLECTION,
    HIDDEN_COLLECTION,
    LOG_COLLECTION,
    UNRESOLVED_COLLECTION,
    PlaylistService.PLAYLISTS_COLLECTION,  # doc id == the playlist's own ``id`` field
    MAKE_DISMISSED,  # Get this made dismissals
)

FirestoreDep = Annotated[FirestoreService, Depends(get_firestore)]
AuthDep = Annotated[AuthService, Depends(get_auth_service_dep)]


class EmailBody(BaseModel):
    email: str


class SeedArtist(BaseModel):
    name: str
    affinity: Affinity


class SeedBody(BaseModel):
    email: str
    quiz_completed: bool = True
    genres: list[str] = Field(default_factory=list)
    decades: list[str] = Field(default_factory=list)
    energy: Literal["chill", "medium", "high"] | None = None
    artists: list[SeedArtist] = Field(default_factory=list)
    loved: list[str] = Field(default_factory=list)
    hidden: list[str] = Field(default_factory=list)


class VoiceSeedBody(BaseModel):
    email: str
    profile: VoiceProfileIn


def _split_key(song_key: str) -> tuple[str, str]:
    artist, _, title = song_key.partition("|")
    return artist, title


async def _clear_user_data(fs: FirestoreService, user_id: str) -> None:
    for collection in USER_OWNED:
        docs = await fs.query_documents(collection, filters=[("user_id", "==", user_id)], limit=10000)
        if collection == LOG_COLLECTION:  # the doc's own ``id`` field is the entry id, not the doc id
            ids = [log_doc_id(user_id, d["id"]) for d in docs]
        else:
            ids = [d["id"] for d in docs]
        await fs.batch_delete(collection, ids)
    await fs.delete_document(QUIZ_PROGRESS_COLLECTION, user_id)


@router.post("/profile/reset", status_code=status.HTTP_204_NO_CONTENT)
async def reset_profile(body: EmailBody, fs: FirestoreDep, auth: AuthDep) -> Response:
    """Delete the profile doc and everything owned by that user. Unknown emails are a no-op."""
    doc_id = auth._hash_email(body.email.lower())
    doc = await fs.get_document(USERS, doc_id)
    if doc is not None:
        if doc.get("user_id"):
            await _clear_user_data(fs, doc["user_id"])
        await fs.delete_document(USERS, doc_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/profile/seed")
async def seed_profile(body: SeedBody, fs: FirestoreDep, auth: AuthDep) -> dict[str, str]:
    """Replace the user's quiz answers, loved songs and hidden songs with the given ones.

    Writes the fields ``QuizService.submit_quiz`` writes (``quiz_*``), so recommendations
    v2 sees the same profile a real quiz run produces.
    """
    user = await auth.resolve_gen_user(body.email)
    await _clear_user_data(fs, user.id)

    now = datetime.now(UTC).isoformat()
    names = [a.name for a in body.artists]
    await fs.update_document(
        USERS,
        auth._hash_email(body.email.lower()),
        {
            "quiz_completed_at": now if body.quiz_completed else None,
            "quiz_genres": body.genres,
            "quiz_decades": body.decades,
            "quiz_energy_pref": body.energy,
            "quiz_artists_known": names,
            "quiz_artist_affinities": [{"artist_name": a.name, "affinity": a.affinity} for a in body.artists],
            "updated_at": now,
        },
    )
    feedback = SongFeedbackService(fs)
    for song_key in body.loved:
        artist, title = _split_key(song_key)
        await feedback.love(user.id, song_key, artist, title)
    for song_key in body.hidden:
        artist, title = _split_key(song_key)
        await feedback.hide(user.id, song_key, artist, title)
    return {"user_id": user.id}


@router.post("/voice/seed", status_code=status.HTTP_204_NO_CONTENT)
async def seed_voice(body: VoiceSeedBody, fs: FirestoreDep, auth: AuthDep) -> Response:
    """Set the user's voice profile (validated like PUT /api/my/voice) on their profile doc."""
    await auth.resolve_gen_user(body.email)
    profile = VoiceProfileOut(**body.profile.model_dump(by_alias=True), updated_at=datetime.now(UTC))
    stored = profile.model_dump(mode="json", by_alias=True)
    fields = {"voice_profile": stored, **({"voice_profile_mic": stored} if profile.method == "mic" else {})}
    await fs.update_document(USERS, auth._hash_email(body.email.lower()), fields)  # as VoiceProfileService.put
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _user_id_of(fs: FirestoreService, auth: AuthService, email: str) -> str | None:
    doc = await fs.get_document(USERS, auth._hash_email(email.lower())) or {}
    return doc.get("user_id") or None


@router.get("/voice/sessions")
async def read_voice_sessions(email: Annotated[str, Query()], fs: FirestoreDep, auth: AuthDep) -> list[dict[str, Any]]:
    """The user's voice sessions with their attempt counts, for E2E assertions (unknown email: empty)."""
    user_id = await _user_id_of(fs, auth, email)
    if not user_id:
        return []
    by_user = [("user_id", "==", user_id)]
    sessions = await fs.query_documents(SESSIONS_COLLECTION, filters=by_user, limit=10000)
    attempts = await fs.query_documents(ATTEMPTS_COLLECTION, filters=by_user, limit=10000)
    counts: dict[str, int] = {}
    for a in attempts:
        sid = str(a.get("session_id"))
        counts[sid] = counts.get(sid, 0) + 1
    return [
        {
            "session_id": d["id"],
            "state": d.get("state"),
            "attempts": counts.get(d["id"], 0),
            "done_reason": d.get("done_reason"),
        }
        for d in sessions
    ]


@router.get("/voice/recordings")
async def read_voice_recordings(email: Annotated[str, Query()], fs: FirestoreDep, auth: AuthDep) -> dict[str, int]:
    user_id = await _user_id_of(fs, auth, email)
    if not user_id:
        return {"count": 0}
    docs = await fs.query_documents(RECORDINGS_COLLECTION, filters=[("user_id", "==", user_id)], limit=10000)
    return {"count": len(docs)}


@router.post("/voice/reset", status_code=status.HTTP_204_NO_CONTENT)
async def reset_voice(
    body: EmailBody,
    fs: FirestoreDep,
    auth: AuthDep,
    bucket: Annotated[Any, Depends(get_recordings_bucket)],
) -> Response:
    """Delete the user's voice sessions, attempts, imported labels, recordings (objects too) and purge marker, and
    undo what sessions wrote to ``user_songs`` (``user_deletion.erase_voice_data``). Also resets the personal pool
    (``_reset_personal_pool``), which a run on a shared stack would otherwise inherit. Unknown emails: the
    personal pool's global docs are still reset."""
    user_id = await _user_id_of(fs, auth, body.email)
    if user_id:
        await erase_voice_data(fs, bucket, user_id)  # the Phase 3d contract, shared with account deletion
    await _reset_personal_pool(fs, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _reset_personal_pool(fs: FirestoreService, user_id: str | None) -> None:
    """The user's ``voice_personal_batches`` and ``voice_personal_users`` pointer, then (global, test stack only)
    every ``voice_pool_fetch`` doc with ``source == 'personal'`` (any user's: a queued song is skipped by every later
    request, so one run's batch would make the next run's ``nothing_to_add``) and this month's budget doc."""
    if user_id:
        batches = await fs.query_documents(PERSONAL_BATCHES, filters=[("user_id", "==", user_id)], limit=10000)
        await fs.batch_delete(PERSONAL_BATCHES, [str(d["id"]) for d in batches])
        await fs.delete_document(PERSONAL_LAST_BATCH, user_id)
    queued = await fs.query_documents(FETCH_COLLECTION, filters=[("source", "==", "personal")], limit=10000)
    await fs.batch_delete(FETCH_COLLECTION, [str(d["id"]) for d in queued])
    await fs.delete_document(PERSONAL_BUDGET, month_key(datetime.now(UTC)))


PERSONAL_SEED_SONGS = 8


def personal_seed_candidates(catalog: Any, voice: Any) -> list[tuple[str, str, str]]:
    """``(song_key, artist, title)`` the personal pool would pick on the local stack: fixture catalog songs
    (``catalog.fixtures``) that the fixture voice.db (``voice_pool.fixtures``) doesn't hold, with a community version
    that has a YouTube id in the loaded availability.db (``CatalogReader.community_songs``, the pool's own rule), and
    not in the loaded voice.db either (``voice`` None: not loaded). Sorted by key, so every run seeds the same."""
    pooled = voice_fixtures.pooled_song_keys()
    keys = sorted({s.song_key for s in catalog_fixtures._rows().songs} - pooled)
    community = catalog.community_songs(keys)
    return [(k, *community[k]) for k in keys if k in community and (voice is None or voice.song(k) is None)]


def personal_seed_prefix(user_id: str) -> str:
    return f"{user_id}:personal-seed:"


def personal_seed_rows(user_id: str, picks: list[tuple[str, str, str]], now: datetime) -> dict[str, dict[str, Any]]:
    """The seed's ``user_songs`` docs by id: one per pick (source ``lastfm``), the playcounts adding up to at least
    ``HISTORY_LISTENS`` (eligible by history)."""
    plays = math.ceil(HISTORY_LISTENS / max(1, len(picks))) + 1
    return {
        f"{personal_seed_prefix(user_id)}{i}": {
            "user_id": user_id,
            "song_key": key,
            "artist": artist,
            "title": title,
            "source": "lastfm",
            "sources": ["lastfm"],
            "playcount": plays,
            "created_at": now,
            "updated_at": now,
        }
        for i, (key, artist, title) in enumerate(picks)
    }


@router.post("/voice/personal-seed")
async def seed_personal_pool(
    body: EmailBody,
    fs: FirestoreDep,
    auth: AuthDep,
    catalog: Annotated[Any, Depends(get_catalog_store_dep)],
    voice: Annotated[VoiceDataStore, Depends(get_voice_store)],
) -> dict[str, Any]:
    """Make the user eligible for the personal pool by history, with songs it can pick (Maestro's "Analyse my
    music" flow): ``PERSONAL_SEED_SONGS`` of ``personal_seed_candidates`` as ``user_songs`` (source ``lastfm``) whose
    playcounts add up to at least ``HISTORY_LISTENS``. Replaces the user's earlier seed rows; 409
    ``no_personal_candidates`` when the stack has none (no availability.db)."""
    user = await auth.resolve_gen_user(body.email)
    try:
        voice_reader = voice.require()
    except VoiceDataUnavailable:
        voice_reader = None
    picks = personal_seed_candidates(catalog.require(), voice_reader)[:PERSONAL_SEED_SONGS]
    if not picks:
        raise HTTPException(status.HTTP_409_CONFLICT, "no_personal_candidates")
    prefix = personal_seed_prefix(user.id)
    old = await fs.query_documents(USER_SONGS_COLLECTION, filters=[("user_id", "==", user.id)], limit=10000)
    await fs.batch_delete(USER_SONGS_COLLECTION, [str(d["id"]) for d in old if str(d["id"]).startswith(prefix)])
    rows = personal_seed_rows(user.id, picks, datetime.now(UTC))
    for doc_id, row in rows.items():
        await fs.set_document(USER_SONGS_COLLECTION, doc_id, row)
    listens = sum(int(r["playcount"]) for r in rows.values())
    return {"user_id": user.id, "songs": [k for k, _, _ in picks], "listens": listens}


class SeedDemand(BaseModel):
    low: float = 48
    high: float = 64
    p10: float = 50
    p50: float = 55
    p90: float = 60


class SeedAttempt(BaseModel):
    song_key: str
    artist: str
    title: str
    quiz: QuizIn
    pitch: PitchSummaryIn | None = None
    note_labels: list[NoteLabelIn] = Field(default_factory=list, max_length=40)  # falsetto labels (v0.33.0)
    recording: bool = False
    excluded: bool = False
    demand: SeedDemand = Field(default_factory=SeedDemand)


class SeedSession(BaseModel):
    state: SessionStateName = "done"
    # SessionResultSummaryOut without session_id / computed_at (filled in here).
    result_summary: dict[str, Any] | None = None
    attempts: list[SeedAttempt] = Field(default_factory=list, max_length=12)


class VoiceHistorySeedBody(BaseModel):
    email: str
    sessions: list[SeedSession] = Field(default_factory=list, max_length=25)


SEED_WAV_SECONDS = 0.5
_SEED_WAV_RATE = 8000


def synthetic_wav(seconds: float = SEED_WAV_SECONDS, hz: float = 440.0) -> bytes:
    """A short mono 16-bit sine tone (no real audio ever goes into test data)."""
    frames = int(seconds * _SEED_WAV_RATE)
    pcm = b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * hz * i / _SEED_WAV_RATE))) for i in range(frames)
    )
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_SEED_WAV_RATE)
        w.writeframes(pcm)
    return out.getvalue()


@router.post("/voice/seed-history")
async def seed_voice_history(
    body: VoiceHistorySeedBody,
    fs: FirestoreDep,
    auth: AuthDep,
    bucket: Annotated[Any, Depends(get_recordings_bucket)],
) -> dict[str, Any]:
    """Replace the user's voice history (``erase_voice_data`` first) with the given sessions, in order
    (the first is the oldest), for Maestro's "Your recordings" flows. Attempts are stored as a real session
    stores them (outputs already applied, so a later ``GET .../result`` writes no ``user_songs``); an attempt
    with ``recording: true`` also gets a short synthetic WAV in the recordings bucket (local dir) and its
    ``voice_recordings`` doc, so ``GET /api/voice/recordings/{id}/audio`` plays it."""
    user = await auth.resolve_gen_user(body.email)
    await erase_voice_data(fs, bucket, user.id)
    now = datetime.now(UTC)
    out = []
    for i, seed in enumerate(body.sessions):
        sid = uuid.uuid4().hex
        created = now - timedelta(hours=len(body.sessions) - i)
        attempt_ids: list[str] = []
        recording_ids: list[str] = []
        for n, a in enumerate(seed.attempts, start=1):
            attempt_id = f"{sid}:{n}"
            started = created + timedelta(minutes=5 * n)
            recording_id = None
            if a.recording:
                wav = synthetic_wav()
                name = f"{user_prefix(user.id)}{sid}/{n}.wav"
                bucket.blob(name).upload_from_file(io.BytesIO(wav), content_type="audio/wav")
                recording_id = attempt_id
                await fs.set_document(
                    RECORDINGS_COLLECTION,
                    recording_id,
                    {
                        "user_id": user.id,
                        "session_id": sid,
                        "attempt_n": n,
                        "gcs_path": name,
                        "content_type": "audio/wav",
                        "bytes": len(wav),
                        "duration_s": SEED_WAV_SECONDS,
                        "created_at": started + timedelta(minutes=3),
                    },
                )
                recording_ids.append(recording_id)
            await fs.set_document(
                ATTEMPTS_COLLECTION,
                attempt_id,
                {
                    "user_id": user.id,
                    "session_id": sid,
                    "n": n,
                    "song_key": a.song_key,
                    "video_id": f"seed{n}",
                    "quiz": a.quiz.model_dump(),
                    "context": {"posture": "standing", "warmth": "warm", "headphones": True, "device": "Maestro"},
                    "pitch": a.pitch.model_dump() if a.pitch else None,
                    "pitch_all": None,
                    "note_labels": [n.model_dump() for n in a.note_labels],
                    "stopped_early": False,
                    "started_at": started,
                    "ended_at": started + timedelta(minutes=3),
                    "recording_id": recording_id,
                    "song": {
                        "artist": a.artist,
                        "title": a.title,
                        "artist_key": a.artist.lower(),
                        "stratum": "",
                        "demand": a.demand.model_dump(),
                    },
                    "outputs_applied": True,
                    "excluded": a.excluded,
                    "excluded_at": started if a.excluded else None,
                },
            )
            attempt_ids.append(attempt_id)
        summary = None
        if seed.result_summary is not None:
            try:
                summary = SessionResultSummaryOut.model_validate(
                    {"computed_at": created + timedelta(minutes=40), **seed.result_summary, "session_id": sid}
                ).model_dump(mode="json")
            except ValidationError as exc:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid result_summary") from exc
        await fs.set_document(
            SESSIONS_COLLECTION,
            sid,
            {
                "user_id": user.id,
                "setup": {
                    "posture": "standing",
                    "warmth": "warm",
                    "record": any(a.recording for a in seed.attempts),
                    "headphones": True,
                    "device": "Maestro",
                },  # fmt: skip
                "state": seed.state,
                "created_at": created,
                "updated_at": created + timedelta(minutes=40),
                "tired_count": 0,
                "skips": [],
                "prior_profile": None,
                "pending_song_key": None,
                "last_offer": None,
                "done_reason": None,
                "profile_saved": bool(summary and summary["profile_saved"]),
                "result_summary": summary,
            },
        )
        out.append({"session_id": sid, "attempt_ids": attempt_ids, "recording_ids": recording_ids})
    return {"user_id": user.id, "sessions": out}


@router.get("/profile")
async def read_profile(email: Annotated[str, Query()], fs: FirestoreDep, auth: AuthDep) -> dict[str, Any]:
    """The user's decide profile as seeded: an empty profile (user_id null) when none exists."""
    doc = await fs.get_document(USERS, auth._hash_email(email.lower())) or {}
    user_id = doc.get("user_id")
    feedback = SongFeedbackService(fs)
    return {
        "user_id": user_id,
        "quiz_completed": bool(doc.get("quiz_completed_at")),
        "genres": doc.get("quiz_genres") or [],
        "decades": doc.get("quiz_decades") or [],
        "energy": doc.get("quiz_energy_pref"),
        "artists": [
            {"name": a.get("artist_name"), "affinity": a.get("affinity")}
            for a in doc.get("quiz_artist_affinities") or []
        ],
        "loved": [s.song_key for s in await feedback.list_loved(user_id)] if user_id else [],
        "hidden": [s.song_key for s in await feedback.list_hidden(user_id)] if user_id else [],
        "voice_profile": doc.get("voice_profile"),
    }


class KaraokeLogSeedBody(BaseModel):
    email: str
    entries: list[dict[str, Any]]


@router.post("/karaoke-log/seed")
async def seed_karaoke_log(
    body: KaraokeLogSeedBody,
    auth: AuthDep,
    log_service: Annotated[KaraokeLogService, Depends(get_karaoke_log_service)],
) -> dict[str, Any]:
    """Write app (``app_…``) karaoke-log entries for the user (created if needed) through
    ``KaraokeLogService.upsert_user``, so counts and survey placeholders match a real app write.
    Each entry is an app patch plus its ``id``."""
    for entry in body.entries:
        entry_id = entry.get("id")
        if not isinstance(entry_id, str) or not entry_id.startswith("app_"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid")
    user = await auth.resolve_gen_user(body.email)
    for entry in body.entries:
        patch = {k: v for k, v in entry.items() if k != "id"}
        try:
            await log_service.upsert_user(user.id, entry["id"], patch, email=body.email)
        except KaraokeLogError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.code) from exc
    return {"user_id": user.id, "count": len(body.entries)}


class GetMadeSeedSong(BaseModel):
    artist: str
    title: str
    song_key: str | None = None
    loved: bool = False
    times_sung: int = Field(default=0, ge=0)


class GetMadeSeedBody(BaseModel):
    email: str
    songs: list[GetMadeSeedSong]


@router.post("/get-made/seed")
async def seed_get_made(body: GetMadeSeedBody, fs: FirestoreDep, auth: AuthDep) -> dict[str, Any]:
    """Write ``user_songs`` rows (loved and / or sung) for songs with no karaoke version, so
    ``GET /api/my/get-made`` lists them. Use the fixture catalog's "Fixture Artist NN / Fixture
    Song NN-MM" rows (``in_kn = 0``, not in availability.db). Rows are keyed like "Love it" rows
    (``loved_doc_id``), so a re-seed overwrites the same row; other data is left alone."""
    user = await auth.resolve_gen_user(body.email)
    now = datetime.now(UTC).isoformat()
    keys: list[str] = []
    for song in body.songs:
        key = song.song_key or make_song_key(song.artist, song.title)
        keys.append(key)
        await fs.set_document(
            USER_SONGS_COLLECTION,
            loved_doc_id(user.id, key),
            {
                "user_id": user.id,
                "song_id": key,
                "song_key": key,
                "artist": song.artist,
                "title": song.title,
                "source": "manual" if song.loved else "karaoke_log",
                "loved": song.loved,
                "times_sung": song.times_sung,
                "last_sung_at": now if song.times_sung else None,
                "play_count": 0,
                "is_saved": False,
                "created_at": now,
                "updated_at": now,
            },
        )
    return {"user_id": user.id, "keys": keys}


class CrowdCounts(BaseModel):
    """Key-answer counts; omitted answers are 0."""

    model_config = ConfigDict(extra="forbid")

    too_low: int = Field(default=0, ge=0)
    right: int = Field(default=0, ge=0)
    too_high: int = Field(default=0, ge=0)


VoiceBucket = Literal["bass", "baritone", "tenor", "alto", "mezzo", "soprano"]
assert set(get_args(VoiceBucket)) == set(VOICE_BUCKETS)  # kept in step with crowd_stats


class CrowdSeedBody(BaseModel):
    song_key: str = Field(min_length=1)
    total: CrowdCounts = Field(default_factory=CrowdCounts)
    by_bucket: dict[VoiceBucket, CrowdCounts] = Field(default_factory=dict)
    crowd_pleaser_yes: int = Field(default=0, ge=0)
    # Defaults to ``crowd_pleaser_yes`` (every answer was yes), so a share needs answered >= 5 (R17).
    crowd_pleaser_answered: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _answered_covers_yes(self) -> CrowdSeedBody:
        if self.crowd_pleaser_answered is not None and self.crowd_pleaser_answered < self.crowd_pleaser_yes:
            raise ValueError("crowd_pleaser_answered must be >= crowd_pleaser_yes")
        return self


class CrowdKeyBody(BaseModel):
    song_key: str = Field(min_length=1)


def _crowd_counts(c: CrowdCounts) -> dict[str, int]:
    return {k: getattr(c, k) for k in KEY_ANSWERS}


@router.post("/crowd/seed")
async def seed_crowd(body: CrowdSeedBody, fs: FirestoreDep) -> dict[str, str]:
    """Replace one song's ``song_crowd_stats`` doc with these counts, in the shape ``apply_vote`` /
    ``apply_pleaser`` write (doc id ``crowd_doc_id(song_key)``, R23), so ``GET /api/songs/{key}/crowd``
    and the recs crowd attach read it unchanged; thresholds still apply on read. Bucket counts are
    taken as given and not added to ``total`` (a real vote counts in both: include them in ``total``)."""
    doc_id = crowd_doc_id(body.song_key)
    yes = body.crowd_pleaser_yes
    await fs.set_document(
        CROWD_COLLECTION,
        doc_id,
        {
            "song_key": body.song_key,
            "total": _crowd_counts(body.total),
            "by_bucket": {b: _crowd_counts(c) for b, c in body.by_bucket.items()},
            "crowd_pleaser_yes": yes,
            "crowd_pleaser_answered": yes if body.crowd_pleaser_answered is None else body.crowd_pleaser_answered,
            "updated_at": datetime.now(UTC).isoformat(),
        },
    )
    return {"song_key": body.song_key, "doc_id": doc_id}


@router.post("/crowd/reset", status_code=status.HTTP_204_NO_CONTENT)
async def reset_crowd(body: CrowdKeyBody, fs: FirestoreDep) -> Response:
    """Delete that one song's ``song_crowd_stats`` doc. A missing doc is a no-op."""
    await fs.delete_document(CROWD_COLLECTION, crowd_doc_id(body.song_key))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/karaoke-log")
async def read_karaoke_log(email: Annotated[str, Query()], fs: FirestoreDep, auth: AuthDep) -> dict[str, Any]:
    """Everything the karaoke log holds for the user, for E2E assertions.

    ``entries``: every record, newest ``updated_at`` first, deleted ones and survey-only
    placeholders included. ``sung``: ``user_songs`` rows with a sung count. ``unresolved``:
    the per-artist+title counts of records with no catalog match."""
    doc = await fs.get_document(USERS, auth._hash_email(email.lower())) or {}
    user_id = doc.get("user_id")
    if not user_id:
        return {"user_id": None, "entries": [], "sung": [], "unresolved": []}
    by_user = [("user_id", "==", user_id)]
    logs, songs, unresolved = (
        await fs.query_documents(LOG_COLLECTION, filters=by_user, limit=10000),
        await fs.query_documents(USER_SONGS_COLLECTION, filters=by_user, limit=10000),
        await fs.query_documents(UNRESOLVED_COLLECTION, filters=by_user, limit=10000),
    )
    logs.sort(key=lambda d: str(d.get("updated_at") or ""), reverse=True)
    return {
        "user_id": user_id,
        "entries": [
            KaraokeLogEntry.model_validate(d).model_dump(mode="json", exclude={"user_id", "email_hash"}) for d in logs
        ],
        "sung": [
            {k: d.get(k) for k in ("song_key", "artist", "title", "times_sung", "last_sung_at", "source")}
            for d in songs
            if d.get("times_sung")
        ],
        "unresolved": [
            {"artist": d.get("artist"), "title": d.get("title"), "times_sung": d.get("times_sung")}
            for d in unresolved
            if d.get("times_sung")
        ],
    }


async def get_listenbrainz_poller(
    settings: Annotated[BackendSettings, Depends(get_settings)],
    fs: FirestoreDep,
    music_service: Annotated[MusicServiceService, Depends(get_music_service_service_dep)],
    ingest: Annotated[ImportIngestService, Depends(get_import_ingest_service_dep)],
) -> ListenBrainzPoller:
    return ListenBrainzPoller(settings, fs, music_service, ingest, music_service.listenbrainz)


class PollReportResponse(BaseModel):
    users_polled: int
    listens_added: int
    imports_checked: int
    rate_limited: int
    remaining: int


@router.post("/listenbrainz/poll", response_model=PollReportResponse)
async def poll_listenbrainz(
    settings: Annotated[BackendSettings, Depends(get_settings)],
    poller: Annotated[ListenBrainzPoller, Depends(get_listenbrainz_poller)],
) -> PollReportResponse:
    """Run the daily ``lb-poll`` job's poller once, in-process (local stack and Maestro)."""
    report: PollReport = await poller.run(settings.lb_poll_budget_seconds)
    return PollReportResponse(**vars(report))


# ---------------------------------------------------------------------------------------------
# DECIDE_FAKE_EXTERNALS (backend.fakes): reset the fakes, set knobs, seed fake users.
# ---------------------------------------------------------------------------------------------


def require_fakes(settings: Annotated[BackendSettings, Depends(get_settings)]) -> None:
    if not settings.decide_fake_externals:
        raise HTTPException(status.HTTP_409_CONFLICT, "fake_externals_disabled")


FakesOn = Depends(require_fakes)


class FakeExternalsBody(BaseModel):
    knobs: dict[str, str] = Field(default_factory=dict)


class FakeListenIn(BaseModel):
    artist: str
    title: str
    listened_at: int


class FakeListenBrainzUserBody(BaseModel):
    username: str = Field(min_length=1)
    listens: list[FakeListenIn] = Field(default_factory=list)


class FakeTopTrack(BaseModel):
    artist: str
    title: str
    playcount: int = 1


class FakeTopArtist(BaseModel):
    name: str
    playcount: int = 1


class FakeLastFmUserBody(BaseModel):
    username: str = Field(min_length=1)
    top_tracks: list[FakeTopTrack] = Field(default_factory=list)
    top_artists: list[FakeTopArtist] = Field(default_factory=list)


@router.put("/fake-externals", dependencies=[FakesOn])
async def set_fake_externals(body: FakeExternalsBody) -> dict[str, dict[str, str]]:
    """Reset every fake (codes, tokens, imports, seeded users back to the defaults) and set the knobs.

    Knobs: ``metabrainz=deny``, ``metabrainz_user=<name>``, ``spotify_embed=changed``, ``screenshot=fail``.
    """
    unknown = sorted(set(body.knobs) - KNOWN_KNOBS)
    if unknown:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown knobs: {', '.join(unknown)}")
    STATE.reset(body.knobs)
    return {"knobs": STATE.knobs}


@router.post("/fake-listenbrainz/users", dependencies=[FakesOn])
async def seed_fake_listenbrainz_user(body: FakeListenBrainzUserBody) -> dict[str, Any]:
    """Create or replace a fake ListenBrainz user and their listens."""
    STATE.lb_users[body.username] = [FakeListen(x.artist, x.title, x.listened_at) for x in body.listens]
    return {"username": body.username, "listens": len(body.listens)}


@router.post("/fake-lastfm/users", dependencies=[FakesOn])
async def seed_fake_lastfm_user(body: FakeLastFmUserBody) -> dict[str, Any]:
    """Create or replace a fake Last.fm user's top tracks and top artists."""
    STATE.lastfm_users[body.username] = FakeLastFmUser(
        top_tracks=[t.model_dump() for t in body.top_tracks],
        top_artists=[a.model_dump() for a in body.top_artists],
    )
    return {"username": body.username, "top_tracks": len(body.top_tracks), "top_artists": len(body.top_artists)}
