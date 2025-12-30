"""Service for syncing listening history from music services.

Fetches tracks from Spotify and Last.fm, matches them against the
karaoke catalog, and creates UserSong records.
"""

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


@dataclass
class SyncResult:
    """Result of a sync operation for a single service."""

    service_type: str
    tracks_fetched: int
    tracks_matched: int
    user_songs_created: int
    user_songs_updated: int
    error: str | None = None


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

        # Fetch saved tracks (library)
        offset = 0
        while offset < self.SPOTIFY_SAVED_TRACKS_LIMIT:
            response = await self.spotify.get_saved_tracks(access_token, limit=50, offset=offset)
            items = response.get("items", [])
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

            offset += len(items)
            if len(items) < 50:
                break

        # Fetch top tracks (medium term - ~6 months)
        response = await self.spotify.get_top_tracks(access_token, time_range="medium_term", limit=50)
        for track in response.get("items", []):
            track_info = self._extract_spotify_track_info(track)
            if track_info:
                key = f"{track_info['artist']}:{track_info['title']}".lower()
                if key not in seen:
                    seen.add(key)
                    tracks.append(track_info)

        # Fetch top tracks (long term - all time)
        response = await self.spotify.get_top_tracks(access_token, time_range="long_term", limit=50)
        for track in response.get("items", []):
            track_info = self._extract_spotify_track_info(track)
            if track_info:
                key = f"{track_info['artist']}:{track_info['title']}".lower()
                if key not in seen:
                    seen.add(key)
                    tracks.append(track_info)

        # Fetch recently played
        response = await self.spotify.get_recently_played(access_token, limit=50)
        for item in response.get("items", []):
            track = item.get("track", {})
            track_info = self._extract_spotify_track_info(track)
            if track_info:
                key = f"{track_info['artist']}:{track_info['title']}".lower()
                if key not in seen:
                    seen.add(key)
                    tracks.append(track_info)

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

        # Fetch top tracks (overall)
        page = 1
        fetched = 0
        while fetched < self.LASTFM_TOP_TRACKS_LIMIT:
            response = await self.lastfm.get_top_tracks(username, period="overall", limit=100, page=page)
            items = response.get("toptracks", {}).get("track", [])
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

            if len(items) < 100:
                break
            page += 1

        # Fetch loved tracks
        page = 1
        fetched = 0
        while fetched < self.LASTFM_LOVED_TRACKS_LIMIT:
            response = await self.lastfm.get_loved_tracks(username, limit=100, page=page)
            items = response.get("lovedtracks", {}).get("track", [])
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

            if len(items) < 100:
                break
            page += 1

        # Fetch recent tracks
        response = await self.lastfm.get_recent_tracks(username, limit=200, page=1)
        items = response.get("recenttracks", {}).get("track", [])
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
                # Update existing - increment play count
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
