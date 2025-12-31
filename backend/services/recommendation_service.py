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
        "generate_karaoke",
    ]
    has_karaoke_version: bool = True
    duration_ms: int | None = None
    explicit: bool = False
    is_classic: bool = False  # brand_count >= 20


@dataclass
class CategorizedRecommendations:
    """Recommendations organized into categories."""

    from_artists_you_know: list["Recommendation"]
    create_your_own: list["Recommendation"]
    crowd_pleasers: list["Recommendation"]
    total_count: int
    filters_applied: dict[str, Any]


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
    MAX_SONGS_PER_ARTIST = 3  # Artist diversity limit
    CLASSIC_THRESHOLD = 20  # Brand count for "all-time classics"

    # Section limits for categorized recommendations
    KNOWN_ARTIST_LIMIT = 15
    GENERATE_SECTION_LIMIT = 10
    CROWD_PLEASER_LIMIT = 10

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

        # Sort by score and apply artist diversity
        scored_songs.sort(key=lambda x: x.score, reverse=True)
        diverse_songs = self._apply_artist_diversity(scored_songs)
        top_songs = diverse_songs[:limit]

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

    async def get_categorized_recommendations(
        self,
        user_id: str,
        has_karaoke: bool | None = None,
        min_popularity: int | None = None,
        max_popularity: int | None = None,
        exclude_explicit: bool = False,
        min_duration_ms: int | None = None,
        max_duration_ms: int | None = None,
        classics_only: bool = False,
    ) -> CategorizedRecommendations:
        """Get recommendations organized into categories.

        Args:
            user_id: User's ID.
            has_karaoke: Filter by karaoke availability (None=all).
            min_popularity: Minimum Spotify popularity (0-100).
            max_popularity: Maximum Spotify popularity (for "hidden gems").
            exclude_explicit: Hide explicit content.
            min_duration_ms: Minimum song duration.
            max_duration_ms: Maximum song duration.
            classics_only: Only show songs with brand_count >= CLASSIC_THRESHOLD.

        Returns:
            CategorizedRecommendations with three sections.
        """
        # Build user context
        context = await self._build_user_context(user_id)

        filters_applied = {
            "has_karaoke": has_karaoke,
            "min_popularity": min_popularity,
            "max_popularity": max_popularity,
            "exclude_explicit": exclude_explicit,
            "min_duration_ms": min_duration_ms,
            "max_duration_ms": max_duration_ms,
            "classics_only": classics_only,
        }

        # Section 1: From Artists You Know (karaoke songs by known artists)
        from_artists: list[Recommendation] = []
        if context.known_artists and has_karaoke is not False:
            artist_songs = await self._score_candidates(context, self.KNOWN_ARTIST_LIMIT * 5)
            # Filter to only known artist songs
            artist_songs = [s for s in artist_songs if s.reason_type == "known_artist"]
            artist_songs = self._apply_filters(
                artist_songs,
                min_popularity=min_popularity,
                max_popularity=max_popularity,
                classics_only=classics_only,
            )
            artist_songs.sort(key=lambda x: x.score, reverse=True)
            artist_songs = self._apply_artist_diversity(artist_songs)
            from_artists = self._convert_to_recommendations(artist_songs[: self.KNOWN_ARTIST_LIMIT])

        # Section 2: Create Your Own Karaoke (songs without karaoke version)
        create_your_own: list[Recommendation] = []
        if has_karaoke is not True:
            # Get user's songs that don't have karaoke versions
            user_songs_without_karaoke = await self._get_user_songs_without_karaoke(
                user_id, context, limit=self.GENERATE_SECTION_LIMIT * 3
            )
            # Apply filters
            user_songs_without_karaoke = self._apply_filters(
                user_songs_without_karaoke,
                min_popularity=min_popularity,
                max_popularity=max_popularity,
                classics_only=False,  # Can't be classics without karaoke
            )
            user_songs_without_karaoke = self._apply_artist_diversity(user_songs_without_karaoke)
            create_your_own = self._convert_to_recommendations(
                user_songs_without_karaoke[: self.GENERATE_SECTION_LIMIT]
            )

        # Section 3: Crowd Pleasers (popular karaoke songs for discovery)
        crowd_pleasers: list[Recommendation] = []
        if has_karaoke is not False:
            popular_songs = self._get_popular_songs(limit=self.CROWD_PLEASER_LIMIT * 5)
            scored_popular = []
            for song in popular_songs:
                if song["id"] not in context.known_song_ids:
                    score = self._calculate_score(song, context, is_known_artist=False)
                    scored_popular.append(
                        ScoredSong(
                            song_id=song["id"],
                            artist=song["artist"],
                            title=song["title"],
                            brand_count=song["brand_count"],
                            spotify_popularity=song.get("spotify_popularity", 0),
                            score=score,
                            reason="Popular karaoke song",
                            reason_type="crowd_pleaser",
                            is_classic=song["brand_count"] >= self.CLASSIC_THRESHOLD,
                        )
                    )
            scored_popular = self._apply_filters(
                scored_popular,
                min_popularity=min_popularity,
                max_popularity=max_popularity,
                classics_only=classics_only,
            )
            scored_popular.sort(key=lambda x: x.score, reverse=True)
            scored_popular = self._apply_artist_diversity(scored_popular)
            crowd_pleasers = self._convert_to_recommendations(scored_popular[: self.CROWD_PLEASER_LIMIT])

        total_count = len(from_artists) + len(create_your_own) + len(crowd_pleasers)

        return CategorizedRecommendations(
            from_artists_you_know=from_artists,
            create_your_own=create_your_own,
            crowd_pleasers=crowd_pleasers,
            total_count=total_count,
            filters_applied=filters_applied,
        )

    def _apply_filters(
        self,
        songs: list[ScoredSong],
        min_popularity: int | None = None,
        max_popularity: int | None = None,
        classics_only: bool = False,
    ) -> list[ScoredSong]:
        """Apply common filters to a list of scored songs.

        Args:
            songs: List of scored songs.
            min_popularity: Minimum Spotify popularity.
            max_popularity: Maximum Spotify popularity.
            classics_only: Only include classics (brand_count >= CLASSIC_THRESHOLD).

        Returns:
            Filtered list.
        """
        result = songs

        if min_popularity is not None:
            result = [s for s in result if s.spotify_popularity >= min_popularity]

        if max_popularity is not None:
            result = [s for s in result if s.spotify_popularity <= max_popularity]

        if classics_only:
            result = [s for s in result if s.brand_count >= self.CLASSIC_THRESHOLD]

        return result

    def _convert_to_recommendations(self, songs: list[ScoredSong]) -> list[Recommendation]:
        """Convert scored songs to Recommendation models.

        Args:
            songs: List of scored songs.

        Returns:
            List of Recommendation objects.
        """
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
                has_karaoke_version=song.has_karaoke_version,
                duration_ms=song.duration_ms,
                explicit=song.explicit,
                is_classic=song.is_classic,
            )
            for song in songs
        ]

    async def _get_user_songs_without_karaoke(
        self,
        user_id: str,
        context: UserContext,
        limit: int,
    ) -> list[ScoredSong]:
        """Get user's songs that don't have karaoke versions.

        Args:
            user_id: User's ID.
            context: User context.
            limit: Maximum songs to return.

        Returns:
            List of scored songs (with has_karaoke_version=False).
        """
        # Query user_songs for tracks without karaoke
        docs = await self.firestore.query_documents(
            self.USER_SONGS_COLLECTION,
            filters=[
                ("user_id", "==", user_id),
                ("has_karaoke_version", "==", False),
            ],
            order_by="play_count",
            order_direction="DESCENDING",
            limit=limit,
        )

        scored: list[ScoredSong] = []
        for doc in docs:
            artist = doc.get("artist", "")
            is_known = artist.lower() in context.known_artists

            # Build a song dict for scoring
            song_data = {
                "spotify_popularity": doc.get("spotify_popularity", 50),
                "brand_count": 0,  # No karaoke = 0 brands
            }
            score = self._calculate_score(song_data, context, is_known_artist=is_known, has_karaoke=False)

            scored.append(
                ScoredSong(
                    song_id=doc.get("song_id", ""),
                    artist=artist,
                    title=doc.get("title", ""),
                    brand_count=0,
                    spotify_popularity=doc.get("spotify_popularity", 50),
                    score=score,
                    reason=f"Generate karaoke for {artist}",
                    reason_type="generate_karaoke",
                    has_karaoke_version=False,
                    duration_ms=doc.get("duration_ms"),
                    explicit=doc.get("explicit", False),
                    is_classic=False,
                )
            )

        return scored

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
        has_karaoke: bool = True,
    ) -> float:
        """Calculate recommendation score for a song.

        Uses diminishing returns for popularity and brand count to
        prevent songs with extremely high values from dominating.

        Args:
            song: Song data dict.
            context: User context.
            is_known_artist: Whether user knows this artist.
            has_karaoke: Whether song has karaoke version.

        Returns:
            Score between 0 and 1.
        """
        score = 0.0

        # Artist match component (0.35)
        if is_known_artist:
            score += self.ARTIST_MATCH_WEIGHT

        # Popularity component with diminishing returns (0.25)
        # Use sqrt for curve: 100 pop = 0.25, 25 pop = 0.125, 4 pop = 0.05
        spotify_pop = song.get("spotify_popularity", 0)
        pop_score = (spotify_pop / 100.0) ** 0.5  # sqrt curve
        score += self.POPULARITY_WEIGHT * pop_score

        # Karaoke availability with diminishing returns (0.20)
        if has_karaoke:
            brand_count = song.get("brand_count", 0)
            # First 5 brands worth more, then diminishing
            if brand_count <= 5:
                brand_score = brand_count / 5.0
            else:
                # 5 brands = 1.0, 10 brands = 1.3, 20+ brands = 1.5 (capped)
                base = 1.0
                extra = min(brand_count - 5, 15) / 15.0 * 0.5
                brand_score = base + extra
            # Normalize to 0-1
            brand_score = min(brand_score / 1.5, 1.0)
            score += self.KARAOKE_AVAILABILITY_WEIGHT * brand_score

        # Decade preference bonus (if we have data)
        if context.quiz_decade_pref and song.get("decade") == context.quiz_decade_pref:
            score += self.DECADE_WEIGHT

        capped_score: float = min(score, 1.0)  # Cap at 1.0
        return capped_score

    def _apply_artist_diversity(
        self,
        songs: list[ScoredSong],
        max_per_artist: int | None = None,
    ) -> list[ScoredSong]:
        """Limit songs per artist to ensure variety.

        Args:
            songs: Sorted list of scored songs (by score, descending).
            max_per_artist: Maximum songs to include per artist.
                           Defaults to MAX_SONGS_PER_ARTIST.

        Returns:
            Filtered list with artist diversity applied.
        """
        if max_per_artist is None:
            max_per_artist = self.MAX_SONGS_PER_ARTIST

        result: list[ScoredSong] = []
        artist_counts: dict[str, int] = {}

        for song in songs:
            artist_key = song.artist.lower()
            count = artist_counts.get(artist_key, 0)

            if count < max_per_artist:
                result.append(song)
                artist_counts[artist_key] = count + 1

        return result

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
