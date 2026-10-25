"""Account deletion: the registry of every per-user store, and ``delete_all_user_data``.

The registry rule (docs/ARCHITECTURE.md § Personal data): every Firestore collection decide's
code names is either in ``USER_DATA_STORES`` (erased by ``delete_all_user_data``) or in
``NOT_USER_DATA`` (global, or keyed by something that isn't the user). The guard test
``backend/tests/test_user_data_registry_guard.py`` fails on any collection in neither, so a new
per-user collection can't ship without being added here.

gen's account deletion calls this through decide's partner endpoint. Every step is idempotent
and any failure raises, so the caller can retry: voice data goes first (its erasure undoes what
voice sessions wrote to ``user_songs``), and the ``decide_users`` profile goes last, so a retry
after a part-way failure still finds the user.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Literal

from backend.services.auth_service import AuthService
from backend.services.firestore_service import FirestoreService
from backend.services.import_ingest_service import EXPORT_ARTISTS_COLLECTION, IMPORTS_COLLECTION
from backend.services.karaoke_log_service import LOG_COLLECTION, UNRESOLVED_COLLECTION
from backend.services.listenbrainz_service import ListenBrainzService
from backend.services.music_service_service import MusicServiceService
from backend.services.playlist_service import PlaylistService
from backend.services.quiz_service import QuizService
from backend.services.screenshot_import_service import RATE_COLLECTION as SCREENSHOT_IMPORTS
from backend.services.song_feedback_service import HIDDEN_COLLECTION, USER_SONGS_COLLECTION
from backend.services.sync_service import SyncService
from backend.services.voice_recording_service import PURGES_COLLECTION, RECORDINGS_COLLECTION, VoiceRecordingService
from backend.services.voice_session_outputs import SESSION_SOURCE
from backend.services.voice_session_service import ATTEMPTS_COLLECTION, SESSIONS_COLLECTION

logger = logging.getLogger(__name__)

Match = Literal["user_id_field", "doc_id_user_id", "doc_id_prefix", "doc_id_email_hash", "email_field"]

USERS_COLLECTION = AuthService.USERS_COLLECTION
VOICE_LABELS_COLLECTION = "voice_labels"  # admin_voice.LABELS_COLLECTION (not imported: routes → services only)
SYNC_JOBS_COLLECTION = "sync_jobs"
QUIZ_PROGRESS_COLLECTION = "quiz_progress"
QUERY_LIMIT = 10000


@dataclass(frozen=True)
class UserDataStore:
    """One per-user Firestore collection and how its docs are found for a user.

    ``user_id_field``: docs whose ``field`` equals the decide user id. ``doc_id_user_id``: the doc
    whose id is the user id. ``doc_id_prefix``: doc ids ``{user_id}:…`` (not supported yet:
    FirestoreService has no id-prefix query; every prefixed collection today also carries
    ``user_id``). ``doc_id_email_hash``: the doc ``sha256(lowercase email)``. ``email_field``:
    docs whose ``field`` equals the lowercase email.
    """

    collection: str
    match: Match
    field: str = "user_id"


USER_DATA_STORES: tuple[UserDataStore, ...] = (
    # Voice (Phase 3d contract): erased by erase_voice_data first; listed so they are counted and swept.
    UserDataStore(RECORDINGS_COLLECTION, "user_id_field"),  # voice_recordings/{session}:{n}, user_id field
    UserDataStore(SESSIONS_COLLECTION, "user_id_field"),  # voice_sessions/{session_id}, user_id field
    UserDataStore(ATTEMPTS_COLLECTION, "user_id_field"),  # voice_attempts/{session}:{n}, user_id field
    UserDataStore(VOICE_LABELS_COLLECTION, "user_id_field"),  # imported sheet labels, user_id field
    UserDataStore(PURGES_COLLECTION, "doc_id_user_id"),  # voice_recording_purges/{user_id} marker
    # Listening and taste data.
    UserDataStore(USER_SONGS_COLLECTION, "user_id_field"),  # user_songs/{user_id}:{song_id}, user_id field
    UserDataStore("user_artists", "user_id_field"),  # user_artists/{user_id}:{artist}, user_id field
    UserDataStore(HIDDEN_COLLECTION, "user_id_field"),  # user_hidden_songs/{user_id}:{digest}, user_id field
    UserDataStore(IMPORTS_COLLECTION, "user_id_field"),  # user_imports/{user_id}:{source} status docs, user_id field
    UserDataStore(EXPORT_ARTISTS_COLLECTION, "user_id_field"),  # export artist aggregates, user_id field
    UserDataStore(UNRESOLVED_COLLECTION, "user_id_field"),  # unmatched karaoke-log songs, user_id field
    UserDataStore(LOG_COLLECTION, "user_id_field"),  # karaoke_log/{user_id}:{entry_id}, user_id field
    UserDataStore(MusicServiceService.SERVICES_COLLECTION, "user_id_field"),  # {user_id}_{service}: tokens
    UserDataStore(QUIZ_PROGRESS_COLLECTION, "doc_id_user_id"),  # quiz_progress/{user_id}: unfinished quiz answers
    UserDataStore(SCREENSHOT_IMPORTS, "doc_id_user_id"),  # screenshot_imports/{user_id}: daily rate counter
    UserDataStore(SyncService.SPOTIFY_PLAYLIST_IMPORTS_COLLECTION, "doc_id_user_id"),  # rate counter per user
    UserDataStore(SyncService.YOUTUBE_MUSIC_IMPORTS_COLLECTION, "doc_id_user_id"),  # rate counter per user
    UserDataStore(PlaylistService.PLAYLISTS_COLLECTION, "user_id_field"),  # the user's playlists, user_id field
    UserDataStore(SYNC_JOBS_COLLECTION, "user_id_field"),  # music-service sync jobs and their results, user_id field
    # Sign-in records keyed by email.
    UserDataStore(AuthService.MAGIC_LINKS_COLLECTION, "email_field", "email"),  # magic_links/{token}, email field
    # The profile itself, deleted last (the lookup key for a retry).
    UserDataStore(USERS_COLLECTION, "doc_id_email_hash"),  # decide_users/{sha256(lowercase email)}
)

NOT_USER_DATA: frozenset[str] = frozenset(
    {
        QuizService.QUIZ_SONGS_CACHE_COLLECTION,  # quiz_songs_cache: global song pool cache
        ListenBrainzService.CACHE_COLLECTION,  # listenbrainz_cache: global per-recording lookups
        MusicServiceService.OAUTH_STATES_COLLECTION,  # oauth_states/{state}: 10-minute tokens, keyed by state
        "gen_users",  # gen's collection (read for session checks); gen deletes it in its own step
        "sessions",  # gen's session docs (read only); gen revokes them LAST so a failed deletion can be retried
        "users",  # legacy pre-decide_users collection, only counted by /health
        QuizService.LASTFM_USERS_COLLECTION,  # lastfm_users: imported public Last.fm dataset, no decide user link
        "jobs",  # gen's jobs, read by the vocal range ETL
        "voice_pool_fetch",  # global voice-pool fetch queue (songs, no user)
        "voice_version_stats",  # global per-version analysis stats
    }
)


def email_hash(email: str) -> str:
    """``decide_users`` doc id: sha256 of the lowercase email (``AuthService._hash_email``)."""
    return hashlib.sha256(email.lower().encode()).hexdigest()


async def erase_voice_data(fs: FirestoreService, bucket: Any, user_id: str) -> None:
    """Erase the user's voice data (the Phase 3d contract, docs/ARCHITECTURE.md § Personal data).

    Recordings (objects, docs, attempt links) via ``VoiceRecordingService.delete_all_for_user``,
    then the purge marker, sessions, attempts and imported labels, then undo what sessions wrote
    to ``user_songs``: rows made only by a session go; on any other row the ``session_*`` fields
    and the ``voice_session`` source are removed. Idempotent.
    """
    by_user = [("user_id", "==", user_id)]
    await VoiceRecordingService(fs, bucket).delete_all_for_user(user_id)  # objects, docs, attempt links
    await fs.delete_document(PURGES_COLLECTION, user_id)  # delete_all writes this marker; a fresh run may upload
    for collection in (ATTEMPTS_COLLECTION, SESSIONS_COLLECTION, VOICE_LABELS_COLLECTION):
        docs = await fs.query_documents(collection, filters=by_user, limit=QUERY_LIMIT)
        await fs.batch_delete(collection, [d["id"] for d in docs])

    rows = await fs.query_documents(USER_SONGS_COLLECTION, filters=by_user, limit=QUERY_LIMIT)
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


async def _doc_ids(fs: FirestoreService, store: UserDataStore, user_id: str | None, email: str) -> list[str]:
    if store.match == "doc_id_email_hash":
        doc_id = email_hash(email)
        return [doc_id] if await fs.get_document(store.collection, doc_id) is not None else []
    if store.match == "email_field":
        docs = await fs.query_documents(store.collection, filters=[(store.field, "==", email)], limit=QUERY_LIMIT)
        return [str(d["id"]) for d in docs]
    if not user_id:
        return []
    if store.match == "doc_id_user_id":
        return [user_id] if await fs.get_document(store.collection, user_id) is not None else []
    if store.match == "user_id_field":
        docs = await fs.query_documents(store.collection, filters=[(store.field, "==", user_id)], limit=QUERY_LIMIT)
        return [str(d["id"]) for d in docs]
    raise NotImplementedError(f"{store.collection}: match {store.match!r} needs an id-prefix query in FirestoreService")


async def delete_all_user_data(fs: FirestoreService, bucket: Any, email: str) -> dict[str, int]:
    """Erase everything decide holds for the user with this email. Returns counts per collection.

    Looks the profile up at ``decide_users/{sha256(email)}`` without creating it. An unknown email
    has nothing user-keyed to erase; only email-keyed stores are swept, and the result is ``{}``
    when they hold nothing. Idempotent: a second call finds nothing and returns zeros (or ``{}``).
    """
    email = email.strip().lower()
    profile = await fs.get_document(USERS_COLLECTION, email_hash(email))
    user_id = str(profile["user_id"]) if profile and profile.get("user_id") else None

    found = {s.collection: set(await _doc_ids(fs, s, user_id, email)) for s in USER_DATA_STORES}
    if user_id:
        await erase_voice_data(fs, bucket, user_id)
    for store in USER_DATA_STORES:  # registry order: decide_users is last
        ids = await _doc_ids(fs, store, user_id, email)  # again: catches docs written since the count
        await fs.batch_delete(store.collection, ids)
        found[store.collection] |= set(ids)

    counts = {c: len(ids) for c, ids in found.items()}
    if profile is None:
        counts = {c: n for c, n in counts.items() if n}
    logger.info("Deleted decide user data for user %s: %s", user_id or "(no profile)", counts)
    return counts
