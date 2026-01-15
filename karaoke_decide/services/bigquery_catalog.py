"""BigQuery-based song catalog service."""

import logging
import re
import time
from dataclasses import dataclass

from google.cloud import bigquery

logger = logging.getLogger(__name__)


def _normalize_for_matching(text: str) -> str:
    """Normalize text for matching.

    Must match the BigQuery REGEXP_REPLACE normalization exactly.
    Removes ALL punctuation (including apostrophes) for simpler matching.
    """
    if not text:
        return ""
    # Lowercase
    result = text.lower()
    # Remove ALL punctuation (including apostrophes) - must match BigQuery regex
    # BigQuery uses: r'[^a-z0-9 ]' which removes everything except letters, numbers, space
    result = re.sub(r"[^a-z0-9 ]", " ", result)
    # Collapse multiple whitespace
    result = re.sub(r"\s+", " ", result)
    return result.strip()


@dataclass
class SongResult:
    """Song from the catalog."""

    id: int
    artist: str
    title: str
    brands: str
    brand_count: int  # Derived from brands


@dataclass
class ArtistMetadata:
    """Artist metadata from Spotify catalog."""

    artist_id: str
    artist_name: str
    popularity: int
    genres: list[str]


@dataclass
class ArtistSearchResult:
    """Artist search result for autocomplete."""

    artist_id: str
    artist_name: str
    popularity: int
    genres: list[str]


@dataclass
class ArtistSearchResultMBID:
    """Artist search result with MBID as primary identifier.

    Used for MBID-first search where MusicBrainz ID is the canonical identifier.
    Spotify data is enrichment (optional).
    """

    artist_mbid: str  # Primary identifier (MusicBrainz UUID)
    artist_name: str
    disambiguation: str | None  # e.g., "UK rock band"
    artist_type: str | None  # Person, Group, Orchestra, etc.
    popularity: int  # From Spotify if mapped, else 50
    tags: list[str]  # MusicBrainz community tags (genres)
    # Enrichment from Spotify (optional)
    spotify_artist_id: str | None
    spotify_genres: list[str] | None


@dataclass
class TrackSearchResult:
    """Track search result for autocomplete."""

    track_id: str
    track_name: str
    artist_name: str
    artist_id: str
    popularity: int
    duration_ms: int
    explicit: bool


@dataclass
class RecordingSearchResult:
    """Recording search result with MBID as primary identifier.

    Represents a MusicBrainz recording with optional Spotify enrichment.
    """

    recording_mbid: str  # Primary identifier (MusicBrainz UUID)
    title: str
    artist_credit: str | None  # Display string like "Artist feat. Other"
    length_ms: int | None
    disambiguation: str | None  # e.g., "live version", "acoustic"
    # Enrichment from Spotify (optional, via ISRC)
    spotify_track_id: str | None
    spotify_popularity: int | None


@dataclass
class KaraokeRecordingLink:
    """Link between a karaoke song and canonical recording(s).

    Maps karaoke catalog songs to MusicBrainz recordings and/or Spotify tracks.
    """

    karaoke_id: int
    recording_mbid: str | None  # MusicBrainz recording UUID
    spotify_track_id: str | None  # Spotify track ID
    match_method: str  # 'isrc', 'exact_name', 'fuzzy_name'
    match_confidence: float  # 0.0-1.0


