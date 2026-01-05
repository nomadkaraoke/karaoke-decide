"""BigQuery-based song catalog service."""

import logging
import re
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
class TrackSearchResult:
    """Track search result for autocomplete."""

    track_id: str
    track_name: str
    artist_name: str
    artist_id: str
    popularity: int
    duration_ms: int
    explicit: bool


class BigQueryCatalogService:
    """Service for querying song catalog from BigQuery."""

    PROJECT_ID = "nomadkaraoke"
    DATASET_ID = "karaoke_decide"

    def __init__(self, client: bigquery.Client | None = None):
        self.client = client or bigquery.Client(project=self.PROJECT_ID)

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

    def search_artists(
        self,
        query: str,
        limit: int = 10,
    ) -> list[ArtistSearchResult]:
        """Search artists by name prefix for autocomplete.

        Uses the pre-computed normalized table for fast prefix matching.
        Results are sorted by popularity (highest first).

        Args:
            query: Search query (will be normalized)
            limit: Maximum results to return (default 10)

        Returns:
            List of ArtistSearchResult sorted by popularity
        """
        if not query or len(query) < 2:
            return []

        normalized = _normalize_for_matching(query)
        if not normalized:
            return []

        # Use prefix matching on normalized name
        sql = f"""
            SELECT
                artist_id,
                artist_name,
                popularity,
                genres
            FROM `{self.PROJECT_ID}.{self.DATASET_ID}.spotify_artists_normalized`
            WHERE normalized_name LIKE @query_prefix
            ORDER BY popularity DESC
            LIMIT @limit
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("query_prefix", "STRING", f"{normalized}%"),
                bigquery.ScalarQueryParameter("limit", "INT64", limit),
            ]
        )

        results = self.client.query(sql, job_config=job_config).result()

        return [
            ArtistSearchResult(
                artist_id=row.artist_id,
                artist_name=row.artist_name,
                popularity=row.popularity or 0,
                genres=list(row.genres)[:5] if row.genres else [],
            )
            for row in results
        ]

    def search_tracks(
        self,
        query: str,
        limit: int = 10,
    ) -> list[TrackSearchResult]:
        """Search tracks by title or artist for autocomplete.

        Uses the pre-computed spotify_tracks_normalized table for fast
        prefix matching. Searches both track title and artist name.
        Results are sorted by popularity (highest first).

        Args:
            query: Search query (will be normalized)
            limit: Maximum results to return (default 10)

        Returns:
            List of TrackSearchResult sorted by popularity
        """
        if not query or len(query) < 2:
            return []

        normalized = _normalize_for_matching(query)
        if not normalized:
            return []

        # Search both title and artist, deduplicate by track_id
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
            WHERE normalized_title LIKE @query_prefix
               OR normalized_artist LIKE @query_prefix
            ORDER BY popularity DESC
            LIMIT @limit
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("query_prefix", "STRING", f"{normalized}%"),
                bigquery.ScalarQueryParameter("limit", "INT64", limit),
            ]
        )

        results = self.client.query(sql, job_config=job_config).result()

        return [
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
