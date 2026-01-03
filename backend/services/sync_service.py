"""Service for syncing listening history from music services.

Fetches tracks from Spotify and Last.fm, matches them against the
karaoke catalog, and creates UserSong records.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from backend.config import BackendSettings
from backend.services.firestore_service import FirestoreService
from backend.services.music_service_service import MusicServiceError, MusicServiceService
from backend.services.track_matcher import MatchedTrack, TrackMatcher
from karaoke_decide.core.exceptions import ExternalServiceError
from karaoke_decide.core.models import MusicService
from karaoke_decide.services.lastfm import LastFmClient
from karaoke_decide.services.spotify import SpotifyClient

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """Result of a sync operation for a single service."""

    service_type: str
    tracks_fetched: int
    tracks_matched: int
    user_songs_created: int
    user_songs_updated: int
    artists_stored: int = 0
    error: str | None = None


# Type for progress callback
ProgressCallback = Any  # Callable that accepts progress params


class SyncService:
    """Service for syncing listening history from connected music services.

    Handles:
    - Fetching tracks from Spotify and Last.fm
    - Matching tracks to the karaoke catalog
    - Creating/updating UserSong records
    """

    USER_SONGS_COLLECTION = "user_songs"

    # Limits for API fetching
    SPOTIFY_SAVED_TRACKS_LIMIT = 500
    SPOTIFY_TOP_TRACKS_LIMIT = 150  # 50 per time range × 3
    SPOTIFY_RECENT_TRACKS_LIMIT = 50
    # Last.fm has much richer data - fetch more!
    LASTFM_TOP_TRACKS_LIMIT = 1000  # Top 1000 tracks with play counts
    LASTFM_TOP_ARTISTS_LIMIT = 1000  # Top 1000 artists with play counts
    LASTFM_LOVED_TRACKS_LIMIT = 500  # Loved tracks (additional to top)
    # Full scrobble history - limit to avoid timeout for large libraries
    LASTFM_FULL_SCROBBLE_HISTORY = True  # Enable fetching scrobbles beyond top tracks
    LASTFM_MAX_SCROBBLES = 50000  # Cap scrobbles to avoid timeout (5min Cloud Run limit)

    def __init__(
        self,
        settings: BackendSettings,
        firestore: FirestoreService,
        music_service_service: MusicServiceService,
        spotify_client: SpotifyClient | None = None,
        lastfm_client: LastFmClient | None = None,
        track_matcher: TrackMatcher | None = None,
    ):
        """Initialize the sync service.

        Args:
            settings: Backend settings.
            firestore: Firestore service.
            music_service_service: Music service management service.
            spotify_client: Optional Spotify client (created lazily).
            lastfm_client: Optional Last.fm client (created lazily).
            track_matcher: Optional track matcher (created lazily).
        """
        self.settings = settings
        self.firestore = firestore
        self.music_service_service = music_service_service
        self._spotify_client = spotify_client
        self._lastfm_client = lastfm_client
        self._track_matcher = track_matcher

    @property
    def spotify(self) -> SpotifyClient:
        """Get or create Spotify client."""
        if self._spotify_client is None:
            self._spotify_client = SpotifyClient(self.settings)
        return self._spotify_client

    @property
    def lastfm(self) -> LastFmClient:
        """Get or create Last.fm client."""
        if self._lastfm_client is None:
            self._lastfm_client = LastFmClient(self.settings)
        return self._lastfm_client

    @property
    def track_matcher(self) -> TrackMatcher:
        """Get or create track matcher."""
        if self._track_matcher is None:
            from backend.services.track_matcher import get_track_matcher

            self._track_matcher = get_track_matcher()
        return self._track_matcher

    async def sync_all_services(self, user_id: str) -> list[SyncResult]:
        """Sync all connected services for a user.

        Args:
            user_id: User ID to sync.

        Returns:
            List of SyncResult for each service.
        """
        services = await self.music_service_service.get_user_services(user_id)
        results: list[SyncResult] = []

        for service in services:
            if service.service_type == "spotify":
                result = await self.sync_spotify(user_id, service)
            elif service.service_type == "lastfm":
                result = await self.sync_lastfm(user_id, service)
            else:
                result = SyncResult(
                    service_type=service.service_type,
                    tracks_fetched=0,
                    tracks_matched=0,
                    user_songs_created=0,
                    user_songs_updated=0,
                    error=f"Unknown service type: {service.service_type}",
                )
            results.append(result)

        return results

    async def sync_all_services_with_progress(
        self,
        user_id: str,
        progress_callback: ProgressCallback | None = None,
    ) -> list[SyncResult]:
        """Sync all connected services with progress updates.

        This is the async version called by Cloud Tasks that reports
        progress and fetches top artists for recommendations.

        Args:
            user_id: User ID to sync.
            progress_callback: Async callback for progress updates.

        Returns:
            List of SyncResult for each service.
        """
        services = await self.music_service_service.get_user_services(user_id)
        logger.info(f"Found {len(services)} connected services for user {user_id}")
        for svc in services:
            logger.info(f"  - {svc.service_type}: username={svc.service_username}")
        results: list[SyncResult] = []

        for service in services:
            if service.service_type == "spotify":
                result = await self._sync_spotify_with_progress(user_id, service, progress_callback)
            elif service.service_type == "lastfm":
                result = await self._sync_lastfm_with_progress(user_id, service, progress_callback)
            else:
                result = SyncResult(
                    service_type=service.service_type,
                    tracks_fetched=0,
                    tracks_matched=0,
                    user_songs_created=0,
                    user_songs_updated=0,
                    error=f"Unknown service type: {service.service_type}",
                )
            results.append(result)

        return results

    async def _sync_spotify_with_progress(
        self,
        user_id: str,
        service: MusicService,
        progress_callback: ProgressCallback | None,
    ) -> SyncResult:
        """Sync Spotify with progress updates and artist fetching."""
        try:
            await self.music_service_service.update_sync_status(user_id, "spotify", "syncing")

            if progress_callback:
                await progress_callback(
                    current_service="spotify",
                    current_phase="fetching",
                    total_tracks=0,
                    processed_tracks=0,
                    matched_tracks=0,
                )

            try:
                access_token = await self.music_service_service.get_valid_spotify_token(service)
            except MusicServiceError as e:
                await self.music_service_service.update_sync_status(user_id, "spotify", "error", error=str(e))
                return SyncResult(
                    service_type="spotify",
                    tracks_fetched=0,
                    tracks_matched=0,
                    user_songs_created=0,
                    user_songs_updated=0,
                    error=str(e),
                )

            # Fetch tracks
            tracks = await self._fetch_spotify_tracks(access_token)

            if progress_callback:
                await progress_callback(
                    current_service="spotify",
                    current_phase="matching",
                    total_tracks=len(tracks),
                    processed_tracks=0,
                    matched_tracks=0,
                )

            # Match to catalog (batch)
            matched = await self.track_matcher.batch_match_tracks(tracks)
            tracks_matched = sum(1 for m in matched if m.catalog_song is not None)

            if progress_callback:
                await progress_callback(
                    current_service="spotify",
                    current_phase="storing",
                    total_tracks=len(tracks),
                    processed_tracks=len(tracks),
                    matched_tracks=tracks_matched,
                )

            # Create/update UserSongs
            created, updated = await self._upsert_user_songs(user_id, matched, "spotify")

            # Fetch and store top artists
            artists_stored = await self._fetch_and_store_spotify_artists(user_id, access_token)

            await self.music_service_service.update_sync_status(
                user_id, "spotify", "idle", tracks_synced=tracks_matched
            )

            return SyncResult(
                service_type="spotify",
                tracks_fetched=len(tracks),
                tracks_matched=tracks_matched,
                user_songs_created=created,
                user_songs_updated=updated,
                artists_stored=artists_stored,
            )

        except ExternalServiceError as e:
            error_msg = f"Spotify API error: {e}"
            await self.music_service_service.update_sync_status(user_id, "spotify", "error", error=error_msg)
            return SyncResult(
                service_type="spotify",
                tracks_fetched=0,
                tracks_matched=0,
                user_songs_created=0,
                user_songs_updated=0,
                error=error_msg,
            )
        except Exception as e:
            error_msg = f"Unexpected error: {e}"
            await self.music_service_service.update_sync_status(user_id, "spotify", "error", error=error_msg)
            return SyncResult(
                service_type="spotify",
                tracks_fetched=0,
                tracks_matched=0,
                user_songs_created=0,
                user_songs_updated=0,
                error=error_msg,
            )

    async def _sync_lastfm_with_progress(
        self,
        user_id: str,
        service: MusicService,
        progress_callback: ProgressCallback | None,
    ) -> SyncResult:
        """Sync Last.fm with progress updates and artist fetching."""
        try:
            await self.music_service_service.update_sync_status(user_id, "lastfm", "syncing")

            if progress_callback:
                await progress_callback(
                    current_service="lastfm",
                    current_phase="fetching",
                    total_tracks=0,
                    processed_tracks=0,
                    matched_tracks=0,
                )

            username = service.service_username
            if not username:
                raise MusicServiceError("No Last.fm username configured")

            # Fetch tracks
            tracks = await self._fetch_lastfm_tracks(username)

            if progress_callback:
                await progress_callback(
                    current_service="lastfm",
                    current_phase="matching",
                    total_tracks=len(tracks),
                    processed_tracks=0,
                    matched_tracks=0,
                )

            # Match to catalog (batch)
            matched = await self.track_matcher.batch_match_tracks(tracks)
            tracks_matched = sum(1 for m in matched if m.catalog_song is not None)

            if progress_callback:
                await progress_callback(
                    current_service="lastfm",
                    current_phase="storing",
                    total_tracks=len(tracks),
                    processed_tracks=len(tracks),
                    matched_tracks=tracks_matched,
                )

            # Create/update UserSongs
            created, updated = await self._upsert_user_songs(user_id, matched, "lastfm")

            # Fetch and store top artists
            artists_stored = await self._fetch_and_store_lastfm_artists(user_id, username)

            await self.music_service_service.update_sync_status(user_id, "lastfm", "idle", tracks_synced=tracks_matched)

            return SyncResult(
                service_type="lastfm",
                tracks_fetched=len(tracks),
                tracks_matched=tracks_matched,
                user_songs_created=created,
                user_songs_updated=updated,
                artists_stored=artists_stored,
            )

        except MusicServiceError as e:
            error_msg = str(e)
            await self.music_service_service.update_sync_status(user_id, "lastfm", "error", error=error_msg)
            return SyncResult(
                service_type="lastfm",
                tracks_fetched=0,
                tracks_matched=0,
                user_songs_created=0,
                user_songs_updated=0,
                error=error_msg,
            )
        except ExternalServiceError as e:
            error_msg = f"Last.fm API error: {e}"
            await self.music_service_service.update_sync_status(user_id, "lastfm", "error", error=error_msg)
            return SyncResult(
                service_type="lastfm",
                tracks_fetched=0,
                tracks_matched=0,
                user_songs_created=0,
                user_songs_updated=0,
                error=error_msg,
            )
        except Exception as e:
            error_msg = f"Unexpected error: {e}"
            await self.music_service_service.update_sync_status(user_id, "lastfm", "error", error=error_msg)
            return SyncResult(
                service_type="lastfm",
                tracks_fetched=0,
                tracks_matched=0,
                user_songs_created=0,
                user_songs_updated=0,
                error=error_msg,
            )

    async def _fetch_and_store_spotify_artists(self, user_id: str, access_token: str) -> int:
        """Fetch and store user's top artists from Spotify.

        Args:
            user_id: User ID.
            access_token: Spotify access token.

        Returns:
            Number of artists stored.
        """
        stored = 0
        now = datetime.now(UTC)

        # Fetch top artists for different time ranges
        for time_range in ["short_term", "medium_term", "long_term"]:
            try:
                response = await self.spotify.get_top_artists(access_token, time_range=time_range, limit=50)
                artists = response.get("items", [])

                for rank, artist in enumerate(artists, 1):
                    artist_id = f"{user_id}:spotify:{artist.get('id', '')}"
                    genres = artist.get("genres", [])

                    artist_data = {
                        "id": artist_id,
                        "user_id": user_id,
                        "source": "spotify",
                        "artist_name": artist.get("name", ""),
                        "artist_id": artist.get("id", ""),
                        "rank": rank,
                        "time_range": time_range,
                        "popularity": artist.get("popularity", 0),
                        "genres": genres[:5] if genres else [],  # Store up to 5 genres
                        "updated_at": now.isoformat(),
                    }

                    await self.firestore.set_document("user_artists", artist_id, artist_data)
                    stored += 1

            except Exception:
                # Continue with other time ranges if one fails
                continue

        return stored

    async def _fetch_and_store_lastfm_artists(self, user_id: str, username: str) -> int:
        """Fetch and store user's top artists from Last.fm with full pagination.

        Fetches up to 1000 artists ranked by play count, providing comprehensive
        data about which artists the user knows best.

        Args:
            user_id: User ID.
            username: Last.fm username.

        Returns:
            Number of artists stored.
        """
        stored = 0
        now = datetime.now(UTC)

        try:
            # Fetch top artists with pagination - up to 1000 artists!
            artists = await self.lastfm.get_all_top_artists(
                username=username,
                period="overall",
                max_artists=self.LASTFM_TOP_ARTISTS_LIMIT,
            )
            logger.info(f"Last.fm artists: fetched {len(artists)} artists with play counts")

            for artist in artists:
                artist_name = artist.get("name", "")
                if not artist_name:
                    continue

                # Create unique ID using normalized name
                safe_name = artist_name.lower().replace(" ", "_")[:50]
                artist_id = f"{user_id}:lastfm:{safe_name}"

                # Get playcount (this is the real listen count!)
                playcount = 0
                if "playcount" in artist:
                    try:
                        playcount = int(artist["playcount"])
                    except (ValueError, TypeError):
                        pass

                artist_data = {
                    "id": artist_id,
                    "user_id": user_id,
                    "source": "lastfm",
                    "artist_name": artist_name,
                    "rank": artist.get("rank", stored + 1),
                    "period": "overall",
                    "playcount": playcount,
                    "updated_at": now.isoformat(),
                }

                await self.firestore.set_document("user_artists", artist_id, artist_data)
                stored += 1

        except Exception as e:
            logger.error(f"Error fetching Last.fm artists: {e}")

        logger.info(f"Last.fm artists: stored {stored} artists")
        return stored

    async def sync_spotify(self, user_id: str, service: MusicService) -> SyncResult:
        """Sync Spotify listening history.

        Fetches saved tracks, top tracks, and recently played.

        Args:
            user_id: User ID.
            service: Spotify MusicService record.

        Returns:
            SyncResult with counts.
        """
        try:
            # Mark as syncing
            await self.music_service_service.update_sync_status(user_id, "spotify", "syncing")

            # Ensure we have a valid token
            try:
                access_token = await self.music_service_service.get_valid_spotify_token(service)
            except MusicServiceError as e:
                await self.music_service_service.update_sync_status(user_id, "spotify", "error", error=str(e))
                return SyncResult(
                    service_type="spotify",
                    tracks_fetched=0,
                    tracks_matched=0,
                    user_songs_created=0,
                    user_songs_updated=0,
                    error=str(e),
                )

            # Fetch tracks from different sources
            tracks = await self._fetch_spotify_tracks(access_token)

            # Match to catalog
            matched = await self.track_matcher.batch_match_tracks(tracks)

            # Create/update UserSongs
            created, updated = await self._upsert_user_songs(user_id, matched, "spotify")

            # Calculate stats
            tracks_matched = sum(1 for m in matched if m.catalog_song is not None)

            # Mark as complete
            await self.music_service_service.update_sync_status(
                user_id, "spotify", "idle", tracks_synced=tracks_matched
            )

            return SyncResult(
                service_type="spotify",
                tracks_fetched=len(tracks),
                tracks_matched=tracks_matched,
                user_songs_created=created,
                user_songs_updated=updated,
            )

        except ExternalServiceError as e:
            error_msg = f"Spotify API error: {e}"
            await self.music_service_service.update_sync_status(user_id, "spotify", "error", error=error_msg)
            return SyncResult(
                service_type="spotify",
                tracks_fetched=0,
                tracks_matched=0,
                user_songs_created=0,
                user_songs_updated=0,
                error=error_msg,
            )
        except Exception as e:
            error_msg = f"Unexpected error: {e}"
            await self.music_service_service.update_sync_status(user_id, "spotify", "error", error=error_msg)
            return SyncResult(
                service_type="spotify",
                tracks_fetched=0,
                tracks_matched=0,
                user_songs_created=0,
                user_songs_updated=0,
                error=error_msg,
            )

    async def sync_lastfm(self, user_id: str, service: MusicService) -> SyncResult:
        """Sync Last.fm listening history.

        Fetches top tracks, loved tracks, and recent tracks.

        Args:
            user_id: User ID.
            service: Last.fm MusicService record.

        Returns:
            SyncResult with counts.
        """
        try:
            # Mark as syncing
            await self.music_service_service.update_sync_status(user_id, "lastfm", "syncing")

            username = service.service_username
            if not username:
                raise MusicServiceError("No Last.fm username configured")

            # Fetch tracks
            tracks = await self._fetch_lastfm_tracks(username)

            # Match to catalog
            matched = await self.track_matcher.batch_match_tracks(tracks)

            # Create/update UserSongs
            created, updated = await self._upsert_user_songs(user_id, matched, "lastfm")

            # Calculate stats
            tracks_matched = sum(1 for m in matched if m.catalog_song is not None)

            # Mark as complete
            await self.music_service_service.update_sync_status(user_id, "lastfm", "idle", tracks_synced=tracks_matched)

            return SyncResult(
                service_type="lastfm",
                tracks_fetched=len(tracks),
                tracks_matched=tracks_matched,
                user_songs_created=created,
                user_songs_updated=updated,
            )

        except ExternalServiceError as e:
            error_msg = f"Last.fm API error: {e}"
            await self.music_service_service.update_sync_status(user_id, "lastfm", "error", error=error_msg)
            return SyncResult(
                service_type="lastfm",
                tracks_fetched=0,
                tracks_matched=0,
                user_songs_created=0,
                user_songs_updated=0,
                error=error_msg,
            )
        except Exception as e:
            error_msg = f"Unexpected error: {e}"
            await self.music_service_service.update_sync_status(user_id, "lastfm", "error", error=error_msg)
            return SyncResult(
                service_type="lastfm",
                tracks_fetched=0,
                tracks_matched=0,
                user_songs_created=0,
                user_songs_updated=0,
                error=error_msg,
            )

    async def _fetch_spotify_tracks(self, access_token: str) -> list[dict[str, str]]:
        """Fetch tracks from Spotify API.

        Combines saved tracks, top tracks, and recently played.

        Args:
            access_token: Valid Spotify access token.

        Returns:
            List of dicts with 'artist' and 'title' keys.
        """
        tracks: list[dict[str, str]] = []
        seen: set[str] = set()

        logger.info("Starting Spotify track fetch")

        # Fetch saved tracks (library)
        offset = 0
        saved_count = 0
        while offset < self.SPOTIFY_SAVED_TRACKS_LIMIT:
            response = await self.spotify.get_saved_tracks(access_token, limit=50, offset=offset)
            items = response.get("items", [])
            logger.info(f"Spotify saved tracks: offset={offset}, items returned={len(items)}")
            if not items:
                break

            for item in items:
                track = item.get("track", {})
                track_info = self._extract_spotify_track_info(track)
                if track_info:
                    key = f"{track_info['artist']}:{track_info['title']}".lower()
                    if key not in seen:
                        seen.add(key)
                        tracks.append(track_info)
                        saved_count += 1

            offset += len(items)
            if len(items) < 50:
                break

        logger.info(f"Spotify saved tracks total: {saved_count} unique tracks")

        # Fetch top tracks for all time ranges with rank preservation
        for time_range in ["short_term", "medium_term", "long_term"]:
            top_count = 0
            response = await self.spotify.get_top_tracks(access_token, time_range=time_range, limit=50)
            items = response.get("items", [])
            logger.info(f"Spotify top tracks ({time_range}): {len(items)} items")

            for rank, track in enumerate(items, 1):
                track_info = self._extract_spotify_track_info(track, rank=rank, time_range=time_range)
                if track_info:
                    key = f"{track_info['artist']}:{track_info['title']}".lower()
                    if key not in seen:
                        seen.add(key)
                        tracks.append(track_info)
                        top_count += 1

            logger.info(f"Spotify top tracks ({time_range}): {top_count} new unique tracks")

        # Fetch recently played
        recent_count = 0
        response = await self.spotify.get_recently_played(access_token, limit=50)
        logger.info(f"Spotify recently played: {len(response.get('items', []))} items")
        for item in response.get("items", []):
            track = item.get("track", {})
            track_info = self._extract_spotify_track_info(track)
            if track_info:
                key = f"{track_info['artist']}:{track_info['title']}".lower()
                if key not in seen:
                    seen.add(key)
                    tracks.append(track_info)
                    recent_count += 1
        logger.info(f"Spotify recently played: {recent_count} new unique tracks")

        logger.info(f"Spotify fetch complete: {len(tracks)} total unique tracks")
        return tracks

    def _extract_spotify_track_info(
        self, track: dict[str, Any], rank: int | None = None, time_range: str | None = None
    ) -> dict[str, Any] | None:
        """Extract track info from Spotify track object.

        Args:
            track: Spotify track object.
            rank: Optional rank in user's top list (1-50).
            time_range: Optional time range for top tracks (short_term, medium_term, long_term).

        Returns:
            Dict with track info, or None if invalid.
        """
        if not track:
            return None

        title = track.get("name", "")
        artists = track.get("artists", [])

        if not title or not artists:
            return None

        # Use primary artist
        artist = artists[0].get("name", "") if artists else ""
        if not artist:
            return None

        return {
            "artist": artist,
            "title": title,
            "popularity": track.get("popularity", 0),
            "duration_ms": track.get("duration_ms"),
            "explicit": track.get("explicit", False),
            "rank": rank,
            "time_range": time_range,
        }

    async def _fetch_lastfm_tracks(self, username: str) -> list[dict[str, Any]]:
        """Fetch tracks from Last.fm API with real play counts.

        Fetches data in order of richness:
        1. Top tracks (ranked by playcount, gives official aggregated counts)
        2. Full scrobble history (gives complete coverage of ALL songs ever played)
        3. Loved tracks (may include songs not scrobbled recently)

        The full scrobble fetch ensures we capture every unique song the user
        has ever listened to, not just their top 1000.

        Args:
            username: Last.fm username.

        Returns:
            List of dicts with 'artist', 'title', 'playcount', 'rank' keys.
        """
        tracks: list[dict[str, Any]] = []
        seen: set[str] = set()
        # Track play counts from scrobbles (for songs not in top tracks)
        scrobble_counts: dict[str, int] = {}

        logger.info(f"Starting Last.fm track fetch for username: {username}")

        # Step 1: Fetch top tracks (overall) with pagination - up to 1000 tracks
        # This gives us tracks ranked by actual play count with official aggregation
        top_tracks = await self.lastfm.get_all_top_tracks(
            username=username,
            period="overall",
            max_tracks=self.LASTFM_TOP_TRACKS_LIMIT,
        )
        logger.info(f"Last.fm top tracks: fetched {len(top_tracks)} tracks with play counts")

        for item in top_tracks:
            track_info = self._extract_lastfm_track_info(item)
            if track_info:
                key = f"{track_info['artist']}:{track_info['title']}".lower()
                if key not in seen:
                    seen.add(key)
                    tracks.append(track_info)

        top_count = len(tracks)
        logger.info(f"Last.fm top tracks total: {top_count} unique tracks")

        # Step 2: Fetch scrobble history for coverage beyond top tracks
        if self.LASTFM_FULL_SCROBBLE_HISTORY:
            logger.info(f"Fetching Last.fm scrobble history (max {self.LASTFM_MAX_SCROBBLES})...")
            scrobble_count = 0
            new_from_scrobbles = 0

            async for scrobble in self.lastfm.get_all_scrobbles(username):
                scrobble_count += 1

                # Stop if we've hit the limit to avoid timeout
                if scrobble_count > self.LASTFM_MAX_SCROBBLES:
                    logger.info(f"Last.fm scrobbles: reached limit of {self.LASTFM_MAX_SCROBBLES}")
                    break

                # Log progress every 10k scrobbles
                if scrobble_count % 10000 == 0:
                    logger.info(
                        f"Last.fm scrobbles: processed {scrobble_count}, "
                        f"found {new_from_scrobbles} new unique tracks"
                    )

                # Extract artist and title from scrobble
                title = scrobble.get("name", "")
                artist_data = scrobble.get("artist", {})
                if isinstance(artist_data, dict):
                    artist = artist_data.get("name", "") or artist_data.get("#text", "")
                else:
                    artist = str(artist_data)

                if not title or not artist:
                    continue

                key = f"{artist}:{title}".lower()

                # Count this scrobble
                scrobble_counts[key] = scrobble_counts.get(key, 0) + 1

                # If not already seen from top tracks, add it
                if key not in seen:
                    seen.add(key)
                    new_from_scrobbles += 1
                    tracks.append(
                        {
                            "artist": artist,
                            "title": title,
                            "playcount": None,  # Will be set from scrobble_counts below
                            "rank": None,  # Not in top tracks
                            "from_scrobbles": True,
                        }
                    )

            # Update playcount for tracks discovered via scrobbles
            for track in tracks:
                if track.get("from_scrobbles") and track.get("playcount") is None:
                    key = f"{track['artist']}:{track['title']}".lower()
                    track["playcount"] = scrobble_counts.get(key, 1)

            logger.info(
                f"Last.fm scrobble history complete: {scrobble_count} total scrobbles, "
                f"{new_from_scrobbles} new unique tracks discovered"
            )

        # Step 3: Fetch loved tracks (may include tracks not scrobbled recently)
        loved_count = 0
        page = 1
        fetched = 0
        while fetched < self.LASTFM_LOVED_TRACKS_LIMIT:
            response = await self.lastfm.get_loved_tracks(username, limit=100, page=page)
            items = response.get("lovedtracks", {}).get("track", [])
            logger.info(f"Last.fm loved tracks: page={page}, items returned={len(items)}")
            if not items:
                break

            for item in items:
                track_info = self._extract_lastfm_track_info(item)
                if track_info:
                    key = f"{track_info['artist']}:{track_info['title']}".lower()
                    if key not in seen:
                        seen.add(key)
                        # Loved tracks don't have playcount, mark as loved
                        track_info["is_loved"] = True
                        tracks.append(track_info)
                        fetched += 1
                        loved_count += 1

            if len(items) < 100:
                break
            page += 1

        logger.info(f"Last.fm loved tracks total: {loved_count} new unique tracks")
        logger.info(f"Last.fm fetch complete: {len(tracks)} total unique tracks")

        return tracks

    def _extract_lastfm_track_info(self, track: dict[str, Any]) -> dict[str, Any] | None:
        """Extract artist, title, and playcount from Last.fm track object.

        Args:
            track: Last.fm track object (from top tracks or recent tracks).

        Returns:
            Dict with 'artist', 'title', 'playcount', 'rank', or None if invalid.
        """
        if not track:
            return None

        title = track.get("name", "")

        # Artist can be a string or an object
        artist_data = track.get("artist", {})
        if isinstance(artist_data, dict):
            artist = artist_data.get("name", "") or artist_data.get("#text", "")
        else:
            artist = str(artist_data)

        if not title or not artist:
            return None

        # Extract playcount (available in top tracks, not in recent tracks)
        playcount = None
        if "playcount" in track:
            try:
                playcount = int(track["playcount"])
            except (ValueError, TypeError):
                pass

        # Extract rank (we add this in get_all_top_tracks)
        rank = track.get("rank")

        return {
            "artist": artist,
            "title": title,
            "playcount": playcount,
            "rank": rank,
        }

    async def _upsert_user_songs(
        self,
        user_id: str,
        matched_tracks: list[MatchedTrack],
        source: str,
    ) -> tuple[int, int]:
        """Create or update UserSong records for ALL tracks.

        Stores both matched tracks (with karaoke versions) and unmatched tracks
        (for "Create Your Own Karaoke" feature).

        Args:
            user_id: User ID.
            matched_tracks: List of matched tracks from track matcher.
            source: Source service (spotify, lastfm).

        Returns:
            Tuple of (created_count, updated_count).
        """
        created = 0
        updated = 0
        now = datetime.now(UTC)

        for match in matched_tracks:
            catalog_song = match.catalog_song
            has_karaoke = catalog_song is not None

            if catalog_song is not None:
                # Matched track - use catalog song ID
                song_id = str(catalog_song.id)
                artist = catalog_song.artist
                title = catalog_song.title
            else:
                # Unmatched track - use synthetic ID based on normalized values
                song_id = f"{source}:{match.normalized_artist}:{match.normalized_title}"
                artist = match.original_artist
                title = match.original_title

            user_song_id = f"{user_id}:{song_id}"

            # Check if UserSong exists
            existing = await self.firestore.get_document(self.USER_SONGS_COLLECTION, user_song_id)

            if existing:
                # Update existing record
                update_data: dict[str, Any] = {
                    "updated_at": now.isoformat(),
                }

                # Update playcount if we have new data from Last.fm
                if match.playcount is not None:
                    update_data["playcount"] = match.playcount
                    update_data["last_played_at"] = now.isoformat()

                # Update rank if available
                if match.rank is not None:
                    update_data["rank"] = match.rank

                # Increment sync_count (tracks how many times we've seen this in sync)
                current_sync_count = existing.get("sync_count", existing.get("play_count", 0))
                update_data["sync_count"] = current_sync_count + 1

                await self.firestore.update_document(
                    self.USER_SONGS_COLLECTION,
                    user_song_id,
                    update_data,
                )
                updated += 1
            else:
                # Create new UserSong
                user_song_data: dict[str, Any] = {
                    "id": user_song_id,
                    "user_id": user_id,
                    "song_id": song_id,
                    "source": source,
                    "sync_count": 1,  # Times seen during sync (not actual plays)
                    "playcount": match.playcount,  # Actual play count from Last.fm (if available)
                    "rank": match.rank,  # Rank in user's top list (if available)
                    "last_played_at": now.isoformat(),
                    "is_saved": source == "spotify",  # Spotify saved tracks are "saved"
                    "times_sung": 0,
                    "last_sung_at": None,
                    "average_rating": None,
                    "notes": None,
                    "artist": artist,
                    "title": title,
                    "has_karaoke_version": has_karaoke,
                    "spotify_popularity": match.spotify_popularity,
                    "duration_ms": match.duration_ms,
                    "explicit": match.explicit,
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                }
                await self.firestore.set_document(
                    self.USER_SONGS_COLLECTION,
                    user_song_id,
                    user_song_data,
                )
                created += 1

        return created, updated


# Lazy initialization
_sync_service: SyncService | None = None


def get_sync_service(
    settings: BackendSettings | None = None,
    firestore: FirestoreService | None = None,
    music_service_service: MusicServiceService | None = None,
) -> SyncService:
    """Get the sync service instance.

    Args:
        settings: Optional settings override.
        firestore: Optional Firestore service override.
        music_service_service: Optional music service override.

    Returns:
        SyncService instance.
    """
    global _sync_service

    if _sync_service is None or settings is not None or firestore is not None or music_service_service is not None:
        if settings is None:
            from backend.config import get_backend_settings

            settings = get_backend_settings()
        if firestore is None:
            firestore = FirestoreService(settings)
        if music_service_service is None:
            from backend.services.music_service_service import get_music_service_service

            music_service_service = get_music_service_service(settings, firestore)

        _sync_service = SyncService(settings, firestore, music_service_service)

    return _sync_service
