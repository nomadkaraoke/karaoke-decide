"""Service for quiz-based onboarding.

Provides quiz artists/songs for data-light users and handles quiz submission
to create UserSong records and update user profiles.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from backend.config import BackendSettings
from backend.services.catalog_store import CatalogStore, CatalogUnavailable, get_catalog_store
from backend.services.firestore_service import FirestoreService
from backend.services.listenbrainz_service import ListenBrainzService
from karaoke_decide.catalog.genres import map_genres
from karaoke_decide.catalog.keys import artist_key, song_key
from karaoke_decide.core.models import QuizArtist, QuizSong, SuggestionReason
from karaoke_decide.services.bigquery_catalog import BigQueryCatalogService
from karaoke_decide.services.bq_limits import make_client

# google.cloud.bigquery is imported inside the functions that use it: importing it costs
# ~0.5 s, so this keeps it out of the main API's cold start (spec §6).
if TYPE_CHECKING:
    from google.cloud import bigquery

    from karaoke_decide.catalog.reader import CatalogReader

# Smart-quiz ordering: the strongest explanation first (spec §5.6).
logger = logging.getLogger(__name__)

REASON_PRIORITY: dict[str, int] = {
    "fans_also_like": 0,
    "similar_artist": 1,
    "genre_match": 2,
    "decade_match": 3,
    "popular_choice": 4,
}


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
    """Artist selected by user via autocomplete search.

    MBID-first: MusicBrainz ID is the primary identifier when available.
    Spotify ID is optional for backward compatibility and image lookup.
    """

    artist_name: str
    mbid: str | None = None  # MusicBrainz ID (primary when available)
    artist_id: str | None = None  # Spotify ID (deprecated, for backward compat)
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

    # Query limit for collaborative filtering (tuned to avoid OOM on the 1Gi Cloud Run instance)
    MAX_LASTFM_USERS = 200  # Max Last.fm users to query for collaborative filtering

    def __init__(
        self,
        settings: BackendSettings,
        firestore: FirestoreService,
        bigquery_client: bigquery.Client | None = None,
        listenbrainz: ListenBrainzService | None = None,
        catalog_store: CatalogStore | None = None,
    ):
        """Initialize the quiz service.

        Args:
            settings: Backend settings.
            firestore: Firestore service for user data.
            bigquery_client: Optional BigQuery client (created lazily).
            listenbrainz: Optional ListenBrainz service for similar artists.
            catalog_store: Where catalog.db comes from (default: the process-wide store).
        """
        self.settings = settings
        self.firestore = firestore
        self._bigquery_client = bigquery_client
        self._listenbrainz = listenbrainz
        self._catalog_store = catalog_store
        self._catalog_service: BigQueryCatalogService | None = None

    @property
    def bigquery(self) -> bigquery.Client:
        """Get or create BigQuery client."""
        if self._bigquery_client is None:
            self._bigquery_client = make_client(self.PROJECT_ID)
        return self._bigquery_client

    @property
    def listenbrainz(self) -> ListenBrainzService:
        """Get or create ListenBrainz service."""
        if self._listenbrainz is None:
            self._listenbrainz = ListenBrainzService(self.settings, self.firestore, self._catalog_store)
        return self._listenbrainz

    async def _known_artist_mbids(self, names: list[str]) -> list[str]:
        """MBIDs for ``names`` (in order, unknown ones skipped): BigQuery, else catalog.db's artists
        when BigQuery is disabled or fails, so a quiz submit never fails on this enrichment."""
        if not self.settings.bigquery_disabled:
            try:
                mbid_map = await asyncio.to_thread(self.catalog.lookup_mbids_by_names, names)  # BigQuery
                # Keys use lookup_mbids_by_names' own normalization.
                found = [mbid_map.get(self.catalog.normalize_for_matching(n), "") for n in names]
                return [m for m in found if m]
            except Exception:
                logger.warning("Quiz submit: MBID lookup failed, using catalog.db", exc_info=True)
        reader = await self._catalog_reader()
        if reader is None:
            return []
        try:
            found_local = await asyncio.to_thread(lambda: [reader.artist_mbid(n) for n in names])
        except Exception:
            logger.warning("Quiz submit: catalog.db MBID lookup failed", exc_info=True)
            return []
        return [m for m in found_local if m]

    async def _catalog_reader(self) -> CatalogReader | None:
        """catalog.db's reader, or None while it is still warming up (callers degrade)."""
        store = self._catalog_store or get_catalog_store()
        try:
            return await asyncio.to_thread(store.require)
        except CatalogUnavailable:
            return None

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
        """Smart quiz artists from BigQuery; from catalog.db when BigQuery is disabled (local
        stack) or fails, so the quiz degrades instead of erroring. Empty only while catalog.db is
        still warming up."""
        if not self.settings.bigquery_disabled:
            try:
                return await self._smart_quiz_artists_bigquery(
                    genres=genres,
                    decades=decades,
                    seed_artists=seed_artists,
                    seed_song_artists=seed_song_artists,
                    exclude_artists=exclude_artists,
                    count=count,
                )
            except Exception:
                logger.warning("Smart quiz artists: BigQuery failed, answering from catalog.db", exc_info=True)
        seeds = list(dict.fromkeys([*(seed_artists or []), *(seed_song_artists or [])]))
        try:
            reader = await self._catalog_reader()
            if reader is None:
                return []
            return await asyncio.to_thread(
                self._catalog_smart_artists, reader, genres or [], decades or [], seeds, exclude_artists or [], count
            )
        except Exception:
            logger.warning("Smart quiz artists: catalog.db fallback failed", exc_info=True)
            return []

    def _catalog_smart_artists(
        self,
        reader: CatalogReader,
        genres: list[str],
        decades: list[str],
        seeds: list[str],
        exclude_artists: list[str],
        count: int,
    ) -> list[QuizArtist]:
        """Smart suggestions from catalog.db alone: ``artist_similar`` of the user's picks, then
        the most popular catalog artists in the chosen genres/decades (any artist if those run
        out). Every artist carries a reason; the order is deterministic."""
        seed_by_key = {artist_key(s): s for s in seeds if artist_key(s)}
        excluded = {artist_key(a) for a in exclude_artists} | set(seed_by_key)

        fans_of: dict[str, list[str]] = {}
        scores: dict[str, float] = {}
        for seed_key, similar_key, score in reader.similar(list(seed_by_key)):  # best score first
            if similar_key in excluded:
                continue
            names = fans_of.setdefault(similar_key, [])
            if seed_by_key[seed_key] not in names:
                names.append(seed_by_key[seed_key])
            scores[similar_key] = max(scores.get(similar_key, 0.0), score)

        genre_filter = [] if "other" in genres else genres
        keys = list(fans_of)
        keys += reader.popular_artist_keys(
            limit=count * 2, genres=genre_filter, decades=decades, exclude_keys=[*excluded, *keys]
        )
        if len(keys) < count and (genre_filter or decades):
            keys += reader.popular_artist_keys(limit=count * 2, exclude_keys=[*excluded, *keys])

        summaries = reader.artist_summaries(keys)
        artists: list[tuple[QuizArtist, int]] = []
        for key in keys:
            summary = summaries.get(key)
            if summary is None or summary.song_count == 0:  # the quiz is about artists you can sing
                continue
            artist = QuizArtist(
                mbid=summary.mbid,
                name=summary.name,
                song_count=summary.song_count,
                top_songs=list(summary.top_songs),
                total_brand_count=summary.brand_total,
                primary_decade=summary.primary_decade or "Unknown",
                genres=list(summary.genres),
            )
            artist.suggestion_reason = self._catalog_reason(artist, fans_of.get(key, []), genre_filter, decades)
            artists.append((artist, summary.popularity))

        artists.sort(
            key=lambda a: (
                REASON_PRIORITY[a[0].suggestion_reason.type if a[0].suggestion_reason else "popular_choice"],
                -scores.get(artist_key(a[0].name), 0.0),
                -a[1],
                -a[0].total_brand_count,
                a[0].name.lower(),
            )
        )
        return [a for a, _ in artists[:count]]

    def _catalog_reason(
        self, artist: QuizArtist, fans_of: list[str], genres: list[str], decades: list[str]
    ) -> SuggestionReason:
        """Why a catalog.db-sourced artist is suggested (same reason types as the BigQuery path)."""
        if fans_of:
            if len(fans_of) == 1:
                text = f"Fans of {fans_of[0]} also like"
            elif len(fans_of) == 2:
                text = f"Fans of {fans_of[0]} & {fans_of[1]} also like"
            else:
                text = f"Fans of {fans_of[0]}, {fans_of[1]} & others also like"
            return SuggestionReason(type="fans_also_like", display_text=text, related_to=", ".join(fans_of[:3]))
        matching = sorted(set(genres) & set(artist.genres))
        if matching:
            return SuggestionReason(
                type="genre_match", display_text=f"Based on {self._format_genre_names(matching[:2])}"
            )
        if artist.primary_decade in decades:
            return SuggestionReason(type="decade_match", display_text=f"Popular in the {artist.primary_decade}")
        return SuggestionReason(type="popular_choice", display_text="Popular karaoke choice")

    async def _smart_quiz_artists_bigquery(
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
                mbid_map = await asyncio.to_thread(self.catalog.lookup_mbids_by_names, all_seed_artists)
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

        # "People also like": catalog.db artist_similar (candidate -> seed artists + best score),
        # then ListenBrainz Labs for candidates the catalog doesn't link.
        listenbrainz_matches: dict[str, list[str]] = {}
        similarity_scores: dict[str, float] = {}
        candidate_names = [c.name for c in candidates]
        if all_seed_artists:
            listenbrainz_matches, similarity_scores = await self._catalog_similar_matches(all_seed_artists, candidates)
            remaining = [n for n in candidate_names if n not in listenbrainz_matches]
            if remaining:
                for name, seeds in (
                    await self.listenbrainz.find_similar_artist_matches(
                        seed_artists=all_seed_artists,
                        candidate_names=remaining,
                    )
                ).items():
                    listenbrainz_matches[name] = seeds

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

        # Strongest explanation first, then similarity score, then karaoke popularity; the name makes
        # the order (and so the quiz) deterministic. Every artist carries a reason (popular_choice at worst).
        candidates_with_reasons.sort(
            key=lambda a: (
                REASON_PRIORITY[a.suggestion_reason.type if a.suggestion_reason else "popular_choice"],
                -similarity_scores.get(a.name, 0.0),
                -a.total_brand_count,
                a.name.lower(),
            )
        )
        return candidates_with_reasons[:count]

    async def _catalog_similar_matches(
        self, seed_artists: list[str], candidates: list[QuizArtist]
    ) -> tuple[dict[str, list[str]], dict[str, float]]:
        """Which candidates catalog.db's ``artist_similar`` links to which seed artists.

        Returns ``(candidate name -> seed names (best score first), candidate name -> best score)``;
        both empty while the catalog is warming up.
        """
        reader = await self._catalog_reader()
        if reader is None or not seed_artists or not candidates:
            return {}, {}
        seed_by_key = {artist_key(s): s for s in seed_artists}
        by_key = {artist_key(c.name): c.name for c in candidates}
        rows = await asyncio.to_thread(reader.similar, list(seed_by_key))
        matches: dict[str, list[str]] = {}
        scores: dict[str, float] = {}
        for seed_key, similar_key, score in rows:  # best score first
            name = by_key.get(similar_key)
            seed = seed_by_key.get(seed_key)
            if name is None or seed is None or similar_key in seed_by_key:
                continue
            seeds = matches.setdefault(name, [])
            if seed not in seeds:
                seeds.append(seed)
            scores[name] = max(scores.get(name, 0.0), score)
        return matches, scores

    def _get_artist_genres_detailed(self, artist_names: list[str]) -> dict[str, list[str]]:
        """Get genres for given artists from BigQuery, returning a dict by artist.

        Used to track which seed artist contributed which genres.

        Args:
            artist_names: List of artist names.

        Returns:
            Dict mapping artist name to list of genre IDs.
        """
        from google.cloud import bigquery

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
        from google.cloud import bigquery

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

        Last.fm users (lastfm_users) are matched by MBID if available, else artist name, using
        the artist_mbids array. ("People also like" from catalog.db is added by the caller; it
        never scans decide_users.)

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

        lastfm_users = await self._query_collaborative_sources(
            user_artists_lower,
            user_artist_mbids=valid_mbids if valid_mbids else None,
        )

        # Find similar users and collect their artists
        # Structure: artist_name_lower -> list of (shared_artists, user_artists, source)
        artist_supporters: dict[str, list[tuple[list[str], list[str], str]]] = {}

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
        from google.cloud import bigquery

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
    ) -> list[dict]:
        """Query Last.fm users who share artists with this user.

        MBID-First: When MBIDs are available, uses them for more accurate
        Last.fm user matching. Falls back to name-based matching otherwise.
        Uses array_contains_any on artist_mbids (preferred) or artist_names_lower (fallback).

        Args:
            user_artists_lower: Set of lowercased artist names for matching.
            user_artist_mbids: Optional list of MBIDs for MBID-based matching.

        Returns:
            Last.fm user documents.
        """

        async def query_lastfm() -> list[dict]:
            """Query Last.fm users.

            MBID-First: Prefers MBID-based matching for accuracy.
            Falls back to name-based matching if no MBIDs available.

            Note: array_contains_any has a 30-value limit, so we use
            a subset of the user's artists for the initial filter.

            Limited to MAX_LASTFM_USERS to avoid OOM on memory-constrained Cloud Run (1Gi, most taken by the in-memory catalog).
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
                        limit=self.MAX_LASTFM_USERS,
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
                        limit=self.MAX_LASTFM_USERS,
                    )
            except Exception:
                return []

        return await query_lastfm()

    def _map_spotify_genres_to_ids(self, spotify_genres: list[str]) -> list[str]:
        """Map Spotify genre strings to our genre IDs.

        Args:
            spotify_genres: List of Spotify genre strings (e.g., 'classic rock').

        Returns:
            List of our genre IDs (e.g., 'classic-rock').
        """
        return map_genres(spotify_genres)

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
        from google.cloud import bigquery

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
        from google.cloud import bigquery

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
        from google.cloud import bigquery

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
        artist_affinities: list[tuple[str, Literal["occasionally", "like", "love"]]] | None = None,
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
            known_artists: List of artist names the user knows (legacy).
            artist_affinities: List of (artist_name, affinity) tuples where affinity
                is "occasionally", "like", or "love".
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
        known_song_ids = known_song_ids or []
        known_artists = known_artists or []

        # If artist_affinities is provided, merge with known_artists for backward compat
        # Artist affinities takes precedence (new format with affinity levels)
        if artist_affinities:
            affinity_artists = [name for name, _ in artist_affinities]
            # Combine with known_artists, deduplicate
            all_artists_lower = {a.lower() for a in known_artists}
            for artist in affinity_artists:
                if artist.lower() not in all_artists_lower:
                    known_artists.append(artist)
                    all_artists_lower.add(artist.lower())

        # Top songs for the chosen artists (catalog.db; BigQuery while it warms up) plus any legacy
        # direct song picks, written with one read and one batch rather than a round trip per song.
        songs: list[dict] = []
        if known_artists:
            songs.extend(await self._top_songs_for_artists(known_artists, per_artist=5))
        if known_song_ids:
            for song in await asyncio.to_thread(self._get_songs_by_ids, known_song_ids):
                songs.append({**song, "song_key": song_key(song["artist"], song["title"])})

        candidates = {f"{user_id}:{song['id']}": song for song in songs}
        existing = await self.firestore.get_many(self.USER_SONGS_COLLECTION, list(candidates))
        new_docs = {
            doc_id: {
                "id": doc_id,
                "user_id": user_id,
                "song_id": song["id"],
                "song_key": song["song_key"],
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
            for doc_id, song in candidates.items()
            if doc_id not in existing
        }
        if new_docs:
            await self.firestore.batch_set(self.USER_SONGS_COLLECTION, new_docs)
        songs_added = len(new_docs)

        # Update user profile with quiz data
        await self._update_user_quiz_data(
            user_id=user_id,
            known_song_ids=known_song_ids,
            known_artists=known_artists,
            artist_affinities=artist_affinities,
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
            recommendations_ready=len(known_song_ids) > 0
            or len(known_artists) > 0
            or len(manual_artists or []) > 0
            or len(artist_affinities or []) > 0,
        )

    async def _top_songs_for_artists(self, artist_names: list[str], per_artist: int) -> list[dict]:
        """Top songs per artist as ``{id, song_key, artist, title}``.

        ``id`` is the KaraokeNerds id when catalog.db maps one (so docs written by earlier quizzes
        are recognised), else a digest of the song_key. BigQuery answers while the catalog warms up.
        """
        reader = await self._catalog_reader()
        if reader is None:
            rows = await asyncio.to_thread(self._get_songs_by_artists, artist_names, per_artist)
            return [{**r, "song_key": song_key(r["artist"], r["title"])} for r in rows]

        def lookup() -> list[dict]:
            keys = list(dict.fromkeys(artist_key(a) for a in artist_names))
            found = reader.top_songs_for_artists(keys, per_artist)
            kn_ids = reader.kn_ids_for_song_keys([r["song_key"] for r in found])
            return [
                {
                    "id": str(kn_ids[r["song_key"]])
                    if r["song_key"] in kn_ids
                    else "sk:" + hashlib.sha1(r["song_key"].encode()).hexdigest()[:24],  # noqa: S324
                    "song_key": r["song_key"],
                    "artist": r["artist"],
                    "title": r["title"],
                }
                for r in found
            ]

        return await asyncio.to_thread(lookup)

    def _get_songs_by_artists(self, artist_names: list[str], limit_per_artist: int = 5) -> list[dict]:
        """Get top songs for given artists from BigQuery.

        Args:
            artist_names: List of artist names.
            limit_per_artist: Max songs per artist.

        Returns:
            List of dicts with id, artist, title.
        """
        from google.cloud import bigquery

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
        from google.cloud import bigquery

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
        artist_affinities: list[tuple[str, Literal["occasionally", "like", "love"]]] | None,
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
            artist_affinities: List of (artist_name, affinity) tuples.
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
            artist_mbids = await self._known_artist_mbids(known_artists)

        # MBID-First: Resolve Spotify IDs to MBIDs for manual artists (bulk lookup)
        manual_artists_with_mbid: list[dict[str, Any]] = []
        if manual_artists:
            # Bulk lookup all Spotify IDs in one query (filter out None values)
            spotify_ids = [a.artist_id for a in manual_artists if a.artist_id]
            spotify_to_mbid: dict[str, str] = {}
            if spotify_ids and not self.settings.bigquery_disabled:
                try:
                    spotify_to_mbid = await asyncio.to_thread(self.catalog.lookup_mbids_by_spotify_ids, spotify_ids)
                except Exception:
                    # Enrichment only: the artists are saved without an MBID rather than failing the submit.
                    logger.warning("Quiz submit: Spotify->MBID lookup failed", exc_info=True)

            for a in manual_artists:
                # Look up MBID from Spotify ID mapping (use artist's mbid if already provided)
                manual_mbid: str | None = a.mbid
                if not manual_mbid and a.artist_id:
                    manual_mbid = spotify_to_mbid.get(a.artist_id)
                manual_artists_with_mbid.append(
                    {
                        "artist_id": a.artist_id,  # Spotify ID (backward compat)
                        "artist_name": a.artist_name,
                        "genres": a.genres or [],
                        "mbid": manual_mbid,  # MBID-First: Add MusicBrainz ID
                    }
                )
                # Also add to mbids list if resolved
                if manual_mbid:
                    artist_mbids.append(manual_mbid)

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

        # Store artist affinities (new format with affinity levels)
        if artist_affinities:
            update_data["quiz_artist_affinities"] = [
                {"artist_name": name, "affinity": affinity} for name, affinity in artist_affinities
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

    async def save_progress(
        self,
        user_id: str,
        step: int,
        genres: list[str] | None = None,
        decades: list[str] | None = None,
        artist_affinities: list[tuple[str, Literal["occasionally", "like", "love"]]] | None = None,
        manual_artists: list[dict[str, Any]] | None = None,
        enjoy_songs: list[dict[str, Any]] | None = None,
        energy_preference: Literal["chill", "medium", "high"] | None = None,
        vocal_comfort_pref: Literal["easy", "challenging", "any"] | None = None,
        crowd_pleaser_pref: Literal["hits", "deep_cuts", "any"] | None = None,
    ) -> None:
        """Save quiz progress for auto-save.

        This stores progress in a subcollection so users can resume
        their quiz if they close the browser.

        Args:
            user_id: User's ID.
            step: Current quiz step (1-5).
            genres: Selected genre IDs.
            decades: Selected decade preferences.
            artist_affinities: List of (artist_name, affinity) tuples.
            manual_artists: Manually added artists.
            enjoy_songs: Songs marked as enjoy singing.
            energy_preference: Energy preference.
            vocal_comfort_pref: Vocal comfort preference.
            crowd_pleaser_pref: Crowd pleaser preference.
        """
        now = datetime.now(UTC)

        progress_data: dict[str, Any] = {
            "step": step,
            "updated_at": now.isoformat(),
        }

        if genres is not None:
            progress_data["genres"] = genres

        if decades is not None:
            progress_data["decades"] = decades

        if artist_affinities is not None:
            progress_data["artist_affinities"] = [
                {"artist_name": name, "affinity": affinity} for name, affinity in artist_affinities
            ]

        if manual_artists is not None:
            progress_data["manual_artists"] = manual_artists

        if enjoy_songs is not None:
            progress_data["enjoy_songs"] = enjoy_songs

        if energy_preference is not None:
            progress_data["energy_preference"] = energy_preference

        if vocal_comfort_pref is not None:
            progress_data["vocal_comfort_pref"] = vocal_comfort_pref

        if crowd_pleaser_pref is not None:
            progress_data["crowd_pleaser_pref"] = crowd_pleaser_pref

        # Store progress in a user-specific document
        # Using user_id as the document ID for easy upsert
        await self.firestore.set_document(
            "quiz_progress",
            user_id,
            progress_data,
        )

    async def get_progress(self, user_id: str) -> dict[str, Any] | None:
        """The auto-saved quiz progress document, or None if nothing was saved."""
        return await self.firestore.get_document("quiz_progress", user_id)

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
