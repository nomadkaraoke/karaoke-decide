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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from backend.services.auth_service import AuthService
from backend.services.crowd_stats import CROWD_COLLECTION
from backend.services.firestore_service import FirestoreService
from backend.services.get_made_service import DISMISSED_COLLECTION as MAKE_DISMISSED
from backend.services.import_ingest_service import (
    EXPORT_ARTISTS_COLLECTION,
    IMPORTS_COLLECTION,
    USER_ARTISTS_COLLECTION,
)
from backend.services.karaoke_log_service import LOG_COLLECTION, UNRESOLVED_COLLECTION
from backend.services.listenbrainz_service import ListenBrainzService
from backend.services.music_service_service import MusicServiceService
from backend.services.playlist_service import PlaylistService
from backend.services.quiz_service import QuizService
from backend.services.screenshot_import_service import RATE_COLLECTION as SCREENSHOT_IMPORTS
from backend.services.song_feedback_service import HIDDEN_COLLECTION, USER_SONGS_COLLECTION
from backend.services.sync_service import SyncService
from backend.services.voice_pool_service import BATCHES_COLLECTION as PERSONAL_BATCHES
from backend.services.voice_pool_service import BUDGET_COLLECTION as PERSONAL_BUDGET
from backend.services.voice_pool_service import LAST_BATCH_COLLECTION as PERSONAL_LAST_BATCH
from backend.services.voice_recording_service import PURGES_COLLECTION, RECORDINGS_COLLECTION, VoiceRecordingService
from backend.services.voice_session_outputs import SESSION_SOURCE
from backend.services.voice_session_service import (
    ACTIVE_COLLECTION,
    ATTEMPTS_COLLECTION,
    GUEST_REPORTERS_FIELD,
    REPORTERS_FIELD,
    SESSIONS_COLLECTION,
)
from karaoke_decide.voice_pool.voicedb import VERSION_STATS_COLLECTION

logger = logging.getLogger(__name__)

Match = Literal["user_id_field", "doc_id_user_id", "doc_id_prefix", "doc_id_email_hash", "email_field"]

USERS_COLLECTION = AuthService.USERS_COLLECTION
VOICE_LABELS_COLLECTION = "voice_labels"  # admin_voice.LABELS_COLLECTION (not imported: routes → services only)
SYNC_JOBS_COLLECTION = "sync_jobs"
QUIZ_PROGRESS_COLLECTION = "quiz_progress"
PAGE_SIZE = 500  # docs per query; deletes repeat until a query comes back empty


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
    UserDataStore(ACTIVE_COLLECTION, "doc_id_user_id"),  # voice_session_active/{user_id}: active session pointer
    UserDataStore(ATTEMPTS_COLLECTION, "user_id_field"),  # voice_attempts/{session}:{n}, user_id field
    UserDataStore(VOICE_LABELS_COLLECTION, "user_id_field"),  # imported sheet labels, user_id field
    UserDataStore(PURGES_COLLECTION, "doc_id_user_id"),  # voice_recording_purges/{user_id} marker
    UserDataStore(PERSONAL_BATCHES, "user_id_field"),  # voice_personal_batches/{batch_id}, user_id field
    UserDataStore(PERSONAL_LAST_BATCH, "doc_id_user_id"),  # voice_personal_users/{user_id}: last batch pointer
    # Listening and taste data.
    UserDataStore(USER_SONGS_COLLECTION, "user_id_field"),  # user_songs/{user_id}:{song_id}, user_id field
    UserDataStore(USER_ARTISTS_COLLECTION, "user_id_field"),  # user_artists/{user_id}:{artist}, user_id field
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
    UserDataStore(MAKE_DISMISSED, "user_id_field"),  # user_make_dismissed/{user_id}:{sha1(key)[:24]}, user_id field
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
        # Global per-version stats keyed by video id. Its ``not_embeddable_by`` / ``not_embeddable_by_guests`` list
        # user ids: erase_voice_data removes the user from them (the doc and any global ``embeddable`` verdict stay).
        VERSION_STATS_COLLECTION,
        # voice_personal_budget/{YYYY-MM}: the month's estimated personal-pool spend (totals only, no user).
        PERSONAL_BUDGET,
        CROWD_COLLECTION,  # song_crowd_stats/{crowd_doc_id(song_key)}: aggregate counts; ≥5-singer threshold; no user ids
    }
)

