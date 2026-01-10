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
    has_karaoke_version: bool = True


@dataclass
class AddSpotifyTrackResult:
    """Result of adding a song via Spotify track ID."""

    added: bool
    track_id: str
    track_name: str
    artist_name: str
    artist_id: str
    popularity: int
    duration_ms: int
    explicit: bool
    already_existed: bool = False


@dataclass
class KnownSongsListResult:
    """Result of listing known songs."""

    songs: list[dict]
    total: int
    page: int
    per_page: int


@dataclass
class SetEnjoySingingResult:
    """Result of setting enjoy singing metadata on a song."""

    success: bool
    song_id: str
    artist: str
    title: str
    enjoy_singing: bool
    singing_tags: list[str]
    singing_energy: str | None
    vocal_comfort: str | None
    notes: str | None
    created_new: bool = False  # True if song was newly added


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

    async def add_spotify_track(
        self,
        user_id: str,
        track_id: str,
    ) -> AddSpotifyTrackResult:
        """Add a song to user's known songs via Spotify track ID.

        Args:
            user_id: User's ID.
            track_id: Spotify track ID.

        Returns:
            AddSpotifyTrackResult with track details.

        Raises:
            ValueError: If track not found in Spotify catalog.
        """
        # Get track details from BigQuery (run in executor to avoid blocking)
        loop = asyncio.get_running_loop()
        track = await loop.run_in_executor(None, self._get_spotify_track, track_id)
        if not track:
            raise ValueError(f"Track with ID {track_id} not found in Spotify catalog")

        now = datetime.now(UTC)
        # Use spotify: prefix to differentiate from karaoke catalog IDs
        user_song_id = f"{user_id}:spotify:{track_id}"

        # Check if already exists first
        existing = await self.firestore.get_document(self.USER_SONGS_COLLECTION, user_song_id)

        if existing is not None:
            # Song already in user's library
            return AddSpotifyTrackResult(
                added=False,
                track_id=track_id,
                track_name=track["track_name"],
                artist_name=track["artist_name"],
                artist_id=track["artist_id"],
                popularity=track["popularity"],
                duration_ms=track["duration_ms"],
                explicit=track["explicit"],
                already_existed=True,
            )

        # Create new UserSong record
        user_song_data = {
            "id": user_song_id,
            "user_id": user_id,
            "song_id": f"spotify:{track_id}",  # Prefixed to indicate Spotify source
            "source": "known_songs",
            "play_count": 1,  # User selected it, counts as one "play"
            "last_played_at": None,
            "is_saved": True,  # User explicitly saved this
            "times_sung": 0,
            "last_sung_at": None,
            "average_rating": None,
            "notes": None,
            "artist": track["artist_name"],
            "title": track["track_name"],
            "has_karaoke_version": False,  # Spotify track, not from karaoke catalog
            "spotify_track_id": track_id,  # Reference to Spotify
            "spotify_artist_id": track["artist_id"],
            "spotify_popularity": track["popularity"],
            "duration_ms": track["duration_ms"],
            "explicit": track["explicit"],
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }

        await self.firestore.set_document(
            self.USER_SONGS_COLLECTION,
            user_song_id,
            user_song_data,
            merge=True,
        )

        return AddSpotifyTrackResult(
            added=True,
            track_id=track_id,
            track_name=track["track_name"],
            artist_name=track["artist_name"],
            artist_id=track["artist_id"],
            popularity=track["popularity"],
            duration_ms=track["duration_ms"],
            explicit=track["explicit"],
            already_existed=False,
        )

    async def remove_spotify_track(
        self,
        user_id: str,
        track_id: str,
    ) -> bool:
        """Remove a Spotify track from user's known songs.

        Args:
            user_id: User's ID.
            track_id: Spotify track ID.

        Returns:
            True if removed, False if not found or wrong source.
        """
        user_song_id = f"{user_id}:spotify:{track_id}"

        # Check if exists and is from known_songs source
        existing = await self.firestore.get_document(self.USER_SONGS_COLLECTION, user_song_id)

        if existing is None:
            return False

        # Only remove if source is known_songs
        if existing.get("source") != "known_songs":
            return False

        await self.firestore.delete_document(self.USER_SONGS_COLLECTION, user_song_id)
        return True

    async def set_enjoy_singing(
        self,
        user_id: str,
        song_id: str,
        singing_tags: list[str] | None = None,
        singing_energy: str | None = None,
        vocal_comfort: str | None = None,
        notes: str | None = None,
    ) -> SetEnjoySingingResult:
        """Mark a song as one the user enjoys singing with optional metadata.

        This can work on:
        - Existing songs in user's library (updates with enjoy_singing=True)
        - New karaoke catalog songs (creates with source="enjoy_singing")
        - New Spotify tracks (creates with source="enjoy_singing")

        Args:
            user_id: User's ID.
            song_id: Song ID - either karaoke catalog ID or "spotify:{track_id}".
            singing_tags: Optional list of tags describing why user enjoys singing.
                Valid: "easy_to_sing", "crowd_pleaser", "shows_range", "fun_lyrics", "nostalgic"
            singing_energy: Optional energy/mood of the song for the user.
                Valid: "upbeat_party", "chill_ballad", "emotional_powerhouse"
            vocal_comfort: Optional comfort level in user's vocal range.
                Valid: "easy", "comfortable", "challenging"
            notes: Optional free-form notes from user.

        Returns:
            SetEnjoySingingResult with song details and metadata.

        Raises:
            ValueError: If song not found in catalog.
        """
        from karaoke_decide.core.models import (
            SINGING_ENERGY_OPTIONS,
            SINGING_TAGS,
            VOCAL_COMFORT_OPTIONS,
        )

        # Validate inputs
        if singing_tags:
            invalid_tags = [t for t in singing_tags if t not in SINGING_TAGS]
            if invalid_tags:
                raise ValueError(f"Invalid singing tags: {invalid_tags}. Valid: {SINGING_TAGS}")

        if singing_energy and singing_energy not in SINGING_ENERGY_OPTIONS:
            raise ValueError(f"Invalid singing_energy: {singing_energy}. Valid: {SINGING_ENERGY_OPTIONS}")

        if vocal_comfort and vocal_comfort not in VOCAL_COMFORT_OPTIONS:
            raise ValueError(f"Invalid vocal_comfort: {vocal_comfort}. Valid: {VOCAL_COMFORT_OPTIONS}")

        now = datetime.now(UTC)
        is_spotify = song_id.startswith("spotify:")

        # Build the user_song_id based on song type
        if is_spotify:
            track_id = song_id.replace("spotify:", "")
            user_song_id = f"{user_id}:spotify:{track_id}"
        else:
            user_song_id = f"{user_id}:{song_id}"

        # Check if already exists
        existing = await self.firestore.get_document(self.USER_SONGS_COLLECTION, user_song_id)

        if existing is not None:
            # Update existing song with enjoy_singing metadata
            update_data = {
                "enjoy_singing": True,
                "singing_tags": singing_tags or [],
                "singing_energy": singing_energy,
                "vocal_comfort": vocal_comfort,
                "notes": notes,
                "updated_at": now.isoformat(),
            }
            await self.firestore.update_document(self.USER_SONGS_COLLECTION, user_song_id, update_data)
            return SetEnjoySingingResult(
                success=True,
                song_id=song_id,
                artist=existing.get("artist", ""),
                title=existing.get("title", ""),
                enjoy_singing=True,
                singing_tags=singing_tags or [],
                singing_energy=singing_energy,
                vocal_comfort=vocal_comfort,
                notes=notes,
                created_new=False,
            )

        # Song doesn't exist - need to look it up and create
        loop = asyncio.get_running_loop()

        if is_spotify:
            track_id = song_id.replace("spotify:", "")
            track = await loop.run_in_executor(None, self._get_spotify_track, track_id)
            if not track:
                raise ValueError(f"Track with ID {track_id} not found in Spotify catalog")

            artist = track["artist_name"]
            title = track["track_name"]

            user_song_data = {
                "id": user_song_id,
                "user_id": user_id,
                "song_id": song_id,
                "source": "enjoy_singing",
                "play_count": 1,
                "last_played_at": None,
                "is_saved": True,
                "times_sung": 0,
                "last_sung_at": None,
                "average_rating": None,
                "artist": artist,
                "title": title,
                "has_karaoke_version": False,
                "spotify_track_id": track_id,
                "spotify_artist_id": track["artist_id"],
                "spotify_popularity": track["popularity"],
                "duration_ms": track["duration_ms"],
                "explicit": track["explicit"],
                # Enjoy singing metadata
                "enjoy_singing": True,
                "singing_tags": singing_tags or [],
                "singing_energy": singing_energy,
                "vocal_comfort": vocal_comfort,
                "notes": notes,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }
        else:
            # Karaoke catalog song
            song_id_int = int(song_id)
            song = await loop.run_in_executor(None, self._get_song_by_id, song_id_int)
            if not song:
                raise ValueError(f"Song with ID {song_id} not found in catalog")

            artist = song["artist"]
            title = song["title"]

            user_song_data = {
                "id": user_song_id,
                "user_id": user_id,
                "song_id": song_id,
                "source": "enjoy_singing",
                "play_count": 1,
                "last_played_at": None,
                "is_saved": True,
                "times_sung": 0,
                "last_sung_at": None,
                "average_rating": None,
                "artist": artist,
                "title": title,
                "has_karaoke_version": True,
                "spotify_popularity": None,
                "duration_ms": None,
                "explicit": False,
                # Enjoy singing metadata
                "enjoy_singing": True,
                "singing_tags": singing_tags or [],
                "singing_energy": singing_energy,
                "vocal_comfort": vocal_comfort,
                "notes": notes,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }

        await self.firestore.set_document(
            self.USER_SONGS_COLLECTION,
            user_song_id,
            user_song_data,
            merge=True,
        )

        return SetEnjoySingingResult(
            success=True,
            song_id=song_id,
            artist=artist,
            title=title,
            enjoy_singing=True,
            singing_tags=singing_tags or [],
            singing_energy=singing_energy,
            vocal_comfort=vocal_comfort,
            notes=notes,
            created_new=True,
        )

    async def get_enjoy_singing_songs(
        self,
        user_id: str,
        page: int = 1,
        per_page: int = 20,
    ) -> KnownSongsListResult:
        """Get user's songs marked as enjoy singing.

        Args:
            user_id: User's ID.
            page: Page number (1-indexed).
            per_page: Items per page.

        Returns:
            KnownSongsListResult with paginated songs.
        """
        if page < 1:
            raise ValueError("page must be >= 1")
        if per_page < 1:
            raise ValueError("per_page must be >= 1")

        offset = (page - 1) * per_page

        # Count total enjoy_singing songs for this user
        total = await self.firestore.count_documents(
            self.USER_SONGS_COLLECTION,
            filters=[
                ("user_id", "==", user_id),
                ("enjoy_singing", "==", True),
            ],
        )

        # Get paginated results
        songs = await self.firestore.query_documents(
            self.USER_SONGS_COLLECTION,
            filters=[
                ("user_id", "==", user_id),
                ("enjoy_singing", "==", True),
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

    async def remove_enjoy_singing(
        self,
        user_id: str,
        song_id: str,
    ) -> bool:
        """Remove enjoy singing flag from a song.

        If the song was added solely via enjoy_singing source, deletes it.
        If the song has another source (spotify, lastfm, etc.), just removes the flag.

        Args:
            user_id: User's ID.
            song_id: Song ID - either karaoke catalog ID or "spotify:{track_id}".

        Returns:
            True if updated/removed, False if not found.
        """
        is_spotify = song_id.startswith("spotify:")
        if is_spotify:
            track_id = song_id.replace("spotify:", "")
            user_song_id = f"{user_id}:spotify:{track_id}"
        else:
            user_song_id = f"{user_id}:{song_id}"

        existing = await self.firestore.get_document(self.USER_SONGS_COLLECTION, user_song_id)
        if existing is None:
            return False

        # If source is enjoy_singing, delete the whole record
        if existing.get("source") == "enjoy_singing":
            await self.firestore.delete_document(self.USER_SONGS_COLLECTION, user_song_id)
            return True

        # Otherwise, just clear the enjoy_singing metadata
        update_data = {
            "enjoy_singing": False,
            "singing_tags": [],
            "singing_energy": None,
            "vocal_comfort": None,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        await self.firestore.update_document(self.USER_SONGS_COLLECTION, user_song_id, update_data)
        return True

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

    def _get_spotify_track(self, track_id: str) -> dict | None:
        """Get Spotify track details from BigQuery.

        Args:
            track_id: Spotify track ID.

        Returns:
            Dict with track details or None if not found.
        """
        sql = f"""
            SELECT
                track_id,
                track_name,
                artist_name,
                artist_id,
                popularity,
                duration_ms,
                explicit
            FROM `{self.PROJECT_ID}.{self.DATASET_ID}.spotify_tracks_normalized`
            WHERE track_id = @track_id
            LIMIT 1
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("track_id", "STRING", track_id),
            ]
        )

        results = list(self.bigquery.query(sql, job_config=job_config).result())

        if not results:
            return None

        row = results[0]
        return {
            "track_id": row.track_id,
            "track_name": row.track_name,
            "artist_name": row.artist_name,
            "artist_id": row.artist_id or "",
            "popularity": row.popularity or 0,
            "duration_ms": row.duration_ms or 0,
            "explicit": row.explicit or False,
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
