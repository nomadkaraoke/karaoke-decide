"""Service for quiz-based onboarding.

Provides quiz artists/songs for data-light users and handles quiz submission
to create UserSong records and update user profiles.
"""

import hashlib
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from google.cloud import bigquery

from backend.config import BackendSettings
from backend.services.firestore_service import FirestoreService
from backend.services.listenbrainz_service import ListenBrainzService
from karaoke_decide.core.models import QuizArtist, QuizSong, SuggestionReason
from karaoke_decide.services.bigquery_catalog import BigQueryCatalogService


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


@dataclass
class ManualArtist:
    """Artist selected by user via autocomplete search (with Spotify ID)."""

    artist_id: str
    artist_name: str
    genres: list[str] | None = None


class QuizService:
    """Service for quiz-based onboarding.

    Handles:
    - Fetching popular karaoke songs for quiz
    - Processing quiz submissions
    - Creating UserSong records from quiz responses
    """

    USERS_COLLECTION = "decide_users"
    USER_SONGS_COLLECTION = "user_songs"
    QUIZ_SONGS_CACHE_COLLECTION = "quiz_songs_cache"
    LASTFM_USERS_COLLECTION = "lastfm_users"  # MBID-first collaborative filtering

    # BigQuery config
    PROJECT_ID = "nomadkaraoke"
    DATASET_ID = "karaoke_decide"

    # Quiz configuration
    DEFAULT_QUIZ_SIZE = 15
    DEFAULT_ARTIST_COUNT = 25  # Number of artists to show in quiz
    MIN_BRAND_COUNT = 5  # Songs must be on at least 5 karaoke brands
    MIN_ARTIST_SONGS = 3  # Artists must have at least 3 karaoke songs
    CACHE_TTL_HOURS = 24  # How long to cache quiz data

    # Collaborative filtering configuration
    MIN_SHARED_ARTISTS = 3  # Minimum artists in common to consider users "similar"
    MIN_SIMILAR_USERS = 5  # Minimum similar users needed to show "fans_also_like"
    MLHD_MIN_SHARED_USERS = 500  # Minimum shared users in MLHD data to suggest

    def __init__(
        self,
        settings: BackendSettings,
        firestore: FirestoreService,
        bigquery_client: bigquery.Client | None = None,
        listenbrainz: ListenBrainzService | None = None,
    ):
        """Initialize the quiz service.

        Args:
            settings: Backend settings.
            firestore: Firestore service for user data.
            bigquery_client: Optional BigQuery client (created lazily).
            listenbrainz: Optional ListenBrainz service for similar artists.
        """
        self.settings = settings
        self.firestore = firestore
        self._bigquery_client = bigquery_client
        self._listenbrainz = listenbrainz
        self._catalog_service: BigQueryCatalogService | None = None

    @property
    def bigquery(self) -> bigquery.Client:
        """Get or create BigQuery client."""
        if self._bigquery_client is None:
            self._bigquery_client = bigquery.Client(project=self.PROJECT_ID)
        return self._bigquery_client

    @property
    def listenbrainz(self) -> ListenBrainzService:
        """Get or create ListenBrainz service."""
        if self._listenbrainz is None:
            self._listenbrainz = ListenBrainzService(self.settings, self.firestore)
        return self._listenbrainz

    @property
    def catalog(self) -> BigQueryCatalogService:
        """Get or create BigQuery catalog service for MBID lookups."""
        if self._catalog_service is None:
            self._catalog_service = BigQueryCatalogService(self.bigquery)
        return self._catalog_service

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

    async def get_quiz_artists(
        self,
        count: int = DEFAULT_ARTIST_COUNT,
        genres: list[str] | None = None,
        exclude_artists: list[str] | None = None,
    ) -> list[QuizArtist]:
        """Get quiz artists for onboarding.

        Returns popular karaoke artists that users are likely to recognize.
        Artists are selected based on total brand coverage and song count.

        Args:
            count: Number of quiz artists to return.
            genres: Optional list of genres to filter by.
            exclude_artists: Optional list of artist names to exclude (for pagination).

        Returns:
            List of QuizArtist objects for the quiz.
        """
        # Fetch artist candidates with aggregated stats
        candidates = self._fetch_artist_candidates(
            limit=count * 2,
            genres=genres,
            exclude_artists=exclude_artists,
        )

        # Randomly select final set for variety
        if len(candidates) > count:
            candidates = random.sample(candidates, count)

        return candidates

    async def get_smart_quiz_artists(
        self,
        genres: list[str] | None = None,
        decades: list[str] | None = None,
        seed_artists: list[str] | None = None,
        seed_song_artists: list[str] | None = None,
        exclude_artists: list[str] | None = None,
        count: int = DEFAULT_ARTIST_COUNT,
    ) -> list[QuizArtist]:
        """Get quiz artists informed by user's preferences and manual entries.

        This enhanced version uses multiple signals to find more relevant artists:
        1. Collaborative filtering (artists liked by users with similar taste)
        2. Explicit genre selections from user
        3. Genres inferred from manually entered artists
        4. Genres inferred from artists of songs user enjoys singing
        5. Decade filtering

        Args:
            genres: User's selected genre IDs (e.g., ['rock', 'punk']).
            decades: User's selected decades (e.g., ['1990s', '2000s']).
            seed_artists: Artists manually entered by user (to infer genres).
            seed_song_artists: Artists from songs user enjoys singing.
            exclude_artists: Artists to exclude (already selected/entered).
            count: Number of artists to return.

        Returns:
            List of QuizArtist objects that match user's taste profile with suggestion reasons.
        """
        # Combine all exclusions
        all_exclusions = list(set((exclude_artists or []) + (seed_artists or []) + (seed_song_artists or [])))

        # Get genres from seed artists if provided, tracking which artist they came from
        inferred_genres: list[str] = []
        seed_artist_genres: dict[str, list[str]] = {}  # artist_name -> genres
        all_seed_artists = list(set((seed_artists or []) + (seed_song_artists or [])))

        if all_seed_artists:
            seed_artist_genres = self._get_artist_genres_detailed(all_seed_artists)
            for artist_genres in seed_artist_genres.values():
                inferred_genres.extend(artist_genres)
            inferred_genres = list(set(inferred_genres))

        # Combine explicit genres with inferred genres
        all_genres = list(set((genres or []) + inferred_genres))

        # If we have genres, use them for filtering
        # If "other" is in genres, don't filter by genre at all
        effective_genres = None
        if all_genres and "other" not in all_genres:
            effective_genres = all_genres

        # Get collaborative suggestions if user has selected enough artists
        # This finds artists liked by users with similar taste
        collaborative_suggestions: dict[str, list[str]] = {}
        if all_seed_artists and len(all_seed_artists) >= self.MIN_SHARED_ARTISTS:
            # MBID-First: Look up MBIDs for seed artists to enable more accurate matching
            seed_artist_mbids: list[str] = []
            try:
                mbid_map = self.catalog.lookup_mbids_by_names(all_seed_artists)
                # Use same normalization as lookup_mbids_by_names for consistent keys
                seed_artist_mbids = [mbid_map.get(self.catalog.normalize_for_matching(a), "") for a in all_seed_artists]
                seed_artist_mbids = [m for m in seed_artist_mbids if m]  # Filter empty
            except Exception:
                pass  # Fall back to name-based matching

            collaborative_suggestions = await self._get_collaborative_suggestions(
                user_selected_artists=all_seed_artists,
                exclude_artists=set(all_exclusions) if all_exclusions else set(),
                user_artist_mbids=seed_artist_mbids if seed_artist_mbids else None,
            )

        # Fetch candidates with combined filters
        candidates = self._fetch_artist_candidates(
            limit=count * 2,
            genres=effective_genres,
            exclude_artists=all_exclusions if all_exclusions else None,
        )

        # Get ListenBrainz similar artist matches (candidate -> seed artists)
        listenbrainz_matches: dict[str, list[str]] = {}
        candidate_names = [c.name for c in candidates]
        if all_seed_artists:
            listenbrainz_matches = await self.listenbrainz.find_similar_artist_matches(
                seed_artists=all_seed_artists,
                candidate_names=candidate_names,
            )

        # Get MLHD+ similar artist matches (from 583K Last.fm users)
        mlhd_matches: dict[str, list[str]] = {}
        if all_seed_artists:
            mlhd_matches = self._get_mlhd_similar_artists(
                seed_artist_names=all_seed_artists,
                candidate_names=candidate_names,
                exclude_artists=set(all_exclusions) if all_exclusions else None,
            )

        # Generate suggestion reasons for each candidate
        candidates_with_reasons = self._add_suggestion_reasons(
            candidates=candidates,
            user_genres=genres or [],
            user_decades=decades or [],
            seed_artist_genres=seed_artist_genres,
            listenbrainz_matches=listenbrainz_matches,
            mlhd_matches=mlhd_matches,
            collaborative_suggestions=collaborative_suggestions,
        )

        # Randomly select final set
        if len(candidates_with_reasons) > count:
            candidates_with_reasons = random.sample(candidates_with_reasons, count)

        return candidates_with_reasons

    def _get_artist_genres_detailed(self, artist_names: list[str]) -> dict[str, list[str]]:
        """Get genres for given artists from BigQuery, returning a dict by artist.

        Used to track which seed artist contributed which genres.

        Args:
            artist_names: List of artist names.

        Returns:
            Dict mapping artist name to list of genre IDs.
        """
        if not artist_names:
            return {}

        # Build query to get genres for these artists
        placeholders = ", ".join([f"@artist_{i}" for i in range(len(artist_names))])
        sql = f"""
            SELECT sa.artist_name, ARRAY_AGG(DISTINCT sag.genre) as genres
            FROM `{self.PROJECT_ID}.{self.DATASET_ID}.spotify_artists` sa
            JOIN `{self.PROJECT_ID}.{self.DATASET_ID}.spotify_artist_genres` sag
                ON sa.artist_id = sag.artist_id
            WHERE LOWER(sa.artist_name) IN ({placeholders})
            GROUP BY sa.artist_name
            LIMIT 50
        """

        params = [
            bigquery.ScalarQueryParameter(f"artist_{i}", "STRING", name.lower()) for i, name in enumerate(artist_names)
        ]

        job_config = bigquery.QueryJobConfig(query_parameters=params)

        try:
            results = self.bigquery.query(sql, job_config=job_config).result()
            result_dict: dict[str, list[str]] = {}
            for row in results:
                spotify_genres = [g.lower() for g in row.genres] if row.genres else []
                genre_ids = self._map_spotify_genres_to_ids(spotify_genres)
                result_dict[row.artist_name] = genre_ids
            return result_dict
        except Exception:
            return {}

    def _add_suggestion_reasons(
        self,
        candidates: list[QuizArtist],
        user_genres: list[str],
        user_decades: list[str],
        seed_artist_genres: dict[str, list[str]],
        listenbrainz_matches: dict[str, list[str]] | None = None,
        mlhd_matches: dict[str, list[str]] | None = None,
        collaborative_suggestions: dict[str, list[str]] | None = None,
    ) -> list[QuizArtist]:
        """Add suggestion reasons to each candidate artist.

        Priority order:
        1a. fans_also_like - Karaoke singers with similar taste (our quiz data)
        1b. fans_also_like - Music listeners with similar taste (ListenBrainz)
        1c. fans_also_like - Last.fm listeners with similar taste (MLHD+)
        2. similar_artist - if artist shares 2+ genres with a seed artist
        3. genre_match - if artist matches user's selected genres
        4. decade_match - if artist's decade matches user's selection
        5. popular_choice - fallback for popular karaoke artists

        Args:
            candidates: List of QuizArtist candidates.
            user_genres: User's explicitly selected genre IDs.
            user_decades: User's selected decades.
            seed_artist_genres: Dict of seed artist name -> their genre IDs.
            listenbrainz_matches: Dict of candidate name -> seed artists (ListenBrainz).
            mlhd_matches: Dict of candidate name -> seed artists (MLHD+ Last.fm data).
            collaborative_suggestions: Dict of artist name -> shared artists (karaoke singers).

        Returns:
            List of QuizArtist with suggestion_reason populated.
        """
        results: list[QuizArtist] = []

        for candidate in candidates:
            reason = self._generate_suggestion_reason(
                artist=candidate,
                user_genres=user_genres,
                user_decades=user_decades,
                seed_artist_genres=seed_artist_genres,
                listenbrainz_matches=listenbrainz_matches,
                mlhd_matches=mlhd_matches,
                collaborative_suggestions=collaborative_suggestions or {},
            )

            # Create new QuizArtist with reason
            artist_dict = candidate.model_dump()
            artist_dict["suggestion_reason"] = reason
            results.append(QuizArtist(**artist_dict))

        return results

    def _generate_suggestion_reason(
        self,
        artist: QuizArtist,
        user_genres: list[str],
        user_decades: list[str],
        seed_artist_genres: dict[str, list[str]],
        listenbrainz_matches: dict[str, list[str]] | None = None,
        mlhd_matches: dict[str, list[str]] | None = None,
        collaborative_suggestions: dict[str, list[str]] | None = None,
    ) -> SuggestionReason:
        """Generate a suggestion reason for an artist.

        Args:
            artist: The artist to generate reason for.
            user_genres: User's explicitly selected genre IDs.
            user_decades: User's selected decades.
            seed_artist_genres: Dict of seed artist name -> their genre IDs.
            listenbrainz_matches: Dict of candidate name -> seed artists (ListenBrainz).
            mlhd_matches: Dict of candidate name -> seed artists (MLHD+ Last.fm data).
            collaborative_suggestions: Dict of artist name -> shared artists (Firestore).

        Returns:
            SuggestionReason explaining why this artist was suggested.
        """
        collaborative_suggestions = collaborative_suggestions or {}

        # Priority 1a: Check for karaoke singer similarity (our quiz data)
        # This is karaoke-specific - people who sing similar artists at karaoke
        for collab_artist, shared_artists in collaborative_suggestions.items():
            if collab_artist.lower() == artist.name.lower() and shared_artists:
                if len(shared_artists) == 1:
                    display_text = f"Singers who like {shared_artists[0]} also chose"
                elif len(shared_artists) == 2:
                    display_text = f"Singers who like {shared_artists[0]} & {shared_artists[1]} also chose"
                else:
                    display_text = f"Singers who like {shared_artists[0]}, {shared_artists[1]} & others also chose"
                return SuggestionReason(
                    type="fans_also_like",
                    display_text=display_text,
                    related_to=", ".join(shared_artists[:3]),
                )

        # Priority 1b: Check for music listener similarity (ListenBrainz)
        # Based on general music listening patterns from millions of users
        # Use case-insensitive lookup for robustness
        listenbrainz_match_key = artist.name.lower()
        listenbrainz_matches_lower = (
            {k.lower(): v for k, v in listenbrainz_matches.items()} if listenbrainz_matches else {}
        )
        if listenbrainz_match_key in listenbrainz_matches_lower:
            similar_to = listenbrainz_matches_lower[listenbrainz_match_key]
            if len(similar_to) == 1:
                display_text = f"Fans of {similar_to[0]} also like"
            elif len(similar_to) == 2:
                display_text = f"Fans of {similar_to[0]} & {similar_to[1]} also like"
            else:
                display_text = f"Fans of {similar_to[0]}, {similar_to[1]} & others also like"
            return SuggestionReason(
                type="fans_also_like",
                display_text=display_text,
                related_to=", ".join(similar_to[:3]),
            )

        # Priority 1c: Check for MLHD+ similarity (Last.fm listening history)
        # Based on co-occurrence from 583K Last.fm users
        mlhd_match_key = artist.name.lower()
        mlhd_matches_lower = {k.lower(): v for k, v in mlhd_matches.items()} if mlhd_matches else {}
        if mlhd_match_key in mlhd_matches_lower:
            similar_to = mlhd_matches_lower[mlhd_match_key]
            if len(similar_to) == 1:
                display_text = f"Listeners of {similar_to[0]} also like"
            elif len(similar_to) == 2:
                display_text = f"Listeners of {similar_to[0]} & {similar_to[1]} also like"
            else:
                display_text = f"Listeners of {similar_to[0]}, {similar_to[1]} & others also like"
            return SuggestionReason(
                type="fans_also_like",
                display_text=display_text,
                related_to=", ".join(similar_to[:3]),
            )

        # Map artist's Spotify genres to our genre IDs
        artist_genre_ids = self._map_spotify_genres_to_ids([g.lower() for g in artist.genres] if artist.genres else [])

        # Priority 2: Check for similar_artist (shares 2+ genres with seed artist)
        if seed_artist_genres:
            for seed_artist, seed_genres in seed_artist_genres.items():
                if seed_genres and artist_genre_ids:
                    overlap = set(artist_genre_ids) & set(seed_genres)
                    if len(overlap) >= 2:
                        return SuggestionReason(
                            type="similar_artist",
                            display_text=f"Similar to {seed_artist}",
                            related_to=seed_artist,
                        )

        # Priority 3: Check for genre_match
        if user_genres and artist_genre_ids:
            matching_genres = set(user_genres) & set(artist_genre_ids)
            if matching_genres:
                # Format genre names nicely
                formatted = self._format_genre_names(list(matching_genres)[:2])
                return SuggestionReason(
                    type="genre_match",
                    display_text=f"Based on {formatted}",
                    related_to=None,
                )

        # Priority 4: Check for decade_match
        if user_decades and artist.primary_decade != "Unknown":
            if artist.primary_decade in user_decades:
                return SuggestionReason(
                    type="decade_match",
                    display_text=f"Popular in the {artist.primary_decade}",
                    related_to=None,
                )

        # Priority 5: Fallback to popular_choice
        return SuggestionReason(
            type="popular_choice",
            display_text="Popular karaoke choice",
            related_to=None,
        )

    def _format_genre_names(self, genre_ids: list[str]) -> str:
        """Format genre IDs into human-readable names.

        Args:
            genre_ids: List of genre IDs like ['rock', 'punk'].

        Returns:
            Formatted string like "rock, punk" or "rock & punk".
        """
        # Map IDs to display names
        display_names = {
            "pop": "pop",
            "rock": "rock",
            "hiphop": "hip-hop",
            "rnb": "R&B",
            "country": "country",
            "electronic": "electronic",
            "dance": "dance",
            "metal": "metal",
            "jazz": "jazz",
            "latin": "Latin",
            "indie": "indie",
            "kpop": "K-pop",
            "disco": "disco",
            "classic-rock": "classic rock",
            "musical": "musical theater",
            "reggae": "reggae",
            "punk": "punk",
            "emo": "emo",
            "grunge": "grunge",
            "folk": "folk",
            "blues": "blues",
            "ska": "ska",
        }

        names = [display_names.get(gid, gid) for gid in genre_ids]

        if len(names) == 1:
            return names[0]
        elif len(names) == 2:
            return f"{names[0]} & {names[1]}"
        else:
            return ", ".join(names)

    def _get_artist_genres(self, artist_names: list[str]) -> list[str]:
        """Get genres for given artists from BigQuery.

        Used to infer user's genre preferences from manually entered artists.

        Args:
            artist_names: List of artist names.

        Returns:
            List of genre IDs that map to these artists.
        """
        if not artist_names:
            return []

        # Build query to get genres for these artists
        placeholders = ", ".join([f"@artist_{i}" for i in range(len(artist_names))])
        sql = f"""
            SELECT DISTINCT sag.genre
            FROM `{self.PROJECT_ID}.{self.DATASET_ID}.spotify_artists` sa
            JOIN `{self.PROJECT_ID}.{self.DATASET_ID}.spotify_artist_genres` sag
                ON sa.artist_id = sag.artist_id
            WHERE LOWER(sa.artist_name) IN ({placeholders})
            LIMIT 50
        """

        params = [
            bigquery.ScalarQueryParameter(f"artist_{i}", "STRING", name.lower()) for i, name in enumerate(artist_names)
        ]

        job_config = bigquery.QueryJobConfig(query_parameters=params)

        try:
            results = self.bigquery.query(sql, job_config=job_config).result()
            spotify_genres = [row.genre.lower() for row in results]

            # Map Spotify genres back to our genre IDs
            return self._map_spotify_genres_to_ids(spotify_genres)
        except Exception:
            # If query fails, return empty list
            return []

    async def _get_collaborative_suggestions(
        self,
        user_selected_artists: list[str],
        exclude_artists: set[str],
        user_artist_mbids: list[str] | None = None,
    ) -> dict[str, list[str]]:
        """Find artists liked by users with similar taste.

        MBID-First Architecture: Queries two data sources in parallel:
        1. Organic users (decide_users) - by artist name (quiz_artists_known)
        2. Last.fm users (lastfm_users) - by MBID if available, else artist name
           Uses artist_mbids array for efficient MBID-based matching.

        Args:
            user_selected_artists: Artists the current user has selected.
            exclude_artists: Artists to exclude from suggestions.
            user_artist_mbids: Optional list of MBIDs for the user's selected artists.
                              Enables more accurate matching with Last.fm users.

        Returns:
            Dict mapping artist_name -> list of shared artists that connect them.
            Empty dict if not enough similar users found.
        """
        if len(user_selected_artists) < self.MIN_SHARED_ARTISTS:
            return {}

        # Normalize for comparison
        user_artists_lower = {a.lower() for a in user_selected_artists}
        exclude_lower = {a.lower() for a in exclude_artists}

        # Filter to valid MBIDs (non-empty)
        valid_mbids = [m for m in (user_artist_mbids or []) if m]

        # Query both user collections in parallel
        organic_users, lastfm_users = await self._query_collaborative_sources(
            user_artists_lower,
            user_artist_mbids=valid_mbids if valid_mbids else None,
        )

        # Find similar users and collect their artists
        # Structure: artist_name_lower -> list of (shared_artists, user_artists, source)
        artist_supporters: dict[str, list[tuple[list[str], list[str], str]]] = {}

        # Process organic users (from quiz)
        for user_doc in organic_users:
            their_artists = user_doc.get("quiz_artists_known", [])
            if not their_artists or len(their_artists) < self.MIN_SHARED_ARTISTS:
                continue

            their_artists_lower = {a.lower() for a in their_artists}
            shared = user_artists_lower & their_artists_lower

            if len(shared) >= self.MIN_SHARED_ARTISTS:
                shared_display = [a for a in their_artists if a.lower() in shared][:3]
                for artist in their_artists:
                    artist_lower = artist.lower()
                    if artist_lower in user_artists_lower or artist_lower in exclude_lower:
                        continue
                    if artist_lower not in artist_supporters:
                        artist_supporters[artist_lower] = []
                    artist_supporters[artist_lower].append((shared_display, their_artists, "organic"))

        # Process Last.fm users (MBID-first source)
        for user_doc in lastfm_users:
            # Use top_artist_names for efficient comparison (top 100)
            their_artists = user_doc.get("top_artist_names", [])
            if not their_artists or len(their_artists) < self.MIN_SHARED_ARTISTS:
                continue

            their_artists_lower = {a.lower() for a in their_artists}
            shared = user_artists_lower & their_artists_lower

            if len(shared) >= self.MIN_SHARED_ARTISTS:
                shared_display = [a for a in their_artists if a.lower() in shared][:3]
                for artist in their_artists:
                    artist_lower = artist.lower()
                    if artist_lower in user_artists_lower or artist_lower in exclude_lower:
                        continue
                    if artist_lower not in artist_supporters:
                        artist_supporters[artist_lower] = []
                    artist_supporters[artist_lower].append((shared_display, their_artists, "lastfm"))

        # Filter to artists with enough supporters
        result: dict[str, list[str]] = {}
        for artist_lower, supporters in artist_supporters.items():
            if len(supporters) >= self.MIN_SIMILAR_USERS:
                shared_artists = supporters[0][0]
                original_name = None
                for _, their_artists, _ in supporters:
                    for a in their_artists:
                        if a.lower() == artist_lower:
                            original_name = a
                            break
                    if original_name:
                        break

                if original_name:
                    result[original_name] = shared_artists

        return result

    def _get_mlhd_similar_artists(
        self,
        seed_artist_names: list[str],
        candidate_names: list[str],
        exclude_artists: set[str] | None = None,
    ) -> dict[str, list[str]]:
        """Find similar artists using MLHD+ listening history data.

        Queries the mlhd_artist_similarity BigQuery table which contains
        artist co-occurrence data from 583,000 Last.fm users.

        Args:
            seed_artist_names: Artists the user has selected.
            candidate_names: Candidate artists to check for similarity.
            exclude_artists: Artists to exclude from suggestions.

        Returns:
            Dict mapping candidate artist name -> list of seed artists they're similar to.
        """
        if not seed_artist_names or not candidate_names:
            return {}

        exclude_artists = exclude_artists or set()
        exclude_lower = {a.lower() for a in exclude_artists}

        # Build query to find candidates similar to seed artists
        # We query in both directions since the table stores pairs as (a, b) where a < b
        seed_placeholders = ", ".join([f"@seed_{i}" for i in range(len(seed_artist_names))])
        candidate_placeholders = ", ".join([f"@cand_{i}" for i in range(len(candidate_names))])

        sql = f"""
            WITH seed_matches AS (
                -- Find where seed is artist_a and candidate is artist_b
                SELECT
                    artist_a_name AS seed_name,
                    artist_b_name AS similar_name,
                    shared_users
                FROM `{self.PROJECT_ID}.{self.DATASET_ID}.mlhd_artist_similarity`
                WHERE LOWER(artist_a_name) IN ({seed_placeholders})
                  AND LOWER(artist_b_name) IN ({candidate_placeholders})
                  AND shared_users >= @min_shared

                UNION ALL

                -- Find where seed is artist_b and candidate is artist_a
                SELECT
                    artist_b_name AS seed_name,
                    artist_a_name AS similar_name,
                    shared_users
                FROM `{self.PROJECT_ID}.{self.DATASET_ID}.mlhd_artist_similarity`
                WHERE LOWER(artist_b_name) IN ({seed_placeholders})
                  AND LOWER(artist_a_name) IN ({candidate_placeholders})
                  AND shared_users >= @min_shared
            )
            SELECT
                similar_name,
                ARRAY_AGG(seed_name ORDER BY shared_users DESC LIMIT 3) AS seed_artists,
                MAX(shared_users) AS max_shared
            FROM seed_matches
            GROUP BY similar_name
            ORDER BY max_shared DESC
            LIMIT 100
        """

        # Build query parameters
        params = [
            bigquery.ScalarQueryParameter("min_shared", "INT64", self.MLHD_MIN_SHARED_USERS),
        ]
        for i, name in enumerate(seed_artist_names):
            params.append(bigquery.ScalarQueryParameter(f"seed_{i}", "STRING", name.lower()))
        for i, name in enumerate(candidate_names):
            params.append(bigquery.ScalarQueryParameter(f"cand_{i}", "STRING", name.lower()))

        job_config = bigquery.QueryJobConfig(query_parameters=params)

        try:
            results = self.bigquery.query(sql, job_config=job_config).result()

            matches: dict[str, list[str]] = {}
            for row in results:
                # Skip if in exclude list
                if row.similar_name.lower() in exclude_lower:
                    continue
                matches[row.similar_name] = list(row.seed_artists)

            return matches
        except Exception:
            # If query fails (e.g., table doesn't exist), return empty
            return {}

    async def _query_collaborative_sources(
        self,
        user_artists_lower: set[str],
        user_artist_mbids: list[str] | None = None,
    ) -> tuple[list[dict], list[dict]]:
        """Query both organic and Last.fm users for collaborative filtering.

        MBID-First: When MBIDs are available, uses them for more accurate
        Last.fm user matching. Falls back to name-based matching otherwise.

        Queries in parallel for efficiency:
        1. Organic users: Full scan of decide_users (small collection)
        2. Last.fm users: Uses array_contains_any on artist_mbids (preferred)
           or artist_names_lower (fallback)

        Args:
            user_artists_lower: Set of lowercased artist names for matching.
            user_artist_mbids: Optional list of MBIDs for MBID-based matching.

        Returns:
            Tuple of (organic_users, lastfm_users) document lists.
        """
        import asyncio

        async def query_organic() -> list[dict]:
            """Query organic users (our quiz users)."""
            try:
                return await self.firestore.query_documents(
                    self.USERS_COLLECTION,
                    filters=[],
                    limit=10000,
                )
            except Exception:
                return []

        async def query_lastfm() -> list[dict]:
            """Query Last.fm users.

            MBID-First: Prefers MBID-based matching for accuracy.
            Falls back to name-based matching if no MBIDs available.

            Note: array_contains_any has a 30-value limit, so we use
            a subset of the user's artists for the initial filter.
            """
            try:
                # MBID-First: Use MBIDs if available for accurate matching
                if user_artist_mbids and len(user_artist_mbids) > 0:
                    # Take up to 30 MBIDs for array_contains_any query
                    query_mbids = user_artist_mbids[:30]

                    # Query by MBID for precise matching
                    return await self.firestore.query_documents(
                        self.LASTFM_USERS_COLLECTION,
                        filters=[
                            ("artist_mbids", "array_contains_any", query_mbids),
                        ],
                        limit=1000,
                    )
                else:
                    # Fallback: Name-based matching
                    query_artists = list(user_artists_lower)[:30]
                    if not query_artists:
                        return []

                    # Use array_contains_any on names for backwards compatibility
                    return await self.firestore.query_documents(
                        self.LASTFM_USERS_COLLECTION,
                        filters=[
                            ("artist_names_lower", "array_contains_any", query_artists),
                        ],
                        limit=1000,
                    )
            except Exception:
                return []

        # Run both queries in parallel
        organic_users, lastfm_users = await asyncio.gather(
            query_organic(),
            query_lastfm(),
        )

        return organic_users, lastfm_users

    def _map_spotify_genres_to_ids(self, spotify_genres: list[str]) -> list[str]:
        """Map Spotify genre strings to our genre IDs.

        Args:
            spotify_genres: List of Spotify genre strings (e.g., 'classic rock').

        Returns:
            List of our genre IDs (e.g., 'classic-rock').
        """
        # Reverse mapping from Spotify genre patterns to our IDs
        genre_keywords = {
            "pop": "pop",
            "rock": "rock",
            "hip hop": "hiphop",
            "r&b": "rnb",
            "country": "country",
            "electro": "electronic",
            "dance": "electronic",
            "metal": "metal",
            "jazz": "jazz",
            "latin": "latin",
            "reggaeton": "latin",
            "indie": "indie",
            "alternative": "indie",
            "k-pop": "kpop",
            "disco": "disco",
            "funk": "disco",
            "soul": "rnb",
            "classic rock": "classic-rock",
            "broadway": "musical",
            "reggae": "reggae",
            "punk": "punk",
            "emo": "emo",
            "grunge": "grunge",
            "folk": "folk",
            "blues": "blues",
            "ska": "ska",
        }

        matched_ids: set[str] = set()
        for spotify_genre in spotify_genres:
            for keyword, genre_id in genre_keywords.items():
                if keyword in spotify_genre:
                    matched_ids.add(genre_id)
                    break

        return list(matched_ids)

    async def get_decade_artists(self, artists_per_decade: int = 5) -> dict[str, list[dict]]:
        """Get example artists for each decade.

        Returns top karaoke artists organized by decade, useful for
        helping users understand what era each decade represents.

        Args:
            artists_per_decade: Number of artists per decade.

        Returns:
            Dict mapping decade strings to list of artist info dicts.
        """
        return self._fetch_decade_artists(artists_per_decade)

    def _fetch_artist_candidates(
        self,
        limit: int,
        genres: list[str] | None = None,
        exclude_artists: list[str] | None = None,
    ) -> list[QuizArtist]:
        """Fetch quiz artist candidates from BigQuery.

        Gets artists aggregated by total brand coverage and song count.
        Optionally filters by genres and excludes previously shown artists.

        Args:
            limit: Maximum number of candidates to fetch.
            genres: Optional genres to filter by.
            exclude_artists: Optional artist names to exclude.

        Returns:
            List of QuizArtist candidates.
        """
        # Build genre filter clause if genres provided
        genre_filter = ""
        genre_join = ""
        genre_patterns: list[str] = []
        if genres:
            # Map frontend genre IDs to Spotify genre patterns
            genre_patterns = self._map_genre_ids_to_patterns(genres)
            if genre_patterns:
                # Join with spotify_artist_genres table via artist_id
                genre_join = f"""
                    LEFT JOIN `{self.PROJECT_ID}.{self.DATASET_ID}.spotify_artist_genres` sag
                        ON sa.artist_id = sag.artist_id
                """
                pattern_conditions = " OR ".join(
                    [f"LOWER(sag.genre) LIKE @genre_pattern_{i}" for i in range(len(genre_patterns))]
                )
                genre_filter = f"AND ({pattern_conditions})"

        # Build exclusion clause
        exclude_clause = ""
        if exclude_artists:
            exclude_placeholders = ", ".join([f"@exclude_{i}" for i in range(len(exclude_artists))])
            exclude_clause = f"AND LOWER(k.Artist) NOT IN ({exclude_placeholders})"

        # Query with optional genre filtering
        # Note: Genre data comes from spotify_artist_genres table (after ETL)
        # We use LEFT JOIN so artists without genre data still appear
        sql = f"""
            WITH artist_stats AS (
                SELECT
                    k.Artist as artist_name,
                    COUNT(DISTINCT k.Id) as song_count,
                    SUM(ARRAY_LENGTH(SPLIT(k.Brands, ','))) as total_brand_count,
                    ARRAY_AGG(DISTINCT k.Title ORDER BY k.Title LIMIT 3) as top_songs
                FROM `{self.PROJECT_ID}.{self.DATASET_ID}.karaokenerds_raw` k
                LEFT JOIN `{self.PROJECT_ID}.{self.DATASET_ID}.spotify_artists` sa
                    ON LOWER(k.Artist) = LOWER(sa.artist_name)
                {genre_join}
                WHERE ARRAY_LENGTH(SPLIT(k.Brands, ',')) >= @min_brands
                {genre_filter}
                {exclude_clause}
                GROUP BY k.Artist
                HAVING COUNT(DISTINCT k.Id) >= @min_songs
            ),
            artist_genres AS (
                SELECT
                    LOWER(sa.artist_name) as artist_name_lower,
                    ARRAY_AGG(DISTINCT sag.genre ORDER BY sag.genre LIMIT 5) as genres
                FROM `{self.PROJECT_ID}.{self.DATASET_ID}.spotify_artists` sa
                JOIN `{self.PROJECT_ID}.{self.DATASET_ID}.spotify_artist_genres` sag
                    ON sa.artist_id = sag.artist_id
                GROUP BY LOWER(sa.artist_name)
            )
            SELECT
                s.artist_name,
                s.song_count,
                s.total_brand_count,
                s.top_songs,
                COALESCE(g.genres, []) as genres
            FROM artist_stats s
            LEFT JOIN artist_genres g ON LOWER(s.artist_name) = g.artist_name_lower
            ORDER BY s.total_brand_count DESC, s.song_count DESC
            LIMIT @limit
        """

        # Build query parameters
        params = [
            bigquery.ScalarQueryParameter("min_brands", "INT64", self.MIN_BRAND_COUNT),
            bigquery.ScalarQueryParameter("min_songs", "INT64", self.MIN_ARTIST_SONGS),
            bigquery.ScalarQueryParameter("limit", "INT64", limit),
        ]

        # Add genre pattern parameters
        if genre_patterns:
            for i, pattern in enumerate(genre_patterns):
                params.append(bigquery.ScalarQueryParameter(f"genre_pattern_{i}", "STRING", pattern))

        # Add exclusion parameters
        if exclude_artists:
            for i, name in enumerate(exclude_artists):
                params.append(bigquery.ScalarQueryParameter(f"exclude_{i}", "STRING", name.lower()))

        job_config = bigquery.QueryJobConfig(query_parameters=params)

        try:
            results = self.bigquery.query(sql, job_config=job_config).result()
        except Exception:
            # Fall back to simpler query if genre tables don't exist yet
            return self._fetch_artist_candidates_simple(limit, exclude_artists)

        return [
            QuizArtist(
                name=row.artist_name,
                song_count=row.song_count,
                top_songs=list(row.top_songs) if row.top_songs else [],
                total_brand_count=row.total_brand_count,
                primary_decade="Unknown",  # Enhanced with decade data below
                genres=list(row.genres) if row.genres else [],
                image_url=None,
            )
            for row in results
        ]

    def _fetch_artist_candidates_simple(
        self,
        limit: int,
        exclude_artists: list[str] | None = None,
    ) -> list[QuizArtist]:
        """Simple fallback query without genre data.

        Used when spotify_artist_genres table doesn't exist yet.
        """
        exclude_clause = ""
        params = [
            bigquery.ScalarQueryParameter("min_brands", "INT64", self.MIN_BRAND_COUNT),
            bigquery.ScalarQueryParameter("min_songs", "INT64", self.MIN_ARTIST_SONGS),
            bigquery.ScalarQueryParameter("limit", "INT64", limit),
        ]

        if exclude_artists:
            exclude_placeholders = ", ".join([f"@exclude_{i}" for i in range(len(exclude_artists))])
            exclude_clause = f"AND LOWER(Artist) NOT IN ({exclude_placeholders})"
            for i, name in enumerate(exclude_artists):
                params.append(bigquery.ScalarQueryParameter(f"exclude_{i}", "STRING", name.lower()))

        sql = f"""
            WITH artist_stats AS (
                SELECT
                    Artist as artist_name,
                    COUNT(*) as song_count,
                    SUM(ARRAY_LENGTH(SPLIT(Brands, ','))) as total_brand_count,
                    ARRAY_AGG(Title ORDER BY ARRAY_LENGTH(SPLIT(Brands, ',')) DESC LIMIT 3) as top_songs
                FROM `{self.PROJECT_ID}.{self.DATASET_ID}.karaokenerds_raw`
                WHERE ARRAY_LENGTH(SPLIT(Brands, ',')) >= @min_brands
                {exclude_clause}
                GROUP BY Artist
                HAVING COUNT(*) >= @min_songs
            )
            SELECT artist_name, song_count, total_brand_count, top_songs
            FROM artist_stats
            ORDER BY total_brand_count DESC, song_count DESC
            LIMIT @limit
        """

        job_config = bigquery.QueryJobConfig(query_parameters=params)
        results = self.bigquery.query(sql, job_config=job_config).result()

        return [
            QuizArtist(
                name=row.artist_name,
                song_count=row.song_count,
                top_songs=list(row.top_songs) if row.top_songs else [],
                total_brand_count=row.total_brand_count,
                primary_decade="Unknown",
                genres=[],
                image_url=None,
            )
            for row in results
        ]

    def _map_genre_ids_to_patterns(self, genre_ids: list[str]) -> list[str]:
        """Map frontend genre IDs to Spotify genre search patterns.

        Frontend uses simplified IDs like 'rock', 'hiphop'.
        Spotify genres are more specific like 'classic rock', 'hip hop'.

        Args:
            genre_ids: Frontend genre IDs.

        Returns:
            List of SQL LIKE patterns for matching Spotify genres.
        """
        # Map genre IDs to patterns that match Spotify's genre vocabulary
        genre_map = {
            "pop": "%pop%",
            "rock": "%rock%",
            "hiphop": "%hip hop%",
            "rnb": "%r&b%",
            "country": "%country%",
            "electronic": "%electro%",
            "dance": "%dance%",
            "metal": "%metal%",
            "jazz": "%jazz%",
            "latin": "%latin%",
            "reggaeton": "%reggaeton%",
            "indie": "%indie%",
            "alternative": "%alternative%",
            "kpop": "%k-pop%",
            "disco": "%disco%",
            "funk": "%funk%",
            "soul": "%soul%",
            "classic-rock": "%classic rock%",
            "musical": "%broadway%",
            "reggae": "%reggae%",
            # New genres added for expanded quiz
            "punk": "%punk%",
            "emo": "%emo%",
            "grunge": "%grunge%",
            "folk": "%folk%",
            "blues": "%blues%",
            "ska": "%ska%",
            # "other" is intentionally not mapped - it's a catch-all
        }

        patterns = []
        for gid in genre_ids:
            if gid in genre_map:
                patterns.append(genre_map[gid])
            else:
                # Default: wrap in wildcards
                patterns.append(f"%{gid}%")

        return patterns

    def _fetch_decade_artists(self, artists_per_decade: int) -> dict[str, list[dict]]:
        """Fetch top artists for each decade.

        Args:
            artists_per_decade: Number of artists per decade.

        Returns:
            Dict mapping decade to list of artist info.
        """
        # For now, use a hardcoded list of iconic artists per decade
        # This provides reliable results even before ETL of album dates
        # TODO: Replace with BigQuery query after album ETL complete
        decade_artists = {
            "1950s": [
                {"name": "Elvis Presley", "top_song": "Hound Dog"},
                {"name": "Chuck Berry", "top_song": "Johnny B. Goode"},
                {"name": "Little Richard", "top_song": "Tutti Frutti"},
                {"name": "Buddy Holly", "top_song": "Peggy Sue"},
                {"name": "Ray Charles", "top_song": "What'd I Say"},
            ],
            "1960s": [
                {"name": "The Beatles", "top_song": "Hey Jude"},
                {"name": "Elvis Presley", "top_song": "Can't Help Falling in Love"},
                {"name": "The Supremes", "top_song": "Stop! In the Name of Love"},
                {"name": "Aretha Franklin", "top_song": "Respect"},
                {"name": "Frank Sinatra", "top_song": "My Way"},
            ],
            "1970s": [
                {"name": "Queen", "top_song": "Bohemian Rhapsody"},
                {"name": "ABBA", "top_song": "Dancing Queen"},
                {"name": "Elton John", "top_song": "Tiny Dancer"},
                {"name": "Bee Gees", "top_song": "Stayin' Alive"},
                {"name": "Fleetwood Mac", "top_song": "Dreams"},
            ],
            "1980s": [
                {"name": "Michael Jackson", "top_song": "Billie Jean"},
                {"name": "Prince", "top_song": "Purple Rain"},
                {"name": "Madonna", "top_song": "Like a Prayer"},
                {"name": "Whitney Houston", "top_song": "I Wanna Dance with Somebody"},
                {"name": "Journey", "top_song": "Don't Stop Believin'"},
            ],
            "1990s": [
                {"name": "Mariah Carey", "top_song": "Vision of Love"},
                {"name": "Backstreet Boys", "top_song": "I Want It That Way"},
                {"name": "Celine Dion", "top_song": "My Heart Will Go On"},
                {"name": "Nirvana", "top_song": "Smells Like Teen Spirit"},
                {"name": "Spice Girls", "top_song": "Wannabe"},
            ],
            "2000s": [
                {"name": "Beyoncé", "top_song": "Crazy in Love"},
                {"name": "Eminem", "top_song": "Lose Yourself"},
                {"name": "Amy Winehouse", "top_song": "Valerie"},
                {"name": "Kelly Clarkson", "top_song": "Since U Been Gone"},
                {"name": "Usher", "top_song": "Yeah!"},
            ],
            "2010s": [
                {"name": "Adele", "top_song": "Rolling in the Deep"},
                {"name": "Bruno Mars", "top_song": "Uptown Funk"},
                {"name": "Taylor Swift", "top_song": "Shake It Off"},
                {"name": "Ed Sheeran", "top_song": "Shape of You"},
                {"name": "Lady Gaga", "top_song": "Shallow"},
            ],
            "2020s": [
                {"name": "Dua Lipa", "top_song": "Levitating"},
                {"name": "The Weeknd", "top_song": "Blinding Lights"},
                {"name": "Olivia Rodrigo", "top_song": "drivers license"},
                {"name": "Harry Styles", "top_song": "As It Was"},
                {"name": "Doja Cat", "top_song": "Say So"},
            ],
        }

        # Trim to requested count
        return {decade: artists[:artists_per_decade] for decade, artists in decade_artists.items()}

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
        decade_preferences: list[str] | None = None,
        energy_preference: Literal["chill", "medium", "high"] | None = None,
        genres: list[str] | None = None,
        vocal_comfort_pref: Literal["easy", "challenging", "any"] | None = None,
        crowd_pleaser_pref: Literal["hits", "deep_cuts", "any"] | None = None,
        manual_artists: list[ManualArtist] | None = None,
    ) -> QuizSubmitResult:
        """Submit quiz responses and update user profile.

        Creates UserSong records for known songs/artists and updates user's
        quiz preferences.

        Args:
            user_id: User's ID.
            known_song_ids: List of song IDs the user recognized (legacy).
            known_artists: List of artist names the user knows.
            decade_preference: User's preferred decade - legacy single (e.g., "1980s").
            decade_preferences: User's preferred decades - multi-select.
            energy_preference: User's preferred energy level.
            genres: User's selected genre IDs.
            vocal_comfort_pref: Preferred vocal comfort (easy, challenging, any).
            crowd_pleaser_pref: Prefer hits or deep cuts.
            manual_artists: Artists selected by user via autocomplete (with Spotify IDs).

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
            user_id=user_id,
            known_song_ids=known_song_ids,
            known_artists=known_artists,
            decade_preference=decade_preference,
            decade_preferences=decade_preferences,
            energy_preference=energy_preference,
            genres=genres,
            vocal_comfort_pref=vocal_comfort_pref,
            crowd_pleaser_pref=crowd_pleaser_pref,
            manual_artists=manual_artists,
            completed_at=now,
        )

        return QuizSubmitResult(
            songs_added=songs_added,
            recommendations_ready=len(known_song_ids) > 0 or len(known_artists) > 0 or len(manual_artists or []) > 0,
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
        decade_preferences: list[str] | None,
        energy_preference: Literal["chill", "medium", "high"] | None,
        genres: list[str] | None,
        vocal_comfort_pref: Literal["easy", "challenging", "any"] | None,
        crowd_pleaser_pref: Literal["hits", "deep_cuts", "any"] | None,
        manual_artists: list[ManualArtist] | None,
        completed_at: datetime,
    ) -> None:
        """Update user profile with quiz data.

        MBID-First: Also resolves and stores MusicBrainz IDs for artists
        to enable MBID-based collaborative filtering.

        Args:
            user_id: User's ID.
            known_song_ids: Song IDs from quiz.
            known_artists: Artist names from quiz.
            decade_preference: Legacy single decade preference.
            decade_preferences: Multi-select decade preferences.
            energy_preference: Energy preference.
            genres: Selected genre IDs.
            vocal_comfort_pref: Vocal comfort preference.
            crowd_pleaser_pref: Crowd pleaser preference.
            manual_artists: Artists selected via autocomplete (with Spotify IDs).
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

        # MBID-First: Resolve artist names to MBIDs
        artist_mbids: list[str] = []
        if known_artists:
            mbid_map = self.catalog.lookup_mbids_by_names(known_artists)
            # Collect MBIDs in order, using same normalization as lookup_mbids_by_names
            for artist in known_artists:
                normalized = self.catalog.normalize_for_matching(artist)
                # Try to find MBID in mapping (keys are normalized)
                mbid = mbid_map.get(normalized, "")
                if mbid:
                    artist_mbids.append(mbid)

        # MBID-First: Resolve Spotify IDs to MBIDs for manual artists (bulk lookup)
        manual_artists_with_mbid: list[dict[str, Any]] = []
        if manual_artists:
            # Bulk lookup all Spotify IDs in one query
            spotify_ids = [a.artist_id for a in manual_artists]
            spotify_to_mbid = self.catalog.lookup_mbids_by_spotify_ids(spotify_ids)

            for a in manual_artists:
                mbid = spotify_to_mbid.get(a.artist_id)
                manual_artists_with_mbid.append(
                    {
                        "artist_id": a.artist_id,  # Spotify ID (backward compat)
                        "artist_name": a.artist_name,
                        "genres": a.genres or [],
                        "mbid": mbid,  # MBID-First: Add MusicBrainz ID
                    }
                )
                # Also add to mbids list if resolved
                if mbid:
                    artist_mbids.append(mbid)

        # Update quiz fields
        update_data: dict[str, Any] = {
            "quiz_completed_at": completed_at.isoformat(),
            "quiz_songs_known": known_song_ids,
            "quiz_artists_known": known_artists,
            "updated_at": completed_at.isoformat(),
        }

        # MBID-First: Store resolved MBIDs for efficient collaborative filtering
        if artist_mbids:
            # Remove duplicates while preserving order
            unique_mbids = list(dict.fromkeys(artist_mbids))
            update_data["quiz_artist_mbids"] = unique_mbids

        # Legacy single decade
        if decade_preference is not None:
            update_data["quiz_decade_pref"] = decade_preference

        # New multi-select decades
        if decade_preferences:
            update_data["quiz_decades"] = decade_preferences

        if energy_preference is not None:
            update_data["quiz_energy_pref"] = energy_preference

        # New preference fields
        if genres:
            update_data["quiz_genres"] = genres

        if vocal_comfort_pref is not None:
            update_data["quiz_vocal_comfort_pref"] = vocal_comfort_pref

        if crowd_pleaser_pref is not None:
            update_data["quiz_crowd_pleaser_pref"] = crowd_pleaser_pref

        if manual_artists_with_mbid:
            update_data["quiz_manual_artists"] = manual_artists_with_mbid
        elif manual_artists:
            # Fallback if MBID lookup failed completely
            update_data["quiz_manual_artists"] = [
                {
                    "artist_id": a.artist_id,
                    "artist_name": a.artist_name,
                    "genres": a.genres or [],
                }
                for a in manual_artists
            ]

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
