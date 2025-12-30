"""BigQuery-based song catalog service."""

from dataclasses import dataclass

from google.cloud import bigquery


@dataclass
class SongResult:
    """Song from the catalog."""
    id: int
    artist: str
    title: str
    brands: str
    brand_count: int  # Derived from brands


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
        return result.count

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
