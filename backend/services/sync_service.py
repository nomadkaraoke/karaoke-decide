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
    SPOTIFY_TOP_TRACKS_LIMIT = 100
    SPOTIFY_RECENT_TRACKS_LIMIT = 50
    LASTFM_TOP_TRACKS_LIMIT = 500
    LASTFM_LOVED_TRACKS_LIMIT = 200
    LASTFM_RECENT_TRACKS_LIMIT = 200

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
            logger.info(f"  - {svc.service_type}: username={svc.service_username}, display={svc.display_name}")
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
        """Fetch and store user's top artists from Last.fm.

        Args:
            user_id: User ID.
            username: Last.fm username.

        Returns:
            Number of artists stored.
        """
        stored = 0
        now = datetime.now(UTC)

        # Fetch top artists for different time periods
        for period in ["overall", "12month", "6month"]:
            try:
                response = await self.lastfm.get_top_artists(username, period=period, limit=50)
                artists = response.get("topartists", {}).get("artist", [])

                for rank, artist in enumerate(artists, 1):
                    artist_name = artist.get("name", "")
                    # Create unique ID using normalized name
                    safe_name = artist_name.lower().replace(" ", "_")[:50]
                    artist_id = f"{user_id}:lastfm:{safe_name}:{period}"

                    artist_data = {
                        "id": artist_id,
                        "user_id": user_id,
                        "source": "lastfm",
                        "artist_name": artist_name,
                        "rank": rank,
                        "period": period,
                        "playcount": int(artist.get("playcount", 0)),
                        "updated_at": now.isoformat(),
                    }

                    await self.firestore.set_document("user_artists", artist_id, artist_data)
                    stored += 1

            except Exception:
                # Continue with other periods if one fails
                continue

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

        # Fetch top tracks (medium term - ~6 months)
        top_medium_count = 0
        response = await self.spotify.get_top_tracks(access_token, time_range="medium_term", limit=50)
        logger.info(f"Spotify top tracks (medium_term): {len(response.get('items', []))} items")
        for track in response.get("items", []):
            track_info = self._extract_spotify_track_info(track)
            if track_info:
                key = f"{track_info['artist']}:{track_info['title']}".lower()
                if key not in seen:
                    seen.add(key)
                    tracks.append(track_info)
                    top_medium_count += 1
        logger.info(f"Spotify top tracks (medium_term): {top_medium_count} new unique tracks")

        # Fetch top tracks (long term - all time)
        top_long_count = 0
        response = await self.spotify.get_top_tracks(access_token, time_range="long_term", limit=50)
        logger.info(f"Spotify top tracks (long_term): {len(response.get('items', []))} items")
        for track in response.get("items", []):
            track_info = self._extract_spotify_track_info(track)
            if track_info:
                key = f"{track_info['artist']}:{track_info['title']}".lower()
                if key not in seen:
                    seen.add(key)
                    tracks.append(track_info)
                    top_long_count += 1
        logger.info(f"Spotify top tracks (long_term): {top_long_count} new unique tracks")

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

    def _extract_spotify_track_info(self, track: dict[str, Any]) -> dict[str, str] | None:
        """Extract artist and title from Spotify track object.

        Args:
            track: Spotify track object.

        Returns:
            Dict with 'artist' and 'title', or None if invalid.
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

        return {"artist": artist, "title": title}

    async def _fetch_lastfm_tracks(self, username: str) -> list[dict[str, str]]:
        """Fetch tracks from Last.fm API.

        Combines top tracks, loved tracks, and recent tracks.

        Args:
            username: Last.fm username.

        Returns:
            List of dicts with 'artist' and 'title' keys.
        """
        tracks: list[dict[str, str]] = []
        seen: set[str] = set()

        logger.info(f"Starting Last.fm track fetch for username: {username}")

        # Fetch top tracks (overall)
        page = 1
        fetched = 0
        top_count = 0
        while fetched < self.LASTFM_TOP_TRACKS_LIMIT:
            response = await self.lastfm.get_top_tracks(username, period="overall", limit=100, page=page)
            items = response.get("toptracks", {}).get("track", [])
            logger.info(f"Last.fm top tracks: page={page}, items returned={len(items)}")
            if not items:
                break

            for item in items:
                track_info = self._extract_lastfm_track_info(item)
                if track_info:
                    key = f"{track_info['artist']}:{track_info['title']}".lower()
                    if key not in seen:
                        seen.add(key)
                        tracks.append(track_info)
                        fetched += 1
                        top_count += 1

            if len(items) < 100:
                break
            page += 1

        logger.info(f"Last.fm top tracks total: {top_count} unique tracks")

        # Fetch loved tracks
        page = 1
        fetched = 0
        loved_count = 0
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
                        tracks.append(track_info)
                        fetched += 1
                        loved_count += 1

            if len(items) < 100:
                break
            page += 1

        logger.info(f"Last.fm loved tracks total: {loved_count} new unique tracks")

        # Fetch recent tracks
        recent_count = 0
        response = await self.lastfm.get_recent_tracks(username, limit=200, page=1)
        items = response.get("recenttracks", {}).get("track", [])
        logger.info(f"Last.fm recent tracks: {len(items)} items returned")
        for item in items:
            # Skip currently playing track (has @attr with nowplaying)
            if item.get("@attr", {}).get("nowplaying"):
                continue

            track_info = self._extract_lastfm_track_info(item)
            if track_info:
                key = f"{track_info['artist']}:{track_info['title']}".lower()
                if key not in seen:
                    seen.add(key)
                    tracks.append(track_info)
                    recent_count += 1

        logger.info(f"Last.fm recent tracks: {recent_count} new unique tracks")
        logger.info(f"Last.fm fetch complete: {len(tracks)} total unique tracks")

        return tracks

    def _extract_lastfm_track_info(self, track: dict[str, Any]) -> dict[str, str] | None:
        """Extract artist and title from Last.fm track object.

        Args:
            track: Last.fm track object.

        Returns:
            Dict with 'artist' and 'title', or None if invalid.
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

        return {"artist": artist, "title": title}

    async def _upsert_user_songs(
        self,
        user_id: str,
        matched_tracks: list[MatchedTrack],
        source: str,
    ) -> tuple[int, int]:
        """Create or update UserSong records for matched tracks.

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
            if match.catalog_song is None:
                # Skip unmatched tracks
                continue

            song_id = str(match.catalog_song.id)
            user_song_id = f"{user_id}:{song_id}"

            # Check if UserSong exists
            existing = await self.firestore.get_document(self.USER_SONGS_COLLECTION, user_song_id)

            if existing:
                # Update existing - increment sync count
                # NOTE: play_count here represents "times seen during sync", not actual plays.
                # This is a known limitation - consider renaming to sync_count in future.
                # See: https://github.com/nomadkaraoke/karaoke-decide/pull/5
                current_play_count = existing.get("play_count", 0)
                await self.firestore.update_document(
                    self.USER_SONGS_COLLECTION,
                    user_song_id,
                    {
                        "play_count": current_play_count + 1,
                        "last_played_at": now.isoformat(),
                        "updated_at": now.isoformat(),
                    },
                )
                updated += 1
            else:
                # Create new UserSong
                user_song_data = {
                    "id": user_song_id,
                    "user_id": user_id,
                    "song_id": song_id,
                    "play_count": 1,
                    "last_played_at": now.isoformat(),
                    "is_saved": source == "spotify",  # Spotify saved tracks are "saved"
                    "times_sung": 0,
                    "last_sung_at": None,
                    "average_rating": None,
                    "notes": None,
                    "artist": match.catalog_song.artist,
                    "title": match.catalog_song.title,
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