class BigQueryCatalogService:
    """Service for querying song catalog from BigQuery."""

    PROJECT_ID = "nomadkaraoke"
    DATASET_ID = "karaoke_decide"

    # Cache for artist search results (key: query_prefix, value: (timestamp, results))
    _artist_search_cache: dict[str, tuple[float, list["ArtistSearchResult"]]] = {}
    _track_search_cache: dict[str, tuple[float, list["TrackSearchResult"]]] = {}
    CACHE_TTL = 300  # 5 minutes

    def __init__(self, client: bigquery.Client | None = None):
        self.client = client or bigquery.Client(project=self.PROJECT_ID)

    @staticmethod
    def normalize_for_matching(text: str) -> str:
        """Normalize text for matching. Public wrapper around module-level function.

        Use this when looking up results from lookup_mbids_by_names() to ensure
        consistent key normalization.
        """
        return _normalize_for_matching(text)

    def search_songs(
        self,
        query: str,
        limit: int = 20,
        offset: int = 0,
        min_brands: int = 0,
    ) -> list[SongResult]:
        """Search songs by artist or title.

        Args:
            query: Search term (matches artist or title)
            limit: Max results to return
            offset: Pagination offset
            min_brands: Minimum number of karaoke brands (popularity filter)

        Returns:
            List of matching songs
        """
        sql = f"""
            SELECT * FROM (
                SELECT
                    Id as id,
                    Artist as artist,
                    Title as title,
                    Brands as brands,
                    ARRAY_LENGTH(SPLIT(Brands, ',')) as brand_count
                FROM `{self.PROJECT_ID}.{self.DATASET_ID}.karaokenerds_raw`
                WHERE
                    LOWER(Artist) LIKE LOWER(@query)
                    OR LOWER(Title) LIKE LOWER(@query)
            )
            WHERE brand_count >= @min_brands
            ORDER BY brand_count DESC, Artist, Title
            LIMIT @limit OFFSET @offset
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("query", "STRING", f"%{query}%"),
                bigquery.ScalarQueryParameter("min_brands", "INT64", min_brands),
                bigquery.ScalarQueryParameter("limit", "INT64", limit),
                bigquery.ScalarQueryParameter("offset", "INT64", offset),
            ]
        )

        results = self.client.query(sql, job_config=job_config).result()
        return [
            SongResult(
                id=row.id,
                artist=row.artist,
                title=row.title,
                brands=row.brands,
                brand_count=row.brand_count,
            )
            for row in results
        ]

    def get_song_by_id(self, song_id: int) -> SongResult | None:
        """Get a single song by ID."""
        sql = f"""
            SELECT
                Id as id,
                Artist as artist,
                Title as title,
                Brands as brands,
                ARRAY_LENGTH(SPLIT(Brands, ',')) as brand_count
            FROM `{self.PROJECT_ID}.{self.DATASET_ID}.karaokenerds_raw`
            WHERE Id = @song_id
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("song_id", "INT64", song_id),
            ]
        )

        results = list(self.client.query(sql, job_config=job_config).result())
        if not results:
            return None

        row = results[0]
        return SongResult(
            id=row.id,
            artist=row.artist,
            title=row.title,
            brands=row.brands,
            brand_count=row.brand_count,
        )

    def get_popular_songs(
        self,
        limit: int = 50,
        min_brands: int = 5,
    ) -> list[SongResult]:
        """Get most popular karaoke songs by brand coverage.

        Songs covered by more karaoke brands are more popular.
        """
        sql = f"""
            SELECT * FROM (
                SELECT
                    Id as id,
                    Artist as artist,
                    Title as title,
                    Brands as brands,
                    ARRAY_LENGTH(SPLIT(Brands, ',')) as brand_count
                FROM `{self.PROJECT_ID}.{self.DATASET_ID}.karaokenerds_raw`
            )
            WHERE brand_count >= @min_brands
            ORDER BY brand_count DESC
            LIMIT @limit
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("min_brands", "INT64", min_brands),
                bigquery.ScalarQueryParameter("limit", "INT64", limit),
            ]
        )

        results = self.client.query(sql, job_config=job_config).result()
        return [
            SongResult(
                id=row.id,
                artist=row.artist,
                title=row.title,
                brands=row.brands,
                brand_count=row.brand_count,
            )
            for row in results
        ]

    def get_songs_by_artist(
        self,
        artist: str,
        limit: int = 50,
    ) -> list[SongResult]:
        """Get all songs by an artist."""
        sql = f"""
            SELECT
                Id as id,
                Artist as artist,
                Title as title,
                Brands as brands,
                ARRAY_LENGTH(SPLIT(Brands, ',')) as brand_count
            FROM `{self.PROJECT_ID}.{self.DATASET_ID}.karaokenerds_raw`
            WHERE LOWER(Artist) = LOWER(@artist)
            ORDER BY brand_count DESC, Title
            LIMIT @limit
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("artist", "STRING", artist),
                bigquery.ScalarQueryParameter("limit", "INT64", limit),
            ]
        )

        results = self.client.query(sql, job_config=job_config).result()
        return [
            SongResult(
                id=row.id,
                artist=row.artist,
                title=row.title,
                brands=row.brands,
                brand_count=row.brand_count,
            )
            for row in results
        ]

    def count_songs(self) -> int:
        """Get total number of songs in catalog."""
        sql = f"""
            SELECT COUNT(*) as count
            FROM `{self.PROJECT_ID}.{self.DATASET_ID}.karaokenerds_raw`
        """
        result = list(self.client.query(sql).result())[0]
        return int(result.count)

    def get_all_songs(self) -> list[SongResult]:
        """Load entire catalog for in-memory lookup.

        Returns all songs from the karaoke catalog. This is used by
        CatalogLookup to pre-load the entire catalog into memory
        for instant matching during sync.

        Returns:
            List of all SongResult objects (~275K entries).
        """
        sql = f"""
            SELECT
                Id as id,
                Artist as artist,
                Title as title,
                Brands as brands,
                ARRAY_LENGTH(SPLIT(Brands, ',')) as brand_count
            FROM `{self.PROJECT_ID}.{self.DATASET_ID}.karaokenerds_raw`
        """

        logger.info("Loading all songs from BigQuery...")
        results = self.client.query(sql).result()

        songs = [
            SongResult(
                id=row.id,
                artist=row.artist,
                title=row.title,
                brands=row.brands,
                brand_count=row.brand_count,
            )
            for row in results
        ]

        logger.info(f"Loaded {len(songs):,} songs from BigQuery")
        return songs

    def get_stats(self) -> dict:
        """Get catalog statistics."""
        sql = f"""
            SELECT
                COUNT(*) as total_songs,
                COUNT(DISTINCT Artist) as unique_artists,
                MAX(ARRAY_LENGTH(SPLIT(Brands, ','))) as max_brand_count,
                AVG(ARRAY_LENGTH(SPLIT(Brands, ','))) as avg_brand_count
            FROM `{self.PROJECT_ID}.{self.DATASET_ID}.karaokenerds_raw`
        """
        result = list(self.client.query(sql).result())[0]
        return {
            "total_songs": result.total_songs,
            "unique_artists": result.unique_artists,
            "max_brand_count": result.max_brand_count,
            "avg_brand_count": round(result.avg_brand_count, 2),
        }

    def batch_match_tracks(
        self,
        tracks: list[tuple[str, str]],
    ) -> dict[tuple[str, str], SongResult]:
        """Match multiple tracks in a single BigQuery query.

        Uses OR conditions to find exact matches for all tracks at once,
        which is much more efficient than one query per track.

        Args:
            tracks: List of (normalized_artist, normalized_title) tuples

        Returns:
            Dict mapping (artist, title) tuples to SongResult for found matches
        """
        if not tracks:
            return {}

        logger.info(f"BigQuery batch_match_tracks: received {len(tracks)} tracks")

        # Build OR conditions for each track
        # Using parameterized queries would require dynamic parameter count,
        # so we sanitize and normalize for matching
        # IMPORTANT: Must normalize BOTH the input AND the catalog data identically
        # The catalog has punctuation (commas, etc.) that input normalization removes
        conditions = []
        for artist, title in tracks:
            # Escape single quotes by doubling them (BigQuery SQL escaping)
            # Input is already normalized (lowercase, punctuation removed except apostrophes)
            safe_artist = artist.replace("'", "''").lower()
            safe_title = title.replace("'", "''").lower()
            # Use REGEXP_REPLACE to normalize catalog data the same way:
            # 1. Lowercase
            # 2. Replace non-alphanumeric (except space and apostrophe) with space
            # 3. Collapse multiple spaces to single space
            # 4. Trim whitespace
            # NOTE: BigQuery uses single quotes for strings, and r'...' for raw regex
            normalize_sql = "TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER({field}), r'[^a-z0-9 ]', ' '), r' +', ' '))"
            normalized_artist = normalize_sql.format(field="Artist")
            normalized_title = normalize_sql.format(field="Title")
            conditions.append(f"({normalized_artist} = '{safe_artist}' AND {normalized_title} = '{safe_title}')")

        # Process in chunks to avoid query size limits (max ~100 tracks per query)
        chunk_size = 100
        all_results: dict[tuple[str, str], SongResult] = {}

        for i in range(0, len(conditions), chunk_size):
            chunk_conditions = conditions[i : i + chunk_size]
            where_clause = " OR ".join(chunk_conditions)

            sql = f"""
                SELECT
                    Id as id,
                    Artist as artist,
                    Title as title,
                    Brands as brands,
                    ARRAY_LENGTH(SPLIT(Brands, ',')) as brand_count
                FROM `{self.PROJECT_ID}.{self.DATASET_ID}.karaokenerds_raw`
                WHERE {where_clause}
            """

            logger.info(f"BigQuery: executing chunk query for {len(chunk_conditions)} tracks")
            results = self.client.query(sql).result()

            chunk_matches = 0
            for row in results:
                chunk_matches += 1
                # Create key using NORMALIZED values to match input
                # Must use same normalization as TrackMatcher applies to input
                key = (_normalize_for_matching(row.artist), _normalize_for_matching(row.title))
                # If multiple matches (same song different brands), keep highest brand_count
                if key not in all_results or row.brand_count > all_results[key].brand_count:
                    all_results[key] = SongResult(
                        id=row.id,
                        artist=row.artist,
                        title=row.title,
                        brands=row.brands,
                        brand_count=row.brand_count,
                    )
            logger.info(f"BigQuery: chunk returned {chunk_matches} matches")

        logger.info(f"BigQuery batch_match_tracks: total {len(all_results)} unique matches")
        return all_results

    def get_artists_metadata(
        self,
        artist_names: list[str],
    ) -> dict[str, ArtistMetadata]:
        """Look up metadata for artists by name from Spotify artist catalog.

        Args:
            artist_names: List of artist names to look up

        Returns:
            Dict mapping normalized artist name -> ArtistMetadata
            Only includes artists that were found in the catalog.
        """
        if not artist_names:
            return {}

        logger.info(f"Looking up metadata for {len(artist_names)} artists")

        # Normalize names for matching
        normalized_names = {_normalize_for_matching(name): name for name in artist_names if name}
        if not normalized_names:
            return {}

        # Build query to find matching artists
        # First get artist IDs and metadata, then join with genres
        chunk_size = 100
        all_results: dict[str, ArtistMetadata] = {}

        normalized_list = list(normalized_names.keys())
        for i in range(0, len(normalized_list), chunk_size):
            chunk = normalized_list[i : i + chunk_size]

            # Build OR conditions for name matching
            conditions = []
            for name in chunk:
                safe_name = name.replace("'", "''")
                # Same normalization as we use for songs
                normalize_sql = (
                    "TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(artist_name), r'[^a-z0-9 ]', ' '), r' +', ' '))"
                )
                conditions.append(f"{normalize_sql} = '{safe_name}'")

            where_clause = " OR ".join(conditions)

            # Query to get artists with their genres aggregated
            sql = f"""
                WITH matched_artists AS (
                    SELECT
                        artist_id,
                        artist_name,
                        popularity,
                        TRIM(REGEXP_REPLACE(REGEXP_REPLACE(LOWER(artist_name), r'[^a-z0-9 ]', ' '), r' +', ' ')) as normalized_name
                    FROM `{self.PROJECT_ID}.{self.DATASET_ID}.spotify_artists`
                    WHERE {where_clause}
                )
                SELECT
                    ma.artist_id,
                    ma.artist_name,
                    ma.popularity,
                    ma.normalized_name,
                    ARRAY_AGG(DISTINCT g.genre IGNORE NULLS) as genres
                FROM matched_artists ma
                LEFT JOIN `{self.PROJECT_ID}.{self.DATASET_ID}.spotify_artist_genres` g
                    ON ma.artist_id = g.artist_id
                GROUP BY ma.artist_id, ma.artist_name, ma.popularity, ma.normalized_name
            """

            logger.info(f"BigQuery: querying artist metadata for chunk of {len(chunk)} artists")
            results = self.client.query(sql).result()

            for row in results:
                # Use normalized name as key for matching
                key = row.normalized_name
                genres = list(row.genres) if row.genres else []
                all_results[key] = ArtistMetadata(
                    artist_id=row.artist_id,
                    artist_name=row.artist_name,
                    popularity=row.popularity or 0,
                    genres=genres[:5],  # Limit to 5 genres like Spotify API does
                )

        logger.info(f"BigQuery: found metadata for {len(all_results)} artists")
        return all_results

    def lookup_artist_by_name(self, artist_name: str) -> ArtistMetadata | None:
        """Fast single-artist lookup using pre-normalized table.

        Uses the pre-computed spotify_artists_normalized table for O(1) lookups
        instead of runtime regex normalization on 500K+ rows.

        Args:
            artist_name: Artist name to look up

        Returns:
            ArtistMetadata if found, None otherwise
        """
        if not artist_name:
            return None

        normalized = _normalize_for_matching(artist_name)
        if not normalized:
            return None

        sql = f"""
            SELECT
                artist_id,
                artist_name,
                popularity,
                genres
            FROM `{self.PROJECT_ID}.{self.DATASET_ID}.spotify_artists_normalized`
            WHERE normalized_name = @normalized_name
            ORDER BY popularity DESC
            LIMIT 1
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("normalized_name", "STRING", normalized),
            ]
        )

        results = list(self.client.query(sql, job_config=job_config).result())
        if not results:
            return None

        row = results[0]
        genres = list(row.genres) if row.genres else []
        return ArtistMetadata(
            artist_id=row.artist_id,
            artist_name=row.artist_name,
            popularity=row.popularity or 0,
            genres=genres[:5],
        )

    def batch_lookup_artists_by_name(
        self,
        artist_names: list[str],
    ) -> dict[str, ArtistMetadata]:
        """Fast batch artist lookup using pre-normalized table.

        Uses the pre-computed spotify_artists_normalized table for fast lookups.
        Much faster than get_artists_metadata() which does runtime normalization.

        Args:
            artist_names: List of artist names to look up

        Returns:
            Dict mapping normalized artist name -> ArtistMetadata
        """
        if not artist_names:
            return {}

        # Normalize names for matching
        normalized_to_original = {}
        for name in artist_names:
            if name:
                normalized = _normalize_for_matching(name)
                if normalized:
                    normalized_to_original[normalized] = name

        if not normalized_to_original:
            return {}

        logger.info(f"Looking up metadata for {len(normalized_to_original)} artists (fast)")

        # Process in chunks
        chunk_size = 100
        all_results: dict[str, ArtistMetadata] = {}
        normalized_list = list(normalized_to_original.keys())

        for i in range(0, len(normalized_list), chunk_size):
            chunk = normalized_list[i : i + chunk_size]

            # Use parameterized IN clause (BigQuery supports UNNEST for arrays)
            sql = f"""
                SELECT
                    artist_id,
                    artist_name,
                    normalized_name,
                    popularity,
                    genres
                FROM `{self.PROJECT_ID}.{self.DATASET_ID}.spotify_artists_normalized`
                WHERE normalized_name IN UNNEST(@names)
            """

            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ArrayQueryParameter("names", "STRING", chunk),
                ]
            )

            results = self.client.query(sql, job_config=job_config).result()

            # Group by normalized name and pick highest popularity
            best_match: dict[str, tuple[int, ArtistMetadata]] = {}
            for row in results:
                key = row.normalized_name
                pop = row.popularity or 0
                if key not in best_match or pop > best_match[key][0]:
                    genres = list(row.genres) if row.genres else []
                    best_match[key] = (
                        pop,
                        ArtistMetadata(
                            artist_id=row.artist_id,
                            artist_name=row.artist_name,
                            popularity=pop,
                            genres=genres[:5],
                        ),
                    )

            for key, (_, metadata) in best_match.items():
                all_results[key] = metadata

        logger.info(f"BigQuery: found metadata for {len(all_results)} artists (fast)")
        return all_results

    def get_artist_index(
        self,
        min_popularity: int = 30,
    ) -> list[ArtistSearchResult]:
        """Get all artists above a popularity threshold for client-side search.

        Returns a large list of artists for building a client-side search index.
        This is designed to be called once and cached server-side.

        Args:
            min_popularity: Minimum popularity score (0-100, default 30)

        Returns:
            List of ArtistSearchResult sorted by popularity (highest first)
        """
        logger.info(f"Loading artist index with min_popularity={min_popularity}")

        sql = f"""
            SELECT
                artist_id,
                artist_name,
                popularity
            FROM `{self.PROJECT_ID}.{self.DATASET_ID}.spotify_artists_normalized`
            WHERE popularity >= @min_popularity
            ORDER BY popularity DESC
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("min_popularity", "INT64", min_popularity),
            ]
        )

        results = self.client.query(sql, job_config=job_config).result()

        artist_list = [
            ArtistSearchResult(
                artist_id=row.artist_id,
                artist_name=row.artist_name,
                popularity=row.popularity or 0,
                genres=[],  # Skip genres for index to reduce size
            )
            for row in results
        ]

        logger.info(f"Loaded {len(artist_list)} artists for index")
        return artist_list

    def get_artist_index_mbid(
        self,
        min_popularity: int = 30,
    ) -> list[ArtistSearchResultMBID]:
        """Get all artists with MBIDs above a popularity threshold for client-side search.

        Uses MusicBrainz as source of truth with Spotify enrichment.
        Returns a large list of artists for building a client-side search index.

        Args:
            min_popularity: Minimum popularity score (0-100, default 30)

        Returns:
            List of ArtistSearchResultMBID sorted by popularity (highest first)
        """
        logger.info(f"Loading MBID artist index with min_popularity={min_popularity}")

        sql = f"""
            SELECT
                artist_mbid,
                artist_name,
                spotify_artist_id,
                popularity
            FROM `{self.PROJECT_ID}.{self.DATASET_ID}.mb_artists_normalized`
            WHERE popularity >= @min_popularity
              AND spotify_artist_id IS NOT NULL
            ORDER BY popularity DESC
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("min_popularity", "INT64", min_popularity),
            ]
        )

        try:
            results = self.client.query(sql, job_config=job_config).result()

            artist_list = [
                ArtistSearchResultMBID(
                    artist_mbid=row.artist_mbid,
                    artist_name=row.artist_name,
                    disambiguation=None,  # Skip to reduce payload
                    artist_type=None,  # Skip to reduce payload
                    popularity=row.popularity or 50,
                    tags=[],  # Skip to reduce payload
                    spotify_artist_id=row.spotify_artist_id,
                    spotify_genres=None,  # Skip to reduce payload
                )
                for row in results
            ]

            logger.info(f"Loaded {len(artist_list)} MBID artists for index")
            return artist_list

        except Exception as e:
            logger.warning(f"MBID artist index failed (tables may not exist): {e}")
            return []

    def search_artists(
        self,
        query: str,
        limit: int = 10,
        min_popularity: int = 20,
    ) -> list[ArtistSearchResult]:
        """Search artists by name prefix for autocomplete.

        Uses the pre-computed normalized table for fast prefix matching.
        Results are sorted by popularity (highest first).

        Args:
            query: Search query (will be normalized)
            limit: Maximum results to return (default 10)
            min_popularity: Minimum popularity score (0-100, default 20 for faster queries)

        Returns:
            List of ArtistSearchResult sorted by popularity
        """
        if not query or len(query) < 2:
            return []

        normalized = _normalize_for_matching(query)
        if not normalized:
            return []

        # Check cache first
        cache_key = f"{normalized}:{limit}:{min_popularity}"
        now = time.time()
        if cache_key in self._artist_search_cache:
            cached_time, cached_results = self._artist_search_cache[cache_key]
            if now - cached_time < self.CACHE_TTL:
                logger.debug(f"Artist search cache hit for '{normalized}'")
                return cached_results

        # Use prefix matching on normalized name with popularity filter
        # The popularity filter significantly reduces scan time
        sql = f"""
            SELECT
                artist_id,
                artist_name,
                popularity,
                genres
            FROM `{self.PROJECT_ID}.{self.DATASET_ID}.spotify_artists_normalized`
            WHERE normalized_name LIKE @query_prefix
              AND popularity >= @min_popularity
            ORDER BY popularity DESC
            LIMIT @limit
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("query_prefix", "STRING", f"{normalized}%"),
                bigquery.ScalarQueryParameter("min_popularity", "INT64", min_popularity),
                bigquery.ScalarQueryParameter("limit", "INT64", limit),
            ]
        )

        results = self.client.query(sql, job_config=job_config).result()

        artist_results = [
            ArtistSearchResult(
                artist_id=row.artist_id,
                artist_name=row.artist_name,
                popularity=row.popularity or 0,
                genres=list(row.genres)[:5] if row.genres else [],
            )
            for row in results
        ]

        # Cache the results
        self._artist_search_cache[cache_key] = (now, artist_results)

        # Clean old cache entries periodically (simple cleanup)
        if len(self._artist_search_cache) > 1000:
            cutoff = now - self.CACHE_TTL
            self._artist_search_cache = {k: v for k, v in self._artist_search_cache.items() if v[0] > cutoff}

        return artist_results

    def search_tracks(
        self,
        query: str,
        limit: int = 10,
        min_popularity: int = 30,
    ) -> list[TrackSearchResult]:
        """Search tracks by title or artist for autocomplete.

        Uses the pre-computed spotify_tracks_normalized table for fast
        prefix matching. Searches both track title and artist name.
        Results are sorted by popularity (highest first).

        Args:
            query: Search query (will be normalized)
            limit: Maximum results to return (default 10)
            min_popularity: Minimum popularity score (0-100, default 30 for faster queries)

        Returns:
            List of TrackSearchResult sorted by popularity
        """
        if not query or len(query) < 2:
            return []

        normalized = _normalize_for_matching(query)
        if not normalized:
            return []

        # Check cache first
        cache_key = f"{normalized}:{limit}:{min_popularity}"
        now = time.time()
        if cache_key in self._track_search_cache:
            cached_time, cached_results = self._track_search_cache[cache_key]
            if now - cached_time < self.CACHE_TTL:
                logger.debug(f"Track search cache hit for '{normalized}'")
                return cached_results

        # Search both title and artist with popularity filter
        sql = f"""
            SELECT DISTINCT
                track_id,
                track_name,
                artist_name,
                artist_id,
                popularity,
                duration_ms,
                explicit
            FROM `{self.PROJECT_ID}.{self.DATASET_ID}.spotify_tracks_normalized`
            WHERE (normalized_title LIKE @query_prefix OR normalized_artist LIKE @query_prefix)
              AND popularity >= @min_popularity
            ORDER BY popularity DESC
            LIMIT @limit
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("query_prefix", "STRING", f"{normalized}%"),
                bigquery.ScalarQueryParameter("min_popularity", "INT64", min_popularity),
                bigquery.ScalarQueryParameter("limit", "INT64", limit),
            ]
        )

        results = self.client.query(sql, job_config=job_config).result()

        track_results = [
            TrackSearchResult(
                track_id=row.track_id,
                track_name=row.track_name,
                artist_name=row.artist_name,
                artist_id=row.artist_id or "",
                popularity=row.popularity or 0,
                duration_ms=row.duration_ms or 0,
                explicit=row.explicit or False,
            )
            for row in results
        ]

        # Cache the results
        self._track_search_cache[cache_key] = (now, track_results)

        # Clean old cache entries periodically
        if len(self._track_search_cache) > 1000:
            cutoff = now - self.CACHE_TTL
            self._track_search_cache = {k: v for k, v in self._track_search_cache.items() if v[0] > cutoff}

        return track_results

    # =========================================================================
    # MBID-First Search Methods (MusicBrainz as source of truth)
    # =========================================================================

    # Cache for MBID search
    _mbid_search_cache: dict[str, tuple[float, list["ArtistSearchResultMBID"]]] = {}

    def search_artists_mbid(
        self,
        query: str,
        limit: int = 10,
        min_popularity: int = 0,
    ) -> list[ArtistSearchResultMBID]:
        """Search artists using MusicBrainz as source of truth.

        Uses the mb_artists table with Spotify enrichment for popularity.
        MusicBrainz has more comprehensive artist coverage than Spotify.

        Args:
            query: Search query (will be normalized)
            limit: Maximum results to return (default 10)
            min_popularity: Minimum popularity score (0-100, default 0 to include all MB artists)

        Returns:
            List of ArtistSearchResultMBID sorted by popularity
        """
        if not query or len(query) < 2:
            return []

        normalized = _normalize_for_matching(query)
        if not normalized:
            return []

        # Check cache first
        cache_key = f"mbid:{normalized}:{limit}:{min_popularity}"
        now = time.time()
        if cache_key in self._mbid_search_cache:
            cached_time, cached_results = self._mbid_search_cache[cache_key]
            if now - cached_time < self.CACHE_TTL:
                logger.debug(f"MBID search cache hit for '{normalized}'")
                return cached_results

        # Query from pre-joined normalized table for performance
        sql = f"""
            SELECT
                artist_mbid,
                artist_name,
                disambiguation,
                artist_type,
                name_normalized,
                spotify_artist_id,
                popularity,
                spotify_genres,
                mb_tags AS tags
            FROM `{self.PROJECT_ID}.{self.DATASET_ID}.mb_artists_normalized`
            WHERE name_normalized LIKE @query_prefix
              AND popularity >= @min_popularity
            ORDER BY popularity DESC
            LIMIT @limit
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("query_prefix", "STRING", f"{normalized}%"),
                bigquery.ScalarQueryParameter("min_popularity", "INT64", min_popularity),
                bigquery.ScalarQueryParameter("limit", "INT64", limit),
            ]
        )

        try:
            results = self.client.query(sql, job_config=job_config).result()

            artist_results = [
                ArtistSearchResultMBID(
                    artist_mbid=row.artist_mbid,
                    artist_name=row.artist_name,
                    disambiguation=row.disambiguation,
                    artist_type=row.artist_type,
                    popularity=row.popularity or 50,
                    tags=list(row.tags) if row.tags else [],
                    spotify_artist_id=row.spotify_artist_id,
                    spotify_genres=list(row.spotify_genres)[:5] if row.spotify_genres else None,
                )
                for row in results
            ]

            # Cache the results
            self._mbid_search_cache[cache_key] = (now, artist_results)

            # Clean old cache entries periodically
            if len(self._mbid_search_cache) > 1000:
                cutoff = now - self.CACHE_TTL
                self._mbid_search_cache = {k: v for k, v in self._mbid_search_cache.items() if v[0] > cutoff}

            return artist_results

        except Exception as e:
            # If MusicBrainz tables don't exist yet, fall back gracefully
            logger.warning(f"MBID search failed (tables may not exist yet): {e}")
            return []

    def get_artist_by_mbid(self, mbid: str) -> ArtistSearchResultMBID | None:
        """Get artist by MusicBrainz ID.

        Args:
            mbid: MusicBrainz artist UUID

        Returns:
            ArtistSearchResultMBID or None if not found
        """
        sql = f"""
            SELECT
                artist_mbid,
                artist_name,
                disambiguation,
                artist_type,
                spotify_artist_id,
                popularity,
                spotify_genres,
                mb_tags AS tags
            FROM `{self.PROJECT_ID}.{self.DATASET_ID}.mb_artists_normalized`
            WHERE artist_mbid = @mbid
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("mbid", "STRING", mbid),
            ]
        )

        try:
            results = list(self.client.query(sql, job_config=job_config).result())
            if not results:
                return None

            row = results[0]
            return ArtistSearchResultMBID(
                artist_mbid=row.artist_mbid,
                artist_name=row.artist_name,
                disambiguation=row.disambiguation,
                artist_type=row.artist_type,
                popularity=row.popularity or 50,
                tags=list(row.tags) if row.tags else [],
                spotify_artist_id=row.spotify_artist_id,
                spotify_genres=list(row.spotify_genres)[:5] if row.spotify_genres else None,
            )
        except Exception as e:
            logger.warning(f"Get artist by MBID failed: {e}")
            return None

    def lookup_mbid_by_spotify_id(self, spotify_artist_id: str) -> str | None:
        """Look up MBID for a Spotify artist ID.

        Args:
            spotify_artist_id: Spotify artist ID

        Returns:
            MusicBrainz artist UUID or None if not mapped
        """
        result = self.lookup_mbids_by_spotify_ids([spotify_artist_id])
        return result.get(spotify_artist_id)

    def lookup_mbids_by_spotify_ids(self, spotify_artist_ids: list[str]) -> dict[str, str]:
        """Look up MBIDs for multiple Spotify artist IDs in a single query.

        Args:
            spotify_artist_ids: List of Spotify artist IDs

        Returns:
            Dict mapping Spotify artist ID to MusicBrainz artist UUID
        """
        if not spotify_artist_ids:
            return {}

        # Deduplicate
        unique_ids = list(set(spotify_artist_ids))

        sql = f"""
            SELECT spotify_artist_id, artist_mbid
            FROM `{self.PROJECT_ID}.{self.DATASET_ID}.mbid_spotify_mapping`
            WHERE spotify_artist_id IN UNNEST(@spotify_ids)
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("spotify_ids", "STRING", unique_ids),
            ]
        )

        try:
            results = self.client.query(sql, job_config=job_config).result()
            return {row.spotify_artist_id: row.artist_mbid for row in results}
        except Exception as e:
            logger.warning(f"Bulk MBID lookup failed: {e}")
            return {}

    def lookup_mbids_by_names(self, artist_names: list[str]) -> dict[str, str]:
        """Look up MBIDs for multiple artist names.

        Uses normalized name matching against MusicBrainz catalog.

        Args:
            artist_names: List of artist names to look up

        Returns:
            Dict mapping normalized artist name to MBID
        """
        if not artist_names:
            return {}

        # Normalize names for lookup
        normalized_names = [_normalize_for_matching(name) for name in artist_names]
        normalized_names = [n for n in normalized_names if n]

        if not normalized_names:
            return {}

        sql = f"""
            SELECT
                name_normalized,
                artist_mbid
            FROM `{self.PROJECT_ID}.{self.DATASET_ID}.mb_artists`
            WHERE name_normalized IN UNNEST(@names)
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("names", "STRING", normalized_names),
            ]
        )

        try:
            results = self.client.query(sql, job_config=job_config).result()
            return {row.name_normalized: row.artist_mbid for row in results}
        except Exception as e:
            logger.warning(f"MBID name lookup failed: {e}")
            return {}

    # =========================================================================
    # Recording/Song Methods (MBID-First) - Phase 7
    # =========================================================================

    # Cache for recording search
    _recording_search_cache: dict[str, tuple[float, list["RecordingSearchResult"]]] = {}

    def search_recordings(
        self,
        query: str,
        limit: int = 10,
        min_popularity: int = 0,
    ) -> list[RecordingSearchResult]:
        """Search recordings by title with Spotify enrichment.

        Uses mb_recordings table with ISRC-based Spotify enrichment.

        Args:
            query: Search query (will be normalized)
            limit: Maximum results to return (default 10)
            min_popularity: Minimum Spotify popularity (0 for MB-only results)

        Returns:
            List of RecordingSearchResult sorted by popularity
        """
        if not query or len(query) < 2:
            return []

        normalized = _normalize_for_matching(query)
        if not normalized:
            return []

        # Check cache first
        cache_key = f"rec:{normalized}:{limit}:{min_popularity}"
        now = time.time()
        if cache_key in self._recording_search_cache:
            cached_time, cached_results = self._recording_search_cache[cache_key]
            if now - cached_time < self.CACHE_TTL:
                logger.debug(f"Recording search cache hit for '{normalized}'")
                return cached_results

        # Query recordings with ISRC-based Spotify enrichment
        sql = f"""
            SELECT
                r.recording_mbid,
                r.title,
                r.artist_credit,
                r.length_ms,
                r.disambiguation,
                st.spotify_id AS spotify_track_id,
                st.popularity AS spotify_popularity
            FROM `{self.PROJECT_ID}.{self.DATASET_ID}.mb_recordings` r
            LEFT JOIN `{self.PROJECT_ID}.{self.DATASET_ID}.mb_recording_isrc` ri
                ON r.recording_mbid = ri.recording_mbid
            LEFT JOIN `{self.PROJECT_ID}.{self.DATASET_ID}.spotify_tracks` st
                ON ri.isrc = st.isrc
            WHERE r.name_normalized LIKE @query_prefix
              AND (st.popularity >= @min_popularity OR st.popularity IS NULL)
            ORDER BY COALESCE(st.popularity, 0) DESC
            LIMIT @limit
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("query_prefix", "STRING", f"{normalized}%"),
                bigquery.ScalarQueryParameter("min_popularity", "INT64", min_popularity),
                bigquery.ScalarQueryParameter("limit", "INT64", limit),
            ]
        )

        try:
            results = self.client.query(sql, job_config=job_config).result()

            recording_results = [
                RecordingSearchResult(
                    recording_mbid=row.recording_mbid,
                    title=row.title,
                    artist_credit=row.artist_credit,
                    length_ms=row.length_ms,
                    disambiguation=row.disambiguation,
                    spotify_track_id=row.spotify_track_id,
                    spotify_popularity=row.spotify_popularity,
                )
                for row in results
            ]

            # Cache the results
            self._recording_search_cache[cache_key] = (now, recording_results)

            # Clean old cache entries periodically
            if len(self._recording_search_cache) > 1000:
                cutoff = now - self.CACHE_TTL
                self._recording_search_cache = {k: v for k, v in self._recording_search_cache.items() if v[0] > cutoff}

            return recording_results

        except Exception as e:
            logger.warning(f"Recording search failed (tables may not exist yet): {e}")
            return []

    def get_recording_by_mbid(self, mbid: str) -> RecordingSearchResult | None:
        """Get recording by MusicBrainz ID.

        Args:
            mbid: MusicBrainz recording UUID

        Returns:
            RecordingSearchResult or None if not found
        """
        sql = f"""
            SELECT
                r.recording_mbid,
                r.title,
                r.artist_credit,
                r.length_ms,
                r.disambiguation,
                st.spotify_id AS spotify_track_id,
                st.popularity AS spotify_popularity
            FROM `{self.PROJECT_ID}.{self.DATASET_ID}.mb_recordings` r
            LEFT JOIN `{self.PROJECT_ID}.{self.DATASET_ID}.mb_recording_isrc` ri
                ON r.recording_mbid = ri.recording_mbid
            LEFT JOIN `{self.PROJECT_ID}.{self.DATASET_ID}.spotify_tracks` st
                ON ri.isrc = st.isrc
            WHERE r.recording_mbid = @mbid
            ORDER BY st.popularity DESC NULLS LAST
            LIMIT 1
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("mbid", "STRING", mbid),
            ]
        )

        try:
            results = list(self.client.query(sql, job_config=job_config).result())
            if not results:
                return None

            row = results[0]
            return RecordingSearchResult(
                recording_mbid=row.recording_mbid,
                title=row.title,
                artist_credit=row.artist_credit,
                length_ms=row.length_ms,
                disambiguation=row.disambiguation,
                spotify_track_id=row.spotify_track_id,
                spotify_popularity=row.spotify_popularity,
            )
        except Exception as e:
            logger.warning(f"Get recording by MBID failed: {e}")
            return None

    def lookup_recording_by_isrc(self, isrc: str) -> RecordingSearchResult | None:
        """Look up recording by ISRC code.

        Args:
            isrc: International Standard Recording Code (12 chars)

        Returns:
            RecordingSearchResult or None if not found
        """
        result = self.lookup_recordings_by_isrcs([isrc])
        return result.get(isrc)

    def lookup_recordings_by_isrcs(
        self,
        isrcs: list[str],
    ) -> dict[str, RecordingSearchResult]:
        """Batch lookup recordings by ISRC codes.

        Args:
            isrcs: List of ISRC codes

        Returns:
            Dict mapping ISRC to RecordingSearchResult
        """
        if not isrcs:
            return {}

        # Deduplicate and normalize ISRCs
        unique_isrcs = list(set(isrc.strip().upper() for isrc in isrcs if isrc))
        if not unique_isrcs:
            return {}

        sql = f"""
            SELECT
                ri.isrc,
                r.recording_mbid,
                r.title,
                r.artist_credit,
                r.length_ms,
                r.disambiguation,
                st.spotify_id AS spotify_track_id,
                st.popularity AS spotify_popularity
            FROM `{self.PROJECT_ID}.{self.DATASET_ID}.mb_recording_isrc` ri
            JOIN `{self.PROJECT_ID}.{self.DATASET_ID}.mb_recordings` r
                ON ri.recording_mbid = r.recording_mbid
            LEFT JOIN `{self.PROJECT_ID}.{self.DATASET_ID}.spotify_tracks` st
                ON ri.isrc = st.isrc
            WHERE ri.isrc IN UNNEST(@isrcs)
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("isrcs", "STRING", unique_isrcs),
            ]
        )

        try:
            results = self.client.query(sql, job_config=job_config).result()
            return {
                row.isrc: RecordingSearchResult(
                    recording_mbid=row.recording_mbid,
                    title=row.title,
                    artist_credit=row.artist_credit,
                    length_ms=row.length_ms,
                    disambiguation=row.disambiguation,
                    spotify_track_id=row.spotify_track_id,
                    spotify_popularity=row.spotify_popularity,
                )
                for row in results
            }
        except Exception as e:
            logger.warning(f"Batch ISRC lookup failed: {e}")
            return {}

    def lookup_recording_mbid_by_spotify_track_id(
        self,
        spotify_track_id: str,
    ) -> str | None:
        """Look up recording MBID for a Spotify track via ISRC.

        Goes: Spotify track → ISRC → MB recording MBID

        Args:
            spotify_track_id: Spotify track ID

        Returns:
            MusicBrainz recording UUID or None if not mapped
        """
        result = self.lookup_recording_mbids_by_spotify_track_ids([spotify_track_id])
        return result.get(spotify_track_id)

    def lookup_recording_mbids_by_spotify_track_ids(
        self,
        spotify_track_ids: list[str],
    ) -> dict[str, str]:
        """Batch lookup recording MBIDs for Spotify tracks via ISRC.

        Args:
            spotify_track_ids: List of Spotify track IDs

        Returns:
            Dict mapping Spotify track ID to MusicBrainz recording UUID
        """
        if not spotify_track_ids:
            return {}

        # Deduplicate
        unique_ids = list(set(spotify_track_ids))

        sql = f"""
            SELECT
                st.spotify_id AS spotify_track_id,
                ri.recording_mbid
            FROM `{self.PROJECT_ID}.{self.DATASET_ID}.spotify_tracks` st
            JOIN `{self.PROJECT_ID}.{self.DATASET_ID}.mb_recording_isrc` ri
                ON st.isrc = ri.isrc
            WHERE st.spotify_id IN UNNEST(@spotify_ids)
              AND st.isrc IS NOT NULL
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("spotify_ids", "STRING", unique_ids),
            ]
        )

        try:
            results = self.client.query(sql, job_config=job_config).result()
            return {row.spotify_track_id: row.recording_mbid for row in results}
        except Exception as e:
            logger.warning(f"Spotify to MBID lookup failed: {e}")
            return {}

    def get_karaoke_recording_links(
        self,
        karaoke_ids: list[int],
    ) -> dict[int, KaraokeRecordingLink]:
        """Get recording links for karaoke songs.

        Looks up the pre-computed karaoke_recording_links table.

        Args:
            karaoke_ids: List of karaoke song IDs from karaokenerds_raw

        Returns:
            Dict mapping karaoke_id to KaraokeRecordingLink
        """
        if not karaoke_ids:
            return {}

        sql = f"""
            SELECT
                karaoke_id,
                recording_mbid,
                spotify_track_id,
                match_method,
                match_confidence
            FROM `{self.PROJECT_ID}.{self.DATASET_ID}.karaoke_recording_links`
            WHERE karaoke_id IN UNNEST(@karaoke_ids)
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("karaoke_ids", "INT64", karaoke_ids),
            ]
        )

        try:
            results = self.client.query(sql, job_config=job_config).result()
            return {
                row.karaoke_id: KaraokeRecordingLink(
                    karaoke_id=row.karaoke_id,
                    recording_mbid=row.recording_mbid,
                    spotify_track_id=row.spotify_track_id,
                    match_method=row.match_method,
                    match_confidence=row.match_confidence,
                )
                for row in results
            }
        except Exception as e:
            logger.warning(f"Karaoke recording links lookup failed: {e}")
            return {}
