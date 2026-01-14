"""ListenBrainz Similar Artists Service.

Provides similar artist recommendations using the ListenBrainz Labs API,
which has pre-computed artist similarity based on listening patterns.
"""

import asyncio
from datetime import UTC, datetime

import httpx

from backend.config import BackendSettings
from backend.services.firestore_service import FirestoreService


class ListenBrainzService:
    """Service for fetching similar artists from ListenBrainz.

    Uses the ListenBrainz Labs API which provides pre-computed artist
    similarity based on session-based listening patterns.
    """

    LISTENBRAINZ_LABS_URL = "https://labs.api.listenbrainz.org"
    MUSICBRAINZ_API_URL = "https://musicbrainz.org/ws/2"

    # Best algorithm based on ListenBrainz documentation
    DEFAULT_ALGORITHM = "session_based_days_7500_session_300_contribution_5_threshold_10_limit_100_filter_True_skip_30"

    # Cache configuration
    CACHE_COLLECTION = "listenbrainz_cache"
    CACHE_TTL_HOURS = 168  # 7 days - similarity data doesn't change often

    def __init__(
        self,
        settings: BackendSettings,
        firestore: FirestoreService | None = None,
    ):
        """Initialize the ListenBrainz service.

        Args:
            settings: Backend settings.
            firestore: Optional Firestore service for caching.
        """
        self.settings = settings
        self.firestore = firestore
        self._http_client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=30.0,
                headers={"User-Agent": "KaraokeDecide/1.0 (contact@nomadkaraoke.com)"},
            )
        return self._http_client

    async def close(self) -> None:
        """Close HTTP client."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    async def search_artist_mbid(self, artist_name: str) -> dict | None:
        """Search MusicBrainz for an artist by name.

        Args:
            artist_name: Artist name to search for.

        Returns:
            Dict with mbid, name, type, score, disambiguation or None if not found.
        """
        client = await self._get_client()

        try:
            response = await client.get(
                f"{self.MUSICBRAINZ_API_URL}/artist",
                params={"query": artist_name, "fmt": "json", "limit": 5},
            )

            if response.status_code == 200:
                data = response.json()
                artists = data.get("artists", [])
                if artists:
                    artist = artists[0]
                    return {
                        "mbid": artist.get("id"),
                        "name": artist.get("name"),
                        "type": artist.get("type"),
                        "score": artist.get("score"),
                        "disambiguation": artist.get("disambiguation"),
                    }
        except Exception:
            pass

        return None

    async def get_similar_artists(
        self,
        artist_mbid: str,
        algorithm: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Get similar artists from ListenBrainz Labs API.

        Args:
            artist_mbid: MusicBrainz artist ID.
            algorithm: Similarity algorithm (default: session-based).
            limit: Maximum results to return.

        Returns:
            List of similar artists with name, mbid, and score.
        """
        if algorithm is None:
            algorithm = self.DEFAULT_ALGORITHM

        client = await self._get_client()

        try:
            response = await client.get(
                f"{self.LISTENBRAINZ_LABS_URL}/similar-artists/json",
                params={
                    "artist_mbids": artist_mbid,
                    "algorithm": algorithm,
                },
            )

            if response.status_code == 200:
                results = response.json()
                # Filter out the seed artist and limit results
                similar = [r for r in results if r.get("artist_mbid") != artist_mbid][:limit]
                return similar

        except Exception:
            pass

        return []

    async def get_similar_artists_by_name(
        self,
        artist_name: str,
        limit: int = 20,
    ) -> list[dict]:
        """Get similar artists by artist name.

        Resolves artist name to MBID first, then fetches similar artists.

        Args:
            artist_name: Artist name to find similar artists for.
            limit: Maximum results to return.

        Returns:
            List of similar artists with name and score.
        """
        # Check cache first
        cache_key = f"similar:{artist_name.lower()}"
        cached = await self._get_from_cache(cache_key)
        if cached is not None:
            return cached[:limit]

        # Search for artist MBID
        artist_info = await self.search_artist_mbid(artist_name)
        if not artist_info or not artist_info.get("mbid"):
            return []

        # Get similar artists
        similar = await self.get_similar_artists(artist_info["mbid"], limit=limit * 2)

        # Extract just name and score
        result = [
            {
                "name": s.get("name", "Unknown"),
                "score": s.get("score", 0),
                "mbid": s.get("artist_mbid"),
            }
            for s in similar
        ][:limit]

        # Cache the result
        await self._set_cache(cache_key, result)

        return result

    async def get_similar_for_multiple_artists(
        self,
        artist_names: list[str],
        limit_per_artist: int = 10,
    ) -> dict[str, list[dict]]:
        """Get similar artists for multiple seed artists.

        Args:
            artist_names: List of artist names.
            limit_per_artist: Max similar artists per seed artist.

        Returns:
            Dict mapping artist name to list of similar artists.
        """
        results: dict[str, list[dict]] = {}

        # Process in parallel with rate limiting
        async def fetch_one(name: str) -> tuple[str, list[dict]]:
            similar = await self.get_similar_artists_by_name(name, limit=limit_per_artist)
            return (name, similar)

        # Batch requests to respect rate limits
        for name in artist_names:
            artist_name, similar = await fetch_one(name)
            results[artist_name] = similar
            # Small delay to avoid rate limiting
            await asyncio.sleep(0.1)

        return results

    async def find_similar_artist_matches(
        self,
        seed_artists: list[str],
        candidate_names: list[str],
        min_score: float = 0.3,
    ) -> dict[str, list[str]]:
        """Find which candidates are similar to which seed artists.

        This is the main integration point for quiz recommendations.
        Returns a mapping of candidate -> seed artists it's similar to.

        Args:
            seed_artists: User's entered artists (seed for similarity).
            candidate_names: Candidate artists to check.
            min_score: Minimum similarity score (0-1).

        Returns:
            Dict mapping candidate name to list of seed artists it's similar to.
        """
        # Get similar artists for all seeds
        all_similar = await self.get_similar_for_multiple_artists(seed_artists, limit_per_artist=50)

        # Build reverse mapping: similar_artist_name -> seed_artists
        similar_to_seeds: dict[str, list[str]] = {}
        for seed_name, similar_list in all_similar.items():
            for s in similar_list:
                similar_name = s.get("name", "").lower()
                score = s.get("score", 0)
                if score >= min_score:
                    if similar_name not in similar_to_seeds:
                        similar_to_seeds[similar_name] = []
                    similar_to_seeds[similar_name].append(seed_name)

        # Check which candidates match
        matches: dict[str, list[str]] = {}
        for candidate in candidate_names:
            candidate_lower = candidate.lower()
            if candidate_lower in similar_to_seeds:
                matches[candidate] = similar_to_seeds[candidate_lower]

        return matches

    async def _get_from_cache(self, key: str) -> list[dict] | None:
        """Get cached similar artists.

        Args:
            key: Cache key.

        Returns:
            Cached data or None if not found/expired.
        """
        if not self.firestore:
            return None

        try:
            doc = await self.firestore.get_document(self.CACHE_COLLECTION, key)
            if doc:
                # Check TTL
                cached_at = doc.get("cached_at")
                if cached_at:
                    cached_dt = datetime.fromisoformat(cached_at)
                    age_hours = (datetime.now(UTC) - cached_dt).total_seconds() / 3600
                    if age_hours < self.CACHE_TTL_HOURS:
                        return list(doc.get("similar_artists", []))
        except Exception:
            pass

        return None

    async def _set_cache(self, key: str, similar_artists: list[dict]) -> None:
        """Cache similar artists.

        Args:
            key: Cache key.
            similar_artists: Data to cache.
        """
        if not self.firestore:
            return

        try:
            await self.firestore.set_document(
                self.CACHE_COLLECTION,
                key,
                {
                    "similar_artists": similar_artists,
                    "cached_at": datetime.now(UTC).isoformat(),
                },
            )
        except Exception:
            pass


# Lazy initialization
_listenbrainz_service: ListenBrainzService | None = None


def get_listenbrainz_service(
    settings: BackendSettings | None = None,
    firestore: FirestoreService | None = None,
) -> ListenBrainzService:
    """Get the ListenBrainz service instance.

    Args:
        settings: Optional settings override.
        firestore: Optional Firestore service override.

    Returns:
        ListenBrainzService instance.
    """
    global _listenbrainz_service

    if _listenbrainz_service is None or settings is not None or firestore is not None:
        if settings is None:
            from backend.config import get_backend_settings

            settings = get_backend_settings()
        if firestore is None:
            firestore = FirestoreService(settings)

        _listenbrainz_service = ListenBrainzService(settings, firestore)

    return _listenbrainz_service
