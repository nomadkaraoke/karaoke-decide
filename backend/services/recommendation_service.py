"""Service for generating personalized song recommendations.

Analyzes user's listening history and quiz responses to recommend
karaoke songs they might enjoy singing.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from google.cloud import bigquery

from backend.config import BackendSettings
from backend.services.firestore_service import FirestoreService
from karaoke_decide.core.models import Recommendation, UserSong


@dataclass
class UserContext:
    """Context about a user for generating recommendations."""

    user_id: str
    known_artists: set[str]  # Lowercase artist names
    known_song_ids: set[str]
    quiz_decade_pref: str | None
    quiz_energy_pref: str | None
    total_songs: int


@dataclass
class ScoredSong:
    """Song with computed recommendation score."""

    song_id: str
    artist: str
    title: str
    brand_count: int
    spotify_popularity: int
    score: float
    reason: str
    reason_type: Literal[
        "known_artist",
        "similar_genre",
        "decade_match",
        "crowd_pleaser",
        "popular",
    ]


class RecommendationService:
    """Service for generating song recommendations.

    Uses a weighted scoring algorithm considering:
    - Artist familiarity (from listening history/quiz)
    - Karaoke popularity (brand count)
    - Spotify popularity
    - User preferences (decade, energy)
    """

    USER_SONGS_COLLECTION = "user_songs"
    USERS_COLLECTION = "users"

    # BigQuery config
    PROJECT_ID = "nomadkaraoke"
    DATASET_ID = "karaoke_decide"

    # Algorithm weights
    ARTIST_MATCH_WEIGHT = 0.35
    POPULARITY_WEIGHT = 0.25
    KARAOKE_AVAILABILITY_WEIGHT = 0.20
    GENRE_WEIGHT = 0.12
    DECADE_WEIGHT = 0.08

    # Thresholds
    MIN_BRAND_COUNT = 3  # Minimum brands for recommendation
    DEFAULT_LIMIT = 20

    def __init__(
        self,
        settings: BackendSettings,
        firestore: FirestoreService,
        bigquery_client: bigquery.Client | None = None,
    ):
        """Initialize the recommendation service.

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

    async def get_recommendations(
        self,
        user_id: str,
        limit: int = DEFAULT_LIMIT,
        decade: str | None = None,
        min_popularity: int | None = None,
    ) -> list[Recommendation]:
        """Get personalized song recommendations for a user.

        Args:
            user_id: User's ID.
            limit: Maximum number of recommendations.
            decade: Optional decade filter (e.g., "1980s").
            min_popularity: Minimum Spotify popularity score.

        Returns:
            List of Recommendation objects, sorted by score.
        """
        # Build user context
        context = await self._build_user_context(user_id)

        if context.total_songs == 0 and not context.quiz_decade_pref:
            # Cold start - no data, return crowd pleasers
            return await self._get_crowd_pleasers(limit)

        # Get candidate songs
        scored_songs = await self._score_candidates(context, limit * 3)

        # Apply filters
        if decade:
            # Note: decade filtering requires release date data we don't have yet
            pass

        if min_popularity:
            scored_songs = [s for s in scored_songs if s.spotify_popularity >= min_popularity]

        # Sort by score and take top N
        scored_songs.sort(key=lambda x: x.score, reverse=True)
        top_songs = scored_songs[:limit]

        # Convert to Recommendation models
        return [
            Recommendation(
                song_id=song.song_id,
                artist=song.artist,
                title=song.title,
                score=round(song.score, 3),
                reason=song.reason,
                reason_type=song.reason_type,
                brand_count=song.brand_count,
                popularity=song.spotify_popularity,
            )
            for song in top_songs
        ]

    async def get_user_songs(
        self,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[UserSong], int]:
        """Get user's songs from listening history.

        Args:
            user_id: User's ID.
            limit: Maximum number of songs.
            offset: Pagination offset.

        Returns:
            Tuple of (list of UserSong, total count).
        """
        # Get total count
        total = await self.firestore.count_documents(
            self.USER_SONGS_COLLECTION,
            filters=[("user_id", "==", user_id)],
        )

        # Get paginated results
        docs = await self.firestore.query_documents(
            self.USER_SONGS_COLLECTION,
            filters=[("user_id", "==", user_id)],
            order_by="play_count",
            order_direction="DESCENDING",
            limit=limit,
            offset=offset,
        )

        songs = [
            UserSong(
                id=doc["id"],
                user_id=doc["user_id"],
                song_id=doc["song_id"],
                source=doc.get("source", "spotify"),
                play_count=doc.get("play_count", 0),
                last_played_at=(datetime.fromisoformat(doc["last_played_at"]) if doc.get("last_played_at") else None),
                is_saved=doc.get("is_saved", False),
                times_sung=doc.get("times_sung", 0),
                last_sung_at=(datetime.fromisoformat(doc["last_sung_at"]) if doc.get("last_sung_at") else None),
                average_rating=doc.get("average_rating"),
                notes=doc.get("notes"),
                artist=doc["artist"],
                title=doc["title"],
                created_at=(datetime.fromisoformat(doc["created_at"]) if doc.get("created_at") else datetime.now(UTC)),
                updated_at=(datetime.fromisoformat(doc["updated_at"]) if doc.get("updated_at") else datetime.now(UTC)),
            )
            for doc in docs
        ]

        return songs, total

    async def get_user_artists(
        self,
        user_id: str,
        source: str | None = None,
        time_range: str | None = None,
        limit: int = 100,
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        """Get user's top artists from listening history.

        Args:
            user_id: User's ID.
            source: Optional filter by source (spotify, lastfm).
            time_range: Optional filter by time range.
            limit: Maximum number of artists.

        Returns:
            Tuple of (list of artist dicts, source counts dict).
        """
        # Build filters
        filters: list[tuple[str, str, Any]] = [("user_id", "==", user_id)]
        if source:
            filters.append(("source", "==", source))
        if time_range:
            filters.append(("time_range", "==", time_range))

        # Get artists sorted by rank
        docs = await self.firestore.query_documents(
            "user_artists",
            filters=filters,
            order_by="rank",
            order_direction="ASCENDING",
            limit=limit,
        )

        # Count by source
        source_counts: dict[str, int] = {}
        for doc in docs:
            src = doc.get("source", "unknown")
            source_counts[src] = source_counts.get(src, 0) + 1

        return docs, source_counts

    async def _build_user_context(self, user_id: str) -> UserContext:
        """Build context about user for scoring.

        Args:
            user_id: User's ID.

        Returns:
            UserContext with aggregated data.
        """
        # Get user's songs
        docs = await self.firestore.query_documents(
            self.USER_SONGS_COLLECTION,
            filters=[("user_id", "==", user_id)],
            limit=1000,  # Get all for context building
        )

        known_artists: set[str] = set()
        known_song_ids: set[str] = set()

        for doc in docs:
            known_artists.add(doc["artist"].lower())
            known_song_ids.add(doc["song_id"])

        # Get user preferences from quiz
        user_docs = await self.firestore.query_documents(
            self.USERS_COLLECTION,
            filters=[("user_id", "==", user_id)],
            limit=1,
        )

        quiz_decade_pref = None
        quiz_energy_pref = None

        if user_docs:
            user_doc = user_docs[0]
            quiz_decade_pref = user_doc.get("quiz_decade_pref")
            quiz_energy_pref = user_doc.get("quiz_energy_pref")

        return UserContext(
            user_id=user_id,
            known_artists=known_artists,
            known_song_ids=known_song_ids,
            quiz_decade_pref=quiz_decade_pref,
            quiz_energy_pref=quiz_energy_pref,
            total_songs=len(known_song_ids),
        )

    async def _score_candidates(
        self,
        context: UserContext,
        limit: int,
    ) -> list[ScoredSong]:
        """Score candidate songs for recommendation.

        Args:
            context: User context.
            limit: Maximum candidates to score.

        Returns:
            List of scored songs.
        """
        scored: list[ScoredSong] = []

        # Strategy 1: Songs from known artists (not already in library)
        if context.known_artists:
            artist_songs = self._get_songs_by_artists(
                list(context.known_artists)[:50],  # Limit artists
                limit=limit,
            )

            for song in artist_songs:
                if song["id"] not in context.known_song_ids:
                    score = self._calculate_score(song, context, is_known_artist=True)
                    scored.append(
                        ScoredSong(
                            song_id=song["id"],
                            artist=song["artist"],
                            title=song["title"],
                            brand_count=song["brand_count"],
                            spotify_popularity=song.get("spotify_popularity", 0),
                            score=score,
                            reason=f"You listen to {song['artist']}",
                            reason_type="known_artist",
                        )
                    )

        # Strategy 2: Popular karaoke songs (crowd pleasers)
        if len(scored) < limit:
            popular_songs = self._get_popular_songs(limit=limit)

            for song in popular_songs:
                if song["id"] not in context.known_song_ids:
                    # Check if we already have this song from artist match
                    existing = next((s for s in scored if s.song_id == song["id"]), None)
                    if existing:
                        continue

                    score = self._calculate_score(song, context, is_known_artist=False)
                    scored.append(
                        ScoredSong(
                            song_id=song["id"],
                            artist=song["artist"],
                            title=song["title"],
                            brand_count=song["brand_count"],
                            spotify_popularity=song.get("spotify_popularity", 0),
                            score=score,
                            reason="Popular karaoke song",
                            reason_type="crowd_pleaser",
                        )
                    )

        return scored

    def _calculate_score(
        self,
        song: dict[str, Any],
        context: UserContext,
        is_known_artist: bool,
    ) -> float:
        """Calculate recommendation score for a song.

        Args:
            song: Song data dict.
            context: User context.
            is_known_artist: Whether user knows this artist.

        Returns:
            Score between 0 and 1.
        """
        score = 0.0

        # Artist match component
        if is_known_artist:
            score += self.ARTIST_MATCH_WEIGHT

        # Popularity component (normalize Spotify popularity 0-100 to 0-1)
        spotify_pop = song.get("spotify_popularity", 0) / 100.0
        score += self.POPULARITY_WEIGHT * spotify_pop

        # Karaoke availability component (normalize brand count)
        # Most songs have 1-15 brands, normalize with cap at 10
        brand_score = min(song["brand_count"], 10) / 10.0
        score += self.KARAOKE_AVAILABILITY_WEIGHT * brand_score

        # Decade preference bonus
        # Note: We don't have decade data yet, but structure is ready
        # if context.quiz_decade_pref and song.get("decade") == context.quiz_decade_pref:
        #     score += self.DECADE_WEIGHT

        capped_score: float = min(score, 1.0)  # Cap at 1.0
        return capped_score

    def _get_songs_by_artists(
        self,
        artists: list[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        """Get songs by multiple artists from BigQuery.

        Args:
            artists: List of artist names (lowercase).
            limit: Maximum songs to return.

        Returns:
            List of song dicts.
        """
        if not artists:
            return []

        # Build parameterized query with GROUP BY to avoid duplicates from Spotify join
        # (one karaoke song can match many Spotify tracks for different releases)
        placeholders = ", ".join([f"@artist_{i}" for i in range(len(artists))])
        sql = f"""
            SELECT
                CAST(k.Id AS STRING) as id,
                k.Artist as artist,
                k.Title as title,
                ARRAY_LENGTH(SPLIT(k.Brands, ',')) as brand_count,
                COALESCE(MAX(s.popularity), 0) as spotify_popularity
            FROM `{self.PROJECT_ID}.{self.DATASET_ID}.karaokenerds_raw` k
            LEFT JOIN `{self.PROJECT_ID}.{self.DATASET_ID}.spotify_tracks` s
                ON LOWER(k.Artist) = LOWER(s.artist_name)
                AND LOWER(k.Title) = LOWER(s.title)
            WHERE LOWER(k.Artist) IN ({placeholders})
                AND ARRAY_LENGTH(SPLIT(k.Brands, ',')) >= @min_brands
            GROUP BY k.Id, k.Artist, k.Title, k.Brands
            ORDER BY ARRAY_LENGTH(SPLIT(k.Brands, ',')) DESC
            LIMIT @limit
        """

        params = [bigquery.ScalarQueryParameter(f"artist_{i}", "STRING", artist) for i, artist in enumerate(artists)]
        params.append(bigquery.ScalarQueryParameter("min_brands", "INT64", self.MIN_BRAND_COUNT))
        params.append(bigquery.ScalarQueryParameter("limit", "INT64", limit))

        job_config = bigquery.QueryJobConfig(query_parameters=params)
        results = self.bigquery.query(sql, job_config=job_config).result()

        return [
            {
                "id": row.id,
                "artist": row.artist,
                "title": row.title,
                "brand_count": row.brand_count,
                "spotify_popularity": row.spotify_popularity,
            }
            for row in results
        ]

    def _get_popular_songs(self, limit: int) -> list[dict[str, Any]]:
        """Get popular karaoke songs from BigQuery.

        Args:
            limit: Maximum songs to return.

        Returns:
            List of song dicts.
        """
        # GROUP BY to avoid duplicates from Spotify join
        # (one karaoke song can match many Spotify tracks for different releases)
        sql = f"""
            SELECT
                CAST(k.Id AS STRING) as id,
                k.Artist as artist,
                k.Title as title,
                ARRAY_LENGTH(SPLIT(k.Brands, ',')) as brand_count,
                COALESCE(MAX(s.popularity), 0) as spotify_popularity
            FROM `{self.PROJECT_ID}.{self.DATASET_ID}.karaokenerds_raw` k
            LEFT JOIN `{self.PROJECT_ID}.{self.DATASET_ID}.spotify_tracks` s
                ON LOWER(k.Artist) = LOWER(s.artist_name)
                AND LOWER(k.Title) = LOWER(s.title)
            WHERE ARRAY_LENGTH(SPLIT(k.Brands, ',')) >= @min_brands
            GROUP BY k.Id, k.Artist, k.Title, k.Brands
            ORDER BY
                ARRAY_LENGTH(SPLIT(k.Brands, ',')) DESC,
                spotify_popularity DESC
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
            {
                "id": row.id,
                "artist": row.artist,
                "title": row.title,
                "brand_count": row.brand_count,
                "spotify_popularity": row.spotify_popularity,
            }
            for row in results
        ]

    async def _get_crowd_pleasers(self, limit: int) -> list[Recommendation]:
        """Get crowd-pleaser recommendations for cold start users.

        Args:
            limit: Maximum recommendations.

        Returns:
            List of Recommendation objects.
        """
        songs = self._get_popular_songs(limit)

        return [
            Recommendation(
                song_id=song["id"],
                artist=song["artist"],
                title=song["title"],
                score=0.5,  # Neutral score for cold start
                reason="Popular karaoke song",
                reason_type="crowd_pleaser",
                brand_count=song["brand_count"],
                popularity=song.get("spotify_popularity", 0),
            )
            for song in songs
        ]


# Lazy initialization
_recommendation_service: RecommendationService | None = None


def get_recommendation_service(
    settings: BackendSettings | None = None,
    firestore: FirestoreService | None = None,
) -> RecommendationService:
    """Get the recommendation service instance.

    Args:
        settings: Optional settings override.
        firestore: Optional Firestore service override.

    Returns:
        RecommendationService instance.
    """
    global _recommendation_service

    if _recommendation_service is None or settings is not None or firestore is not None:
        if settings is None:
            from backend.config import get_backend_settings

            settings = get_backend_settings()
        if firestore is None:
            firestore = FirestoreService(settings)

        _recommendation_service = RecommendationService(settings, firestore)

    return _recommendation_service
