"""Local-stack test API: reset, seed and read a user's decide profile.

Mounted by ``backend.main._mount_test_support`` only when DECIDE_TEST_API is on and the
environment is not production. No auth: anyone who can reach the server can rewrite any
profile, which is why production can never mount it. Users are gen users, so the profile
doc is ``decide_users/{sha256(lowercase email)}`` (via ``AuthService.resolve_gen_user``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from backend.api.deps import (
    get_auth_service_dep,
    get_firestore,
    get_import_ingest_service_dep,
    get_music_service_service_dep,
    get_settings,
)
from backend.api.routes.admin_voice import LABELS_COLLECTION as VOICE_LABELS
from backend.api.routes.admin_voice import get_recordings_bucket
from backend.config import BackendSettings
from backend.fakes.state import KNOWN_KNOBS, STATE, FakeLastFmUser, FakeListen
from backend.models.karaoke_log import KaraokeLogEntry
from backend.services.auth_service import AuthService
from backend.services.firestore_service import FirestoreService
from backend.services.import_ingest_service import ImportIngestService
from backend.services.karaoke_log_service import LOG_COLLECTION, UNRESOLVED_COLLECTION, log_doc_id
from backend.services.listenbrainz_poller import ListenBrainzPoller, PollReport
from backend.services.music_service_service import MusicServiceService
from backend.services.playlist_service import PlaylistService
from backend.services.song_feedback_service import HIDDEN_COLLECTION, USER_SONGS_COLLECTION, SongFeedbackService
from backend.services.voice_profile_service import VoiceProfileIn, VoiceProfileOut
from backend.services.voice_recording_service import PURGES_COLLECTION, RECORDINGS_COLLECTION, VoiceRecordingService
from backend.services.voice_session_outputs import SESSION_SOURCE
from backend.services.voice_session_service import ATTEMPTS_COLLECTION, SESSIONS_COLLECTION

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
    await fs.update_document(
        USERS, auth._hash_email(body.email.lower()), {"voice_profile": profile.model_dump(mode="json", by_alias=True)}
    )
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
    undo what sessions wrote to ``user_songs`` (rows made only by a session go; on any other row
    the ``session_*`` labels and the ``voice_session`` source are removed). Unknown emails: no-op."""
    user_id = await _user_id_of(fs, auth, body.email)
    if not user_id:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    by_user = [("user_id", "==", user_id)]
    await VoiceRecordingService(fs, bucket).delete_all_for_user(user_id)  # objects, docs, attempt links
    await fs.delete_document(PURGES_COLLECTION, user_id)  # delete_all writes this marker; a fresh run may upload
    for collection in (ATTEMPTS_COLLECTION, SESSIONS_COLLECTION, VOICE_LABELS):
        docs = await fs.query_documents(collection, filters=by_user, limit=10000)
        await fs.batch_delete(collection, [d["id"] for d in docs])

    rows = await fs.query_documents(USER_SONGS_COLLECTION, filters=by_user, limit=10000)
    deletes: list[str] = []
    for row in rows:
        sources = list(row.get("sources") or ([row["source"]] if row.get("source") else []))
        if SESSION_SOURCE not in sources and row.get("source") != SESSION_SOURCE:
            continue
        rest = [x for x in sources if x != SESSION_SOURCE]
        if not rest:
            deletes.append(row["id"])
            continue
        kept = {k: v for k, v in row.items() if k != "id" and not k.startswith("session_")}
        kept["sources"] = rest
        if kept.get("source") == SESSION_SOURCE:
            kept["source"] = rest[0]
        await fs.set_document(USER_SONGS_COLLECTION, row["id"], kept)
    await fs.batch_delete(USER_SONGS_COLLECTION, deletes)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
