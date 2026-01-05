"""Service for syncing listening history from music services.

Fetches tracks from Spotify and Last.fm, matches them against the
karaoke catalog, and creates UserSong records.
"""

import logging
import re
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
    # Full scrobble history - now incremental with progress tracking
    LASTFM_FULL_SCROBBLE_HISTORY = True  # Enable fetching scrobbles beyond top tracks
    LASTFM_BATCH_SIZE = 1000  # Save progress every N scrobbles

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
        """Sync Last.fm with incremental progress saving.

        This method saves data incrementally as it fetches, so even if
        interrupted, progress is preserved and can be resumed.
        """
        try:
            await self.music_service_service.update_sync_status(user_id, "lastfm", "syncing")

            username = service.service_username
            if not username:
                raise MusicServiceError("No Last.fm username configured")

            # Get existing scrobble progress (for resume capability)
            progress = await self.music_service_service.get_scrobble_progress(user_id, "lastfm")
            oldest_timestamp = progress.get("oldest_scrobble_timestamp")
            history_complete = progress.get("scrobble_history_complete", False)
            total_scrobbles_processed = progress.get("scrobbles_processed", 0)

            logger.info(
                f"Last.fm sync starting for {username}: "
                f"oldest_timestamp={oldest_timestamp}, "
                f"history_complete={history_complete}, "
                f"scrobbles_processed={total_scrobbles_processed}"
            )

            total_tracks_fetched = 0
            total_tracks_matched = 0
            total_created = 0
            total_updated = 0

            if progress_callback:
                await progress_callback(
                    current_service="lastfm",
                    current_phase="fetching_top_tracks",
                    total_tracks=0,
                    processed_tracks=0,
                    matched_tracks=0,
                )

            # Step 1: Always fetch and save top tracks (they have accurate playcounts)
            top_tracks = await self.lastfm.get_all_top_tracks(
                username=username,
                period="overall",
                max_tracks=self.LASTFM_TOP_TRACKS_LIMIT,
            )
            logger.info(f"Last.fm top tracks: fetched {len(top_tracks)} tracks")

            if top_tracks:
                top_track_infos: list[dict[str, Any]] = [
                    t for t in (self._extract_lastfm_track_info(t) for t in top_tracks) if t is not None
                ]

                # Update progress before long-running match operation (keeps heartbeat alive)
                if progress_callback:
                    await progress_callback(
                        current_service="lastfm",
                        current_phase="matching_top_tracks",
                        total_tracks=len(top_track_infos),
                        processed_tracks=0,
                        matched_tracks=0,
                    )

                matched = await self.track_matcher.batch_match_tracks(top_track_infos)
                created, updated = await self._upsert_user_songs(user_id, matched, "lastfm")
                total_tracks_fetched += len(top_track_infos)
                total_tracks_matched += sum(1 for m in matched if m.catalog_song is not None)
                total_created += created
                total_updated += updated
                logger.info(f"Last.fm top tracks: saved {created} new, {updated} updated")

            # Step 2: Fetch scrobble history incrementally (if not complete)
            if self.LASTFM_FULL_SCROBBLE_HISTORY and not history_complete:
                if progress_callback:
                    await progress_callback(
                        current_service="lastfm",
                        current_phase="fetching_scrobbles",
                        total_tracks=total_tracks_fetched,
                        processed_tracks=total_tracks_fetched,
                        matched_tracks=total_tracks_matched,
                    )

                # Build set of already-saved tracks to avoid duplicates
                seen_keys: set[str] = set()
                for t in top_tracks:
                    info = self._extract_lastfm_track_info(t)
                    if info:
                        seen_keys.add(f"{info['artist']}:{info['title']}".lower())

                scrobble_result = await self._sync_scrobbles_incremental(
                    user_id=user_id,
                    username=username,
                    seen_keys=seen_keys,
                    oldest_timestamp=oldest_timestamp,
                    total_scrobbles_processed=total_scrobbles_processed,
                    progress_callback=progress_callback,
                )

                total_tracks_fetched += scrobble_result["tracks_fetched"]
                total_tracks_matched += scrobble_result["tracks_matched"]
                total_created += scrobble_result["created"]
                total_updated += scrobble_result["updated"]

            # Step 3: Fetch loved tracks (small set, always refresh)
            if progress_callback:
                await progress_callback(
                    current_service="lastfm",
                    current_phase="fetching_loved",
                    total_tracks=total_tracks_fetched,
                    processed_tracks=total_tracks_fetched,
                    matched_tracks=total_tracks_matched,
                )

            loved_result = await self._sync_loved_tracks(user_id, username)
            total_tracks_fetched += loved_result["tracks_fetched"]
            total_tracks_matched += loved_result["tracks_matched"]
            total_created += loved_result["created"]
            total_updated += loved_result["updated"]

            # Step 4: Fetch and store top artists
            if progress_callback:
                await progress_callback(
                    current_service="lastfm",
                    current_phase="fetching_artists",
                    total_tracks=total_tracks_fetched,
                    processed_tracks=total_tracks_fetched,
                    matched_tracks=total_tracks_matched,
                )

            artists_stored = await self._fetch_and_store_lastfm_artists(user_id, username)

            await self.music_service_service.update_sync_status(
                user_id, "lastfm", "idle", tracks_synced=total_tracks_matched
            )

            return SyncResult(
                service_type="lastfm",
                tracks_fetched=total_tracks_fetched,
                tracks_matched=total_tracks_matched,
                user_songs_created=total_created,
                user_songs_updated=total_updated,
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

    async def _sync_scrobbles_incremental(
        self,
        user_id: str,
        username: str,
        seen_keys: set[str],
        oldest_timestamp: int | None,
        total_scrobbles_processed: int,
        progress_callback: ProgressCallback | None,
    ) -> dict[str, int]:
        """Fetch and save scrobbles incrementally with progress tracking.

        Processes scrobbles in batches, saving after each batch so progress
        is preserved if interrupted. Can resume from where it left off.

        Args:
            user_id: User ID.
            username: Last.fm username.
            seen_keys: Set of "artist:title" keys already processed.
            oldest_timestamp: Unix timestamp to fetch scrobbles BEFORE (for resume).
            total_scrobbles_processed: Total scrobbles processed so far.
            progress_callback: Progress callback.

        Returns:
            Dict with tracks_fetched, tracks_matched, created, updated counts.
        """
        tracks_fetched = 0
        tracks_matched = 0
        created = 0
        updated = 0

        batch: list[dict[str, Any]] = []
        scrobble_count = 0
        current_oldest_timestamp = oldest_timestamp
        scrobble_counts: dict[str, int] = {}  # Track play counts for each song

        logger.info(f"Starting incremental scrobble sync to timestamp {oldest_timestamp}")

        # Use to_timestamp to only fetch scrobbles BEFORE what we've already processed
        # This lets the Last.fm API filter on the server side for efficiency
        async for scrobble in self.lastfm.get_all_scrobbles(username, to_timestamp=oldest_timestamp):
            # Get scrobble timestamp
            date_info = scrobble.get("date", {})
            if isinstance(date_info, dict):
                scrobble_ts = int(date_info.get("uts", 0))
            else:
                scrobble_ts = 0

            scrobble_count += 1

            # Extract track info
            title = scrobble.get("name", "")
            artist_data = scrobble.get("artist", {})
            if isinstance(artist_data, dict):
                artist = artist_data.get("name", "") or artist_data.get("#text", "")
            else:
                artist = str(artist_data)

            if not title or not artist:
                continue

            key = f"{artist}:{title}".lower()
            scrobble_counts[key] = scrobble_counts.get(key, 0) + 1

            # Track oldest timestamp we've seen
            if scrobble_ts > 0:
                if current_oldest_timestamp is None or scrobble_ts < current_oldest_timestamp:
                    current_oldest_timestamp = scrobble_ts

            # Add to batch if new
            if key not in seen_keys:
                seen_keys.add(key)
                batch.append(
                    {
                        "artist": artist,
                        "title": title,
                        "playcount": None,  # Will set from scrobble_counts
                        "rank": None,
                        "from_scrobbles": True,
                    }
                )

            # Process batch when it reaches size limit
            if len(batch) >= self.LASTFM_BATCH_SIZE:
                # Set playcounts from accumulated scrobble counts
                for track in batch:
                    track_key = f"{track['artist']}:{track['title']}".lower()
                    track["playcount"] = scrobble_counts.get(track_key, 1)

                # Match and save batch
                matched = await self.track_matcher.batch_match_tracks(batch)
                batch_created, batch_updated = await self._upsert_user_songs(user_id, matched, "lastfm")

                tracks_fetched += len(batch)
                tracks_matched += sum(1 for m in matched if m.catalog_song is not None)
                created += batch_created
                updated += batch_updated

                # Update progress in Firestore
                await self.music_service_service.update_scrobble_progress(
                    user_id=user_id,
                    service_type="lastfm",
                    oldest_scrobble_timestamp=current_oldest_timestamp,
                    scrobbles_processed=total_scrobbles_processed + scrobble_count,
                )

                logger.info(
                    f"Last.fm scrobbles: processed {scrobble_count}, "
                    f"saved batch of {len(batch)} new tracks, "
                    f"oldest_ts={current_oldest_timestamp}"
                )

                if progress_callback:
                    await progress_callback(
                        current_service="lastfm",
                        current_phase="fetching_scrobbles",
                        total_tracks=tracks_fetched,
                        processed_tracks=scrobble_count,
                        matched_tracks=tracks_matched,
                    )

                batch = []

            # Log progress every 10k scrobbles
            if scrobble_count % 10000 == 0:
                logger.info(f"Last.fm scrobbles: processed {scrobble_count}")

        # Process final batch
        if batch:
            for track in batch:
                track_key = f"{track['artist']}:{track['title']}".lower()
                track["playcount"] = scrobble_counts.get(track_key, 1)

            matched = await self.track_matcher.batch_match_tracks(batch)
            batch_created, batch_updated = await self._upsert_user_songs(user_id, matched, "lastfm")

            tracks_fetched += len(batch)
            tracks_matched += sum(1 for m in matched if m.catalog_song is not None)
            created += batch_created
            updated += batch_updated

        # Mark history as complete
        await self.music_service_service.update_scrobble_progress(
            user_id=user_id,
            service_type="lastfm",
            oldest_scrobble_timestamp=current_oldest_timestamp,
            scrobble_history_complete=True,
            scrobbles_processed=total_scrobbles_processed + scrobble_count,
        )

        logger.info(
            f"Last.fm scrobble sync complete: {scrobble_count} scrobbles, "
            f"{tracks_fetched} unique tracks, {tracks_matched} matched"
        )

        return {
            "tracks_fetched": tracks_fetched,
            "tracks_matched": tracks_matched,
            "created": created,
            "updated": updated,
        }

    async def _sync_loved_tracks(self, user_id: str, username: str) -> dict[str, int]:
        """Fetch and save loved tracks.

        Returns:
            Dict with tracks_fetched, tracks_matched, created, updated counts.
        """
        tracks_fetched = 0
        tracks_matched = 0
        created = 0
        updated = 0

        page = 1
        while tracks_fetched < self.LASTFM_LOVED_TRACKS_LIMIT:
            response = await self.lastfm.get_loved_tracks(username, limit=100, page=page)
            items = response.get("lovedtracks", {}).get("track", [])
            if not items:
                break

            track_infos = []
            for item in items:
                if tracks_fetched >= self.LASTFM_LOVED_TRACKS_LIMIT:
                    break
                track_info = self._extract_lastfm_track_info(item)
                if track_info:
                    track_info["is_loved"] = True
                    track_infos.append(track_info)
                    tracks_fetched += 1

            if track_infos:
                matched = await self.track_matcher.batch_match_tracks(track_infos)
                batch_created, batch_updated = await self._upsert_user_songs(user_id, matched, "lastfm")
                tracks_matched += sum(1 for m in matched if m.catalog_song is not None)
                created += batch_created
                updated += batch_updated

            if len(items) < 100:
                break
            page += 1

        logger.info(f"Last.fm loved tracks: {tracks_fetched} fetched, {tracks_matched} matched")

        return {
            "tracks_fetched": tracks_fetched,
            "tracks_matched": tracks_matched,
            "created": created,
            "updated": updated,
        }

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
        data about which artists the user knows best. Enriches with Spotify metadata
        (genres, popularity) using the pre-computed normalized artists table.

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

            # Batch lookup Spotify metadata for all artists
            artist_names = [a.get("name", "") for a in artists if a.get("name")]
            spotify_metadata = self._get_spotify_metadata_for_artists(artist_names)
            logger.info(f"Last.fm artists: found Spotify metadata for {len(spotify_metadata)} artists")

            for artist in artists:
                artist_name = artist.get("name", "")
                if not artist_name:
                    continue

                # Create unique ID using normalized name
                # Remove slashes and special chars that break Firestore paths
                safe_name = re.sub(r"[^a-z0-9_]", "_", artist_name.lower())[:50]
                artist_id = f"{user_id}:lastfm:{safe_name}"

                # Get playcount (this is the real listen count!)
                playcount = 0
                if "playcount" in artist:
                    try:
                        playcount = int(artist["playcount"])
                    except (ValueError, TypeError):
                        pass

                artist_data: dict[str, Any] = {
                    "id": artist_id,
                    "user_id": user_id,
                    "source": "lastfm",
                    "artist_name": artist_name,
                    "rank": artist.get("rank", stored + 1),
                    "period": "overall",
                    "playcount": playcount,
                    "updated_at": now.isoformat(),
                }

                # Enrich with Spotify metadata if available
                # Normalize name to match BigQuery lookup key
                normalized_name = artist_name.lower()
                normalized_name = re.sub(r"[^a-z0-9 ]", " ", normalized_name)
                normalized_name = re.sub(r"\s+", " ", normalized_name).strip()

                if normalized_name in spotify_metadata:
                    metadata = spotify_metadata[normalized_name]
                    artist_data["spotify_artist_id"] = metadata.artist_id
                    artist_data["genres"] = metadata.genres
                    artist_data["popularity"] = metadata.popularity

                await self.firestore.set_document("user_artists", artist_id, artist_data)
                stored += 1

        except Exception as e:
            logger.error(f"Error fetching Last.fm artists: {e}")

        logger.info(f"Last.fm artists: stored {stored} artists")
        return stored

    def _get_spotify_metadata_for_artists(
        self,
        artist_names: list[str],
    ) -> dict[str, Any]:
        """Get Spotify metadata for a list of artist names.

        Uses the fast batch lookup method on the pre-normalized BigQuery table.

        Args:
            artist_names: List of artist names

        Returns:
            Dict mapping normalized name -> ArtistMetadata
        """
        try:
            from karaoke_decide.services.bigquery_catalog import BigQueryCatalogService

            catalog = BigQueryCatalogService()
            return catalog.batch_lookup_artists_by_name(artist_names)
        except Exception as e:
            logger.warning(f"Could not enrich artists with Spotify metadata: {e}")
            return {}

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

        Uses incremental sync with progress tracking. Saves data as it fetches
        so progress is preserved if interrupted.

        Args:
            user_id: User ID.
            service: Last.fm MusicService record.

        Returns:
            SyncResult with counts.
        """
        # Delegate to the incremental sync method
        return await self._sync_lastfm_with_progress(user_id, service, progress_callback=None)

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