# Stores erase_voice_data deletes from (fully or in part), so they are counted before it runs.
VOICE_ERASED = frozenset(
    {
        RECORDINGS_COLLECTION,
        SESSIONS_COLLECTION,
        ACTIVE_COLLECTION,
        ATTEMPTS_COLLECTION,
        VOICE_LABELS_COLLECTION,
        PURGES_COLLECTION,
        USER_SONGS_COLLECTION,
    }
)


def email_hash(email: str) -> str:
    """``decide_users`` doc id: sha256 of the lowercase email (``AuthService._hash_email``)."""
    return hashlib.sha256(email.lower().encode()).hexdigest()


async def _delete_pages(fs: FirestoreService, collection: str, next_page: Callable[[], Awaitable[list[str]]]) -> int:
    """Delete ``next_page()``'s ids until it comes back empty. Returns how many were deleted.

    No query is capped: a user with more rows than a page (a big export import) is erased in full.
    A page that repeats the previous one means deletes aren't landing: raise rather than loop.
    """
    deleted, previous = 0, None
    while ids := await next_page():
        if ids == previous:
            raise RuntimeError(f"{collection}: deleted docs keep coming back")
        await fs.batch_delete(collection, ids)
        deleted += len(ids)
        previous = ids
    return deleted


async def _query_page(
    fs: FirestoreService, collection: str, filters: list[tuple[str, str, Any]]
) -> list[dict[str, Any]]:
    return await fs.query_documents(collection, filters=filters, limit=PAGE_SIZE)


async def _strip_session_rows(fs: FirestoreService, user_id: str, filters: list[tuple[str, str, Any]]) -> None:
    """Undo what voice sessions wrote to the user's ``user_songs`` rows matching ``filters``, page by page.

    Each row stops matching once handled (deleted, or ``voice_session`` gone from ``sources`` and
    ``source``), so re-querying until empty terminates.
    """
    previous: list[str] | None = None
    while rows := await _query_page(fs, USER_SONGS_COLLECTION, [("user_id", "==", user_id), *filters]):
        ids = [str(r["id"]) for r in rows]
        if ids == previous:
            raise RuntimeError(f"{USER_SONGS_COLLECTION}: voice session rows keep coming back")
        previous = ids
        deletes: list[str] = []
        for row in rows:
            sources = list(row.get("sources") or ([row["source"]] if row.get("source") else []))
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


async def erase_voice_data(fs: FirestoreService, bucket: Any, user_id: str) -> None:
    """Erase the user's voice data (the Phase 3d contract, docs/ARCHITECTURE.md § Personal data).

    Recordings (objects, docs, attempt links) via ``VoiceRecordingService.delete_all_for_user``,
    then the purge marker, the active-session pointer, sessions, attempts and imported labels, then undo what sessions wrote
    to ``user_songs``: rows made only by a session go; on any other row the ``session_*`` fields
    and the ``voice_session`` source are removed. Last, the user's id leaves every version's
    ``not_embeddable_by`` / ``not_embeddable_by_guests``. Idempotent; nothing is capped.
    """
    by_user = [("user_id", "==", user_id)]
    await VoiceRecordingService(fs, bucket).delete_all_for_user(user_id)  # objects, docs, attempt links
    await fs.delete_document(PURGES_COLLECTION, user_id)  # delete_all writes this marker; a fresh run may upload
    await fs.delete_document(ACTIVE_COLLECTION, user_id)  # the active-session pointer (sessions go next)
    for collection in (ATTEMPTS_COLLECTION, SESSIONS_COLLECTION, VOICE_LABELS_COLLECTION):

        async def page(collection: str = collection) -> list[str]:
            return [str(d["id"]) for d in await _query_page(fs, collection, by_user)]

        await _delete_pages(fs, collection, page)

    await _strip_session_rows(fs, user_id, [("sources", "array_contains", SESSION_SOURCE)])
    await _strip_session_rows(fs, user_id, [("source", "==", SESSION_SOURCE)])  # rows with no ``sources`` list
    for field in (REPORTERS_FIELD, GUEST_REPORTERS_FIELD):
        await _strip_not_embeddable_reports(fs, user_id, field)


