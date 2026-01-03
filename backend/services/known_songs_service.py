"""Service for managing user's known songs.

Allows users to manually add songs they know they like singing,
which helps improve recommendation quality.
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from google.cloud import bigquery

from backend.config import BackendSettings
from backend.services.firestore_service import FirestoreService


@dataclass
class AddKnownSongResult:
    """Result of adding a known song."""

    added: bool
    song_id: str
    artist: str
    title: str
    already_existed: bool = False


@dataclass
class KnownSongsListResult:
    """Result of listing known songs."""

    songs: list[dict]
    total: int
    page: int
    per_page: int


class KnownSongsService:
    """Service for managing user's manually added known songs.

    Allows users to search for and add songs they already know they
    like singing, separate from music service sync or quiz selection.
    """

    USER_SONGS_COLLECTION = "user_songs"

    # BigQuery config
    PROJECT_ID = "nomadkaraoke"
    DATASET_ID = "karaoke_decide"

    def __init__(
        self,
        settings: BackendSettings,
        firestore: FirestoreService,
        bigquery_client: bigquery.Client | None = None,
    ):
        """Initialize the known songs service.

        Args:
            settings: Backend settings.
            firestore: Firestore service for user data.
            bigquery_client: Optional BigQuery client (created lazily).
        """
        self.settings = settings
        self.firestore = firestore
        self._bigquery_client = bigquery_client

    @property
    def bigquery(self) -> bigquery.Client:
        """Get or create BigQuery client."""
        if self._bigquery_client is None:
            self._bigquery_client = bigquery.Client(project=self.PROJECT_ID)
        return self._bigquery_client

    async def add_known_song(
        self,
        user_id: str,
        song_id: int,
    ) -> AddKnownSongResult:
        """Add a song to user's known songs.

        Args:
            user_id: User's ID.
            song_id: Karaoke catalog song ID.

        Returns:
            AddKnownSongResult with song details.

        Raises:
            ValueError: If song not found in catalog.
        """
        # Get song details from BigQuery (run in executor to avoid blocking)
        loop = asyncio.get_running_loop()
        song = await loop.run_in_executor(None, self._get_song_by_id, song_id)
        if not song:
            raise ValueError(f"Song with ID {song_id} not found in catalog")

        now = datetime.now(UTC)
        user_song_id = f"{user_id}:{song_id}"

        # Check if already exists first
        existing = await self.firestore.get_document(self.USER_SONGS_COLLECTION, user_song_id)

        if existing is not None:
            # Song already in user's library
            return AddKnownSongResult(
                added=False,
                song_id=str(song_id),
                artist=song["artist"],
                title=song["title"],
                already_existed=True,
            )

        # Create new UserSong record
        user_song_data = {
            "id": user_song_id,
            "user_id": user_id,
            "song_id": str(song_id),
            "source": "known_songs",
            "play_count": 1,  # User selected it, counts as one "play"
            "last_played_at": None,
            "is_saved": True,  # User explicitly saved this
            "times_sung": 0,
            "last_sung_at": None,
            "average_rating": None,
            "notes": None,
            "artist": song["artist"],
            "title": song["title"],
            "has_karaoke_version": True,  # It's from karaoke catalog
            "spotify_popularity": None,
            "duration_ms": None,
            "explicit": False,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }

        # Use merge=True to handle potential TOCTOU race condition gracefully
        # If concurrent request creates the doc, this will merge (effectively no-op
        # since data is identical). This is safe because we already check existence
        # above, and the worst case is both requests succeed (acceptable).
        await self.firestore.set_document(
            self.USER_SONGS_COLLECTION,
            user_song_id,
            user_song_data,
            merge=True,
        )

        return AddKnownSongResult(
            added=True,
            song_id=str(song_id),
            artist=song["artist"],
            title=song["title"],
            already_existed=False,
        )

    async def remove_known_song(
        self,
        user_id: str,
        song_id: int,
    ) -> bool:
        """Remove a song from user's known songs.

        Only removes songs with source='known_songs'. Songs from other
        sources (spotify, lastfm, quiz) are not removed.

        Args:
            user_id: User's ID.
            song_id: Karaoke catalog song ID.

        Returns:
            True if removed, False if not found or wrong source.
        """
        user_song_id = f"{user_id}:{song_id}"

        # Check if exists and is from known_songs source
        existing = await self.firestore.get_document(self.USER_SONGS_COLLECTION, user_song_id)

        if existing is None:
            return False

        # Only remove if source is known_songs
        if existing.get("source") != "known_songs":
            return False

        await self.firestore.delete_document(self.USER_SONGS_COLLECTION, user_song_id)
        return True

    async def get_known_songs(
        self,
        user_id: str,
        page: int = 1,
        per_page: int = 20,
    ) -> KnownSongsListResult:
        """Get user's known songs (added manually).

        Args:
            user_id: User's ID.
            page: Page number (1-indexed).
            per_page: Items per page.

        Returns:
            KnownSongsListResult with paginated songs.

        Raises:
            ValueError: If page < 1 or per_page < 1.
        """
        # Validate pagination parameters
        if page < 1:
            raise ValueError("page must be >= 1")
        if per_page < 1:
            raise ValueError("per_page must be >= 1")

        offset = (page - 1) * per_page

        # Count total known songs for this user
        total = await self.firestore.count_documents(
            self.USER_SONGS_COLLECTION,
            filters=[
                ("user_id", "==", user_id),
                ("source", "==", "known_songs"),
            ],
        )

        # Get paginated results
        songs = await self.firestore.query_documents(
            self.USER_SONGS_COLLECTION,
            filters=[
                ("user_id", "==", user_id),
                ("source", "==", "known_songs"),
            ],
            order_by="created_at",
            order_direction="DESCENDING",
            limit=per_page,
            offset=offset,
        )

        return KnownSongsListResult(
            songs=songs,
            total=total,
            page=page,
            per_page=per_page,
        )

    async def bulk_add_known_songs(
        self,
        user_id: str,
        song_ids: list[int],
    ) -> dict:
        """Add multiple songs to user's known songs.

        Args:
            user_id: User's ID.
            song_ids: List of karaoke catalog song IDs.

        Returns:
            Dict with counts: added, already_existed, not_found.
        """
        added = 0
        already_existed = 0
        not_found = 0

        for song_id in song_ids:
            try:
                result = await self.add_known_song(user_id, song_id)
                if result.added:
                    added += 1
                elif result.already_existed:
                    already_existed += 1
            except ValueError:
                not_found += 1

        return {
            "added": added,
            "already_existed": already_existed,
            "not_found": not_found,
            "total_requested": len(song_ids),
        }

    def _get_song_by_id(self, song_id: int) -> dict | None:
        """Get song details from BigQuery.

        Args:
            song_id: Karaoke catalog song ID.

        Returns:
            Dict with id, artist, title or None if not found.
        """
        sql = f"""
            SELECT
                CAST(Id AS STRING) as id,
                Artist as artist,
                Title as title
            FROM `{self.PROJECT_ID}.{self.DATASET_ID}.karaokenerds_raw`
            WHERE Id = @song_id
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("song_id", "INT64", song_id),
            ]
        )

        results = list(self.bigquery.query(sql, job_config=job_config).result())

        if not results:
            return None

        row = results[0]
        return {
            "id": row.id,
            "artist": row.artist,
            "title": row.title,
        }


# Lazy initialization
_known_songs_service: KnownSongsService | None = None


def get_known_songs_service(
    settings: BackendSettings | None = None,
    firestore: FirestoreService | None = None,
) -> KnownSongsService:
    """Get the known songs service instance.

    Args:
        settings: Optional settings override.
        firestore: Optional Firestore service override.

    Returns:
        KnownSongsService instance.
    """
    global _known_songs_service

    if _known_songs_service is None or settings is not None or firestore is not None:
        if settings is None:
            from backend.config import get_backend_settings

            settings = get_backend_settings()
        if firestore is None:
            firestore = FirestoreService(settings)

        _known_songs_service = KnownSongsService(settings, firestore)

    return _known_songs_service
