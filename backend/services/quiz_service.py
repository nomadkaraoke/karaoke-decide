"""Service for quiz-based onboarding.

Provides quiz artists/songs for data-light users and handles quiz submission
to create UserSong records and update user profiles.
"""

import hashlib
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from google.cloud import bigquery

from backend.config import BackendSettings
from backend.services.firestore_service import FirestoreService
from karaoke_decide.core.models import QuizArtist, QuizSong


@dataclass
class QuizStatus:
    """Status of user's quiz completion."""

    completed: bool
    completed_at: datetime | None
    songs_known_count: int


@dataclass
class QuizSubmitResult:
    """Result of quiz submission."""

    songs_added: int
    recommendations_ready: bool


class QuizService:
    """Service for quiz-based onboarding.

    Handles:
    - Fetching popular karaoke songs for quiz
    - Processing quiz submissions
    - Creating UserSong records from quiz responses
    """

    USERS_COLLECTION = "users"
    USER_SONGS_COLLECTION = "user_songs"
    QUIZ_SONGS_CACHE_COLLECTION = "quiz_songs_cache"

    # BigQuery config
    PROJECT_ID = "nomadkaraoke"
    DATASET_ID = "karaoke_decide"

    # Quiz configuration
    DEFAULT_QUIZ_SIZE = 15
    DEFAULT_ARTIST_COUNT = 25  # Number of artists to show in quiz
    MIN_BRAND_COUNT = 5  # Songs must be on at least 5 karaoke brands
    MIN_ARTIST_SONGS = 3  # Artists must have at least 3 karaoke songs
    CACHE_TTL_HOURS = 24  # How long to cache quiz data

    def __init__(
        self,
        settings: BackendSettings,
        firestore: FirestoreService,
        bigquery_client: bigquery.Client | None = None,
    ):
        """Initialize the quiz service.

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

    async def get_quiz_songs(self, count: int = DEFAULT_QUIZ_SIZE) -> list[QuizSong]:
        """Get quiz songs for onboarding.

        Returns popular karaoke songs that users are likely to recognize.
        Songs are selected to provide variety across different artists.

        Args:
            count: Number of quiz songs to return.

        Returns:
            List of QuizSong objects for the quiz.
        """
        # Fetch more candidates than needed to allow randomization
        candidates = self._fetch_quiz_candidates(count * 3)

        # Ensure artist diversity - max 1 song per artist
        seen_artists: set[str] = set()
        diverse_songs: list[QuizSong] = []

        for song in candidates:
            artist_key = song.artist.lower()
            if artist_key not in seen_artists:
                seen_artists.add(artist_key)
                diverse_songs.append(song)

            if len(diverse_songs) >= count * 2:
                break

        # Randomly select final set
        if len(diverse_songs) > count:
            diverse_songs = random.sample(diverse_songs, count)

        return diverse_songs

    async def get_quiz_artists(self, count: int = DEFAULT_ARTIST_COUNT) -> list[QuizArtist]:
        """Get quiz artists for onboarding.

        Returns popular karaoke artists that users are likely to recognize.
        Artists are selected based on total brand coverage and song count.

        Args:
            count: Number of quiz artists to return.

        Returns:
            List of QuizArtist objects for the quiz.
        """
        # Fetch artist candidates with aggregated stats
        candidates = self._fetch_artist_candidates(count * 2)

        # Randomly select final set for variety
        if len(candidates) > count:
            candidates = random.sample(candidates, count)

        return candidates

    def _fetch_artist_candidates(self, limit: int) -> list[QuizArtist]:
        """Fetch quiz artist candidates from BigQuery.

        Gets artists aggregated by total brand coverage and song count.

        Args:
            limit: Maximum number of candidates to fetch.

        Returns:
            List of QuizArtist candidates.
        """
        sql = f"""
            WITH artist_stats AS (
                SELECT
                    Artist as artist_name,
                    COUNT(*) as song_count,
                    SUM(ARRAY_LENGTH(SPLIT(Brands, ','))) as total_brand_count,
                    ARRAY_AGG(Title ORDER BY ARRAY_LENGTH(SPLIT(Brands, ',')) DESC LIMIT 3) as top_songs
                FROM `{self.PROJECT_ID}.{self.DATASET_ID}.karaokenerds_raw`
                WHERE ARRAY_LENGTH(SPLIT(Brands, ',')) >= @min_brands
                GROUP BY Artist
                HAVING COUNT(*) >= @min_songs
            )
            SELECT
                artist_name,
                song_count,
                total_brand_count,
                top_songs
            FROM artist_stats
            ORDER BY total_brand_count DESC, song_count DESC
            LIMIT @limit
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("min_brands", "INT64", self.MIN_BRAND_COUNT),
                bigquery.ScalarQueryParameter("min_songs", "INT64", self.MIN_ARTIST_SONGS),
                bigquery.ScalarQueryParameter("limit", "INT64", limit),
            ]
        )

        results = self.bigquery.query(sql, job_config=job_config).result()

        return [
            QuizArtist(
                name=row.artist_name,
                song_count=row.song_count,
                top_songs=list(row.top_songs) if row.top_songs else [],
                total_brand_count=row.total_brand_count,
                primary_decade="Unknown",  # Will be enhanced later
                image_url=None,  # Will be fetched from Spotify API
            )
            for row in results
        ]

    def _fetch_quiz_candidates(self, limit: int) -> list[QuizSong]:
        """Fetch quiz song candidates from BigQuery.

        Gets popular karaoke songs ordered by brand count (popularity proxy).
        Note: We don't join with Spotify here to avoid duplicates from
        multiple Spotify versions of the same song.

        Args:
            limit: Maximum number of candidates to fetch.

        Returns:
            List of QuizSong candidates.
        """
        sql = f"""
            SELECT
                CAST(Id AS STRING) as id,
                Artist as artist,
                Title as title,
                ARRAY_LENGTH(SPLIT(Brands, ',')) as brand_count
            FROM `{self.PROJECT_ID}.{self.DATASET_ID}.karaokenerds_raw`
            WHERE ARRAY_LENGTH(SPLIT(Brands, ',')) >= @min_brands
            ORDER BY ARRAY_LENGTH(SPLIT(Brands, ',')) DESC
            LIMIT @limit
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("min_brands", "INT64", self.MIN_BRAND_COUNT),
                bigquery.ScalarQueryParameter("limit", "INT64", limit),
            ]
        )

        results = self.bigquery.query(sql, job_config=job_config).result()

        return [
            QuizSong(
                id=row.id,
                artist=row.artist,
                title=row.title,
                decade="Unknown",  # Will be enhanced with release date data later
                popularity=row.brand_count,  # Use brand_count as popularity proxy
                brand_count=row.brand_count,
            )
            for row in results
        ]

    async def submit_quiz(
        self,
        user_id: str,
        known_song_ids: list[str] | None = None,
        known_artists: list[str] | None = None,
        decade_preference: str | None = None,
        energy_preference: Literal["chill", "medium", "high"] | None = None,
    ) -> QuizSubmitResult:
        """Submit quiz responses and update user profile.

        Creates UserSong records for known songs/artists and updates user's
        quiz preferences.

        Args:
            user_id: User's ID.
            known_song_ids: List of song IDs the user recognized (legacy).
            known_artists: List of artist names the user knows.
            decade_preference: User's preferred decade (e.g., "1980s").
            energy_preference: User's preferred energy level.

        Returns:
            QuizSubmitResult with counts.
        """
        now = datetime.now(UTC)
        songs_added = 0
        known_song_ids = known_song_ids or []
        known_artists = known_artists or []

        # If artists were selected, get their top songs
        if known_artists:
            artist_songs = self._get_songs_by_artists(known_artists, limit_per_artist=5)
            for song in artist_songs:
                user_song_id = f"{user_id}:{song['id']}"

                # Check if already exists
                existing = await self.firestore.get_document(self.USER_SONGS_COLLECTION, user_song_id)

                if existing is None:
                    user_song_data = {
                        "id": user_song_id,
                        "user_id": user_id,
                        "song_id": song["id"],
                        "source": "quiz",
                        "play_count": 1,
                        "last_played_at": None,
                        "is_saved": False,
                        "times_sung": 0,
                        "last_sung_at": None,
                        "average_rating": None,
                        "notes": None,
                        "artist": song["artist"],
                        "title": song["title"],
                        "created_at": now.isoformat(),
                        "updated_at": now.isoformat(),
                    }
                    await self.firestore.set_document(
                        self.USER_SONGS_COLLECTION,
                        user_song_id,
                        user_song_data,
                    )
                    songs_added += 1

        # Also handle direct song selections (legacy support)
        if known_song_ids:
            song_details = self._get_songs_by_ids(known_song_ids)

            for song in song_details:
                user_song_id = f"{user_id}:{song['id']}"

                existing = await self.firestore.get_document(self.USER_SONGS_COLLECTION, user_song_id)

                if existing is None:
                    user_song_data = {
                        "id": user_song_id,
                        "user_id": user_id,
                        "song_id": song["id"],
                        "source": "quiz",
                        "play_count": 1,
                        "last_played_at": None,
                        "is_saved": False,
                        "times_sung": 0,
                        "last_sung_at": None,
                        "average_rating": None,
                        "notes": None,
                        "artist": song["artist"],
                        "title": song["title"],
                        "created_at": now.isoformat(),
                        "updated_at": now.isoformat(),
                    }
                    await self.firestore.set_document(
                        self.USER_SONGS_COLLECTION,
                        user_song_id,
                        user_song_data,
                    )
                    songs_added += 1

        # Update user profile with quiz data
        await self._update_user_quiz_data(
            user_id,
            known_song_ids,
            known_artists,
            decade_preference,
            energy_preference,
            now,
        )

        return QuizSubmitResult(
            songs_added=songs_added,
            recommendations_ready=len(known_song_ids) > 0 or len(known_artists) > 0,
        )

    def _get_songs_by_artists(self, artist_names: list[str], limit_per_artist: int = 5) -> list[dict]:
        """Get top songs for given artists from BigQuery.

        Args:
            artist_names: List of artist names.
            limit_per_artist: Max songs per artist.

        Returns:
            List of dicts with id, artist, title.
        """
        if not artist_names:
            return []

        # Build parameterized query
        placeholders = ", ".join([f"@artist_{i}" for i in range(len(artist_names))])
        sql = f"""
            WITH ranked_songs AS (
                SELECT
                    CAST(Id AS STRING) as id,
                    Artist as artist,
                    Title as title,
                    ARRAY_LENGTH(SPLIT(Brands, ',')) as brand_count,
                    ROW_NUMBER() OVER (PARTITION BY Artist ORDER BY ARRAY_LENGTH(SPLIT(Brands, ',')) DESC) as rn
                FROM `{self.PROJECT_ID}.{self.DATASET_ID}.karaokenerds_raw`
                WHERE LOWER(Artist) IN ({placeholders})
            )
            SELECT id, artist, title
            FROM ranked_songs
            WHERE rn <= @limit_per_artist
        """

        params = [
            bigquery.ScalarQueryParameter(f"artist_{i}", "STRING", name.lower()) for i, name in enumerate(artist_names)
        ]
        params.append(bigquery.ScalarQueryParameter("limit_per_artist", "INT64", limit_per_artist))

        job_config = bigquery.QueryJobConfig(query_parameters=params)
        results = self.bigquery.query(sql, job_config=job_config).result()

        return [{"id": row.id, "artist": row.artist, "title": row.title} for row in results]

    def _get_songs_by_ids(self, song_ids: list[str]) -> list[dict]:
        """Get song details by IDs from BigQuery.

        Args:
            song_ids: List of song IDs to fetch.

        Returns:
            List of dicts with id, artist, title.
        """
        if not song_ids:
            return []

        # Build parameterized query for multiple IDs
        placeholders = ", ".join([f"@id_{i}" for i in range(len(song_ids))])
        sql = f"""
            SELECT
                CAST(Id AS STRING) as id,
                Artist as artist,
                Title as title
            FROM `{self.PROJECT_ID}.{self.DATASET_ID}.karaokenerds_raw`
            WHERE CAST(Id AS STRING) IN ({placeholders})
        """

        params = [bigquery.ScalarQueryParameter(f"id_{i}", "STRING", song_id) for i, song_id in enumerate(song_ids)]

        job_config = bigquery.QueryJobConfig(query_parameters=params)
        results = self.bigquery.query(sql, job_config=job_config).result()

        return [{"id": row.id, "artist": row.artist, "title": row.title} for row in results]

    async def _update_user_quiz_data(
        self,
        user_id: str,
        known_song_ids: list[str],
        known_artists: list[str],
        decade_preference: str | None,
        energy_preference: Literal["chill", "medium", "high"] | None,
        completed_at: datetime,
    ) -> None:
        """Update user profile with quiz data.

        Args:
            user_id: User's ID.
            known_song_ids: Song IDs from quiz.
            known_artists: Artist names from quiz.
            decade_preference: Decade preference.
            energy_preference: Energy preference.
            completed_at: Quiz completion timestamp.
        """
        # For guest users, store by user_id directly
        if user_id.startswith("guest_"):
            doc_id = user_id
        else:
            # Find user document by user_id for verified users
            docs = await self.firestore.query_documents(
                self.USERS_COLLECTION,
                filters=[("user_id", "==", user_id)],
                limit=1,
            )

            if not docs:
                return

            doc = docs[0]
            # Handle case where email might be None
            if doc.get("email"):
                doc_id = self._hash_email(doc["email"])
            else:
                doc_id = user_id

        # Update quiz fields
        update_data = {
            "quiz_completed_at": completed_at.isoformat(),
            "quiz_songs_known": known_song_ids,
            "quiz_artists_known": known_artists,
            "updated_at": completed_at.isoformat(),
        }

        if decade_preference is not None:
            update_data["quiz_decade_pref"] = decade_preference

        if energy_preference is not None:
            update_data["quiz_energy_pref"] = energy_preference

        await self.firestore.update_document(
            self.USERS_COLLECTION,
            doc_id,
            update_data,
        )

    def _hash_email(self, email: str) -> str:
        """Hash an email address for document ID lookup.

        Args:
            email: Email address.

        Returns:
            SHA-256 hash of lowercase email.
        """
        return hashlib.sha256(email.lower().encode()).hexdigest()

    async def get_quiz_status(self, user_id: str) -> QuizStatus:
        """Get user's quiz completion status.

        Args:
            user_id: User's ID.

        Returns:
            QuizStatus with completion info.
        """
        docs = await self.firestore.query_documents(
            self.USERS_COLLECTION,
            filters=[("user_id", "==", user_id)],
            limit=1,
        )

        if not docs:
            return QuizStatus(
                completed=False,
                completed_at=None,
                songs_known_count=0,
            )

        doc = docs[0]
        completed_at_str = doc.get("quiz_completed_at")
        quiz_songs = doc.get("quiz_songs_known", [])

        return QuizStatus(
            completed=completed_at_str is not None,
            completed_at=(datetime.fromisoformat(completed_at_str) if completed_at_str else None),
            songs_known_count=len(quiz_songs),
        )


# Lazy initialization
_quiz_service: QuizService | None = None


def get_quiz_service(
    settings: BackendSettings | None = None,
    firestore: FirestoreService | None = None,
) -> QuizService:
    """Get the quiz service instance.

    Args:
        settings: Optional settings override.
        firestore: Optional Firestore service override.

    Returns:
        QuizService instance.
    """
    global _quiz_service

    if _quiz_service is None or settings is not None or firestore is not None:
        if settings is None:
            from backend.config import get_backend_settings

            settings = get_backend_settings()
        if firestore is None:
            firestore = FirestoreService(settings)

        _quiz_service = QuizService(settings, firestore)

    return _quiz_service