async def _strip_not_embeddable_reports(fs: FirestoreService, user_id: str, field: str) -> None:
    """Remove the user from every ``voice_version_stats`` doc's ``field`` reporter list, page by page.

    One transaction per doc, so a concurrent report by another user is not lost. A global
    ``embeddable = false`` the user's report helped set stays: it is a verdict on the video, not user data.
    """
    by_user = [(field, "array_contains", user_id)]
    previous: list[str] | None = None
    while docs := await _query_page(fs, VERSION_STATS_COLLECTION, by_user):
        ids = [str(d["id"]) for d in docs]
        if ids == previous:
            raise RuntimeError(f"{VERSION_STATS_COLLECTION}: not_embeddable reports keep coming back")
        previous = ids
        for video_id in ids:

            async def strip(txn: Any, video_id: str = video_id) -> None:
                doc = await txn.get(VERSION_STATS_COLLECTION, video_id)
                if doc is None or user_id not in (doc.get(field) or []):
                    return
                by = [u for u in doc[field] if u != user_id]
                txn.set(
                    VERSION_STATS_COLLECTION,
                    video_id,
                    {**{k: v for k, v in doc.items() if k != "id"}, field: by},
                )

            await fs.run_transaction(strip)


async def _page_ids(fs: FirestoreService, store: UserDataStore, user_id: str | None, email: str) -> list[str]:
    """The next page (at most ``PAGE_SIZE``) of the user's doc ids in ``store``."""
    if store.match == "doc_id_email_hash":
        doc_id = email_hash(email)
        return [doc_id] if await fs.get_document(store.collection, doc_id) is not None else []
    if store.match == "email_field":
        return [str(d["id"]) for d in await _query_page(fs, store.collection, [(store.field, "==", email)])]
    if not user_id:
        return []
    if store.match == "doc_id_user_id":
        return [user_id] if await fs.get_document(store.collection, user_id) is not None else []
    if store.match == "user_id_field":
        return [str(d["id"]) for d in await _query_page(fs, store.collection, [(store.field, "==", user_id)])]
    raise NotImplementedError(f"{store.collection}: match {store.match!r} needs an id-prefix query in FirestoreService")


async def _count(fs: FirestoreService, store: UserDataStore, user_id: str) -> int:
    """How many docs the user has in ``store`` (an aggregation, so uncapped and cheap)."""
    if store.match == "doc_id_user_id":
        return int(await fs.get_document(store.collection, user_id) is not None)
    return await fs.count_documents(store.collection, filters=[(store.field, "==", user_id)])


async def delete_all_user_data(fs: FirestoreService, bucket: Any, email: str) -> dict[str, int]:
    """Erase everything decide holds for the user with this email. Returns counts per collection.

    Looks the profile up at ``decide_users/{sha256(email)}`` without creating it. An unknown email
    has nothing user-keyed to erase; only email-keyed stores are swept, and the result is ``{}``
    when they hold nothing. Idempotent: a second call finds nothing and returns ``{}``. Every store
    is deleted page by page until a query comes back empty, so nothing past a page is left behind.
    """
    email = email.strip().lower()
    profile = await fs.get_document(USERS_COLLECTION, email_hash(email))
    user_id = str(profile["user_id"]) if profile and profile.get("user_id") else None

    counts: dict[str, int] = {}
    if user_id:
        # Counted before erase_voice_data, which deletes these itself (and session-only user_songs rows).
        for store in USER_DATA_STORES:
            if store.collection in VOICE_ERASED:
                counts[store.collection] = await _count(fs, store, user_id)
        await erase_voice_data(fs, bucket, user_id)
    for store in USER_DATA_STORES:  # registry order: decide_users is last

        async def page(store: UserDataStore = store) -> list[str]:
            return await _page_ids(fs, store, user_id, email)

        swept = await _delete_pages(fs, store.collection, page)
        counts[store.collection] = max(counts.get(store.collection, 0), swept)

    if profile is None:
        counts = {c: n for c, n in counts.items() if n}
    logger.info("Deleted decide user data for user %s: %s", user_id or "(no profile)", counts)
    return counts
