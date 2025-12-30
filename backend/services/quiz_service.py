"""Service for quiz-based onboarding.

Provides quiz songs for data-light users and handles quiz submission
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
from karaoke_decide.core.models import QuizSong


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
    MIN_BRAND_COUNT = 5  # Songs must be on at least 5 karaoke brands
    CACHE_TTL_HOURS = 24  # How long to cache quiz songs

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

    def _fetch_quiz_candidates(self, limit: int) -> list[QuizSong]:
        """Fetch quiz song candidates from BigQuery.

        Gets popular karaoke songs ordered by brand count (popularity proxy).

        Args:
            limit: Maximum number of candidates to fetch.

        Returns:
            List of QuizSong candidates.
        """
        sql = f"""
            SELECT
                CAST(k.Id AS STRING) as id,
                k.Artist as artist,
                k.Title as title,
                ARRAY_LENGTH(SPLIT(k.Brands, ',')) as brand_count,
                COALESCE(s.popularity, 0) as spotify_popularity
            FROM `{self.PROJECT_ID}.{self.DATASET_ID}.karaokenerds_raw` k
            LEFT JOIN `{self.PROJECT_ID}.{self.DATASET_ID}.spotify_tracks` s
                ON LOWER(k.Artist) = LOWER(s.artist_name)
                AND LOWER(k.Title) = LOWER(s.title)
            WHERE ARRAY_LENGTH(SPLIT(k.Brands, ',')) >= @min_brands
            ORDER BY
                ARRAY_LENGTH(SPLIT(k.Brands, ',')) DESC,
                COALESCE(s.popularity, 0) DESC
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
                popularity=row.spotify_popularity,
                brand_count=row.brand_count,
            )
            for row in results
        ]

    async def submit_quiz(
        self,
        user_id: str,
        known_song_ids: list[str],
        decade_preference: str | None = None,
        energy_preference: Literal["chill", "medium", "high"] | None = None,
    ) -> QuizSubmitResult:
        """Submit quiz responses and update user profile.

        Creates UserSong records for known songs and updates user's
        quiz preferences.

        Args:
            user_id: User's ID.
            known_song_ids: List of song IDs the user recognized.
            decade_preference: User's preferred decade (e.g., "1980s").
            energy_preference: User's preferred energy level.

        Returns:
            QuizSubmitResult with counts.
        """
        now = datetime.now(UTC)
        songs_added = 0

        # Get song details for the known songs
        if known_song_ids:
            song_details = self._get_songs_by_ids(known_song_ids)

            # Create UserSong records for each known song
            for song in song_details:
                user_song_id = f"{user_id}:{song['id']}"

                # Check if already exists
                existing = await self.firestore.get_document(self.USER_SONGS_COLLECTION, user_song_id)

                if existing is None:
                    # Create new UserSong from quiz
                    user_song_data = {
                        "id": user_song_id,
                        "user_id": user_id,
                        "song_id": song["id"],
                        "source": "quiz",
                        "play_count": 1,  # Implicit: they know it
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
            decade_preference,
            energy_preference,
            now,
        )

        return QuizSubmitResult(
            songs_added=songs_added,
            recommendations_ready=len(known_song_ids) > 0,
        )

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
        decade_preference: str | None,
        energy_preference: Literal["chill", "medium", "high"] | None,
        completed_at: datetime,
    ) -> None:
        """Update user profile with quiz data.

        Args:
            user_id: User's ID.
            known_song_ids: Song IDs from quiz.
            decade_preference: Decade preference.
            energy_preference: Energy preference.
            completed_at: Quiz completion timestamp.
        """
        # Find user document by user_id
        docs = await self.firestore.query_documents(
            self.USERS_COLLECTION,
            filters=[("user_id", "==", user_id)],
            limit=1,
        )

        if not docs:
            return

        # Get document ID (email hash)
        doc = docs[0]
        doc_id = self._hash_email(doc["email"])

        # Update quiz fields
        update_data = {
            "quiz_completed_at": completed_at.isoformat(),
            "quiz_songs_known": known_song_ids,
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
