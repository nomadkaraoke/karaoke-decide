"""User data service for managing user preferences and artists.

Provides functionality for the My Data page, including:
- Artist management (add/remove manual artists)
- Preferences CRUD (genres, decades, energy)
- Data aggregation for summary view
"""

from datetime import UTC, datetime
from typing import Any

from google.cloud.firestore_v1 import ArrayRemove, ArrayUnion

from backend.services.firestore_service import FirestoreService


class UserDataService:
    """Service for managing user data and preferences."""

    def __init__(self, firestore: FirestoreService):
        self.firestore = firestore

    async def _get_user_document(self, user_id: str) -> tuple[dict[str, Any] | None, str | None]:
        """Find a user document by user_id.

        For guest users, the document ID is the user_id itself.
        For verified users, the document ID is the email hash, so we need to query.

        Returns:
            Tuple of (user_doc, doc_id) or (None, None) if not found
        """
        # Guest users: doc ID = user_id
        if user_id.startswith("guest_"):
            doc = await self.firestore.get_document("decide_users", user_id)
            if doc:
                return doc, user_id
            return None, None

        # Verified users: query by user_id field
        docs = await self.firestore.query_documents(
            collection="decide_users",
            filters=[("user_id", "==", user_id)],
            limit=1,
        )
        if docs:
            return docs[0], docs[0].get("id")
        return None, None

    async def get_data_summary(self, user_id: str) -> dict[str, Any]:
        """Get aggregated summary of user's data for My Data page.

        Returns counts and status for:
        - Connected services
        - Artists by source
        - Songs total and with karaoke versions
        - Quiz completion status
        """
        # Get user profile for quiz preferences
        user_doc, _ = await self._get_user_document(user_id)
        user_data = user_doc or {}

        # Count artists by source
        artists_by_source: dict[str, int] = {"spotify": 0, "lastfm": 0, "quiz": 0, "manual": 0}

        # Count from user_artists collection (synced from services)
        synced_artists = await self.firestore.query_documents(
            collection="user_artists",
            filters=[("user_id", "==", user_id)],
        )
        for artist in synced_artists:
            source = artist.get("source", "unknown")
            if source in artists_by_source:
                artists_by_source[source] += 1

        # Count from quiz_artists_known (quiz + manual additions)
        quiz_artists = user_data.get("quiz_artists_known", [])
        artists_by_source["quiz"] = len(quiz_artists)

        # Count songs
        songs_total = await self.firestore.count_documents(
            collection="user_songs",
            filters=[("user_id", "==", user_id)],
        )
        songs_with_karaoke = await self.firestore.count_documents(
            collection="user_songs",
            filters=[("user_id", "==", user_id), ("has_karaoke_version", "==", True)],
        )

        # Get connected services
        services = await self.firestore.query_documents(
            collection="music_services",
            filters=[("user_id", "==", user_id)],
        )
        services_summary: dict[str, dict[str, Any]] = {}
        for service in services:
            service_type = service.get("service_type", "unknown")
            services_summary[service_type] = {
                "connected": True,
                "username": service.get("service_username"),
                "tracks_synced": service.get("tracks_synced", 0),
                "last_sync_at": service.get("last_sync_at"),
            }

        # Ensure spotify and lastfm keys exist even if not connected
        if "spotify" not in services_summary:
            services_summary["spotify"] = {"connected": False}
        if "lastfm" not in services_summary:
            services_summary["lastfm"] = {"connected": False}

        # Quiz status
        quiz_completed = user_data.get("quiz_completed_at") is not None
        quiz_preferences = {
            "decade": user_data.get("quiz_decade_pref"),
            "energy": user_data.get("quiz_energy_pref"),
            "genres": user_data.get("quiz_genres_pref", []),
        }

        return {
            "services": services_summary,
            "artists": {
                "total": sum(artists_by_source.values()),
                "by_source": artists_by_source,
            },
            "songs": {
                "total": songs_total,
                "with_karaoke": songs_with_karaoke,
            },
            "preferences": {
                "completed": quiz_completed,
                **quiz_preferences,
            },
        }

    async def get_preferences(self, user_id: str) -> dict[str, Any]:
        """Get user's quiz preferences."""
        user_doc, _ = await self._get_user_document(user_id)
        if not user_doc:
            return {
                "decade_preference": None,
                "energy_preference": None,
                "genres": [],
            }

        return {
            "decade_preference": user_doc.get("quiz_decade_pref"),
            "energy_preference": user_doc.get("quiz_energy_pref"),
            "genres": user_doc.get("quiz_genres_pref", []),
        }

    async def update_preferences(
        self,
        user_id: str,
        decade_preference: str | None = None,
        energy_preference: str | None = None,
        genres: list[str] | None = None,
    ) -> dict[str, Any]:
        """Update user's quiz preferences.

        Only updates fields that are explicitly provided (not None).
        """
        # Find the user document to get the correct document ID
        user_doc, doc_id = await self._get_user_document(user_id)

        update_data: dict[str, Any] = {
            "updated_at": datetime.now(UTC).isoformat(),
        }

        if decade_preference is not None:
            update_data["quiz_decade_pref"] = decade_preference

        if energy_preference is not None:
            update_data["quiz_energy_pref"] = energy_preference

        if genres is not None:
            update_data["quiz_genres_pref"] = genres

        if doc_id:
            # Update existing user document
            await self.firestore.update_document("decide_users", doc_id, update_data)
        else:
            # User document doesn't exist - create it with set+merge
            # This handles edge cases where user exists in JWT but not in Firestore
            update_data["user_id"] = user_id
            await self.firestore.set_document("decide_users", user_id, update_data, merge=True)

        return await self.get_preferences(user_id)

    async def get_all_artists(
        self,
        user_id: str,
        page: int = 1,
        per_page: int = 100,
    ) -> dict[str, Any]:
        """Get all artists for user from all sources with pagination.

        Combines:
        - user_artists collection (from Spotify/Last.fm sync)
        - quiz_artists_known from user profile (from quiz + manual additions)

        Merges data when same artist exists in multiple sources, preserving
        metadata from all sources (e.g., Spotify genres + Last.fm playcount).

        Args:
            user_id: User ID
            page: Page number (1-indexed)
            per_page: Number of artists per page

        Returns:
            Dict with artists list, pagination info, and total count
        """
        # Get user document for quiz artists and exclusions
        user_doc, _ = await self._get_user_document(user_id)
        excluded_artists = set(name.lower() for name in (user_doc or {}).get("excluded_artists", []))

        # Get synced artists from user_artists collection
        synced_artists = await self.firestore.query_documents(
            collection="user_artists",
            filters=[("user_id", "==", user_id)],
        )

        # Merge artists by name, combining data from multiple sources
        merged_artists: dict[str, dict[str, Any]] = {}
        for artist in synced_artists:
            artist_name = artist.get("artist_name", "")
            if not artist_name:
                continue

            key = artist_name.lower()
            source = artist.get("source", "unknown")

            if key not in merged_artists:
                # First time seeing this artist - initialize
                merged_artists[key] = {
                    "artist_name": artist_name,
                    "sources": [],
                    "spotify_rank": None,
                    "spotify_time_range": None,
                    "lastfm_rank": None,
                    "lastfm_playcount": None,
                    "popularity": None,
                    "genres": [],
                    "is_excluded": key in excluded_artists,
                    "is_manual": False,
                }

            existing = merged_artists[key]

            # Add source if not already tracked
            if source not in existing["sources"]:
                existing["sources"].append(source)

            # Merge source-specific data
            if source == "spotify":
                rank = artist.get("rank")
                # Keep best (lowest) Spotify rank across time ranges
                if rank and (existing["spotify_rank"] is None or rank < existing["spotify_rank"]):
                    existing["spotify_rank"] = rank
                    existing["spotify_time_range"] = artist.get("time_range", "")
                # Always take Spotify's popularity and genres (they're global, not per-sync)
                if artist.get("popularity") is not None:
                    existing["popularity"] = artist.get("popularity")
                genres = artist.get("genres", [])
                if genres and not existing["genres"]:
                    existing["genres"] = genres

            elif source == "lastfm":
                rank = artist.get("rank")
                playcount = artist.get("playcount") or 0
                # Keep best (highest playcount) Last.fm data
                if existing["lastfm_playcount"] is None or playcount > existing["lastfm_playcount"]:
                    existing["lastfm_rank"] = rank
                    existing["lastfm_playcount"] = playcount

        # Add quiz/manual artists from user profile
        if user_doc:
            quiz_artists = user_doc.get("quiz_artists_known", [])
            for idx, artist_name in enumerate(quiz_artists):
                key = artist_name.lower()
                if key not in merged_artists:
                    # New artist from quiz/manual
                    merged_artists[key] = {
                        "artist_name": artist_name,
                        "sources": ["quiz"],
                        "spotify_rank": None,
                        "spotify_time_range": None,
                        "lastfm_rank": None,
                        "lastfm_playcount": None,
                        "popularity": None,
                        "genres": [],
                        "is_excluded": key in excluded_artists,
                        "is_manual": True,
                    }
                else:
                    # Artist exists from sync but also in quiz - mark as manual too
                    if "quiz" not in merged_artists[key]["sources"]:
                        merged_artists[key]["sources"].append("quiz")
                    merged_artists[key]["is_manual"] = True

        artists = list(merged_artists.values())

        # Sort by "how well user knows the artist":
        # 1. lastfm_playcount (actual plays) - higher is better
        # 2. Best rank across sources - lower is better
        # 3. Number of sources - more sources = more confident
        def sort_key(a: dict) -> tuple[int, int, int]:
            playcount = a.get("lastfm_playcount") or 0
            # Use best rank from any source
            ranks = [r for r in [a.get("spotify_rank"), a.get("lastfm_rank")] if r]
            best_rank = min(ranks) if ranks else 9999
            num_sources = len(a.get("sources", []))
            return (-playcount, best_rank, -num_sources)

        artists.sort(key=sort_key)

        # Pagination
        total = len(artists)
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_artists = artists[start_idx:end_idx]

        return {
            "artists": paginated_artists,
            "total": total,
            "page": page,
            "per_page": per_page,
            "has_more": end_idx < total,
        }

    async def add_artist(self, user_id: str, artist_name: str) -> dict[str, Any]:
        """Add an artist manually to user's preferences.

        Stores in quiz_artists_known array in user document.

        Returns:
            Updated list of quiz/manual artists
        """
        # Find the user document to get the correct document ID
        user_doc, doc_id = await self._get_user_document(user_id)

        if doc_id:
            # Use ArrayUnion to add without duplicates
            await (
                self.firestore.collection("decide_users")
                .document(doc_id)
                .update(
                    {
                        "quiz_artists_known": ArrayUnion([artist_name]),
                        "updated_at": datetime.now(UTC).isoformat(),
                    }
                )
            )
        else:
            # Create user document if it doesn't exist
            await self.firestore.set_document(
                "decide_users",
                user_id,
                {
                    "user_id": user_id,
                    "quiz_artists_known": [artist_name],
                    "updated_at": datetime.now(UTC).isoformat(),
                },
                merge=True,
            )

        # Return updated artist list
        user_doc, _ = await self._get_user_document(user_id)
        return {
            "artists": user_doc.get("quiz_artists_known", []) if user_doc else [],
            "added": artist_name,
        }

    async def remove_artist(self, user_id: str, artist_name: str) -> dict[str, Any]:
        """Remove an artist from user's preferences.

        Removes from:
        - quiz_artists_known array (for quiz/manual artists)
        - user_artists collection (for synced artists)
        """
        removed_from: list[str] = []

        # Find the user document to get the correct document ID
        user_doc, doc_id = await self._get_user_document(user_id)

        # Remove from quiz_artists_known (case-insensitive)
        if user_doc and doc_id:
            quiz_artists = user_doc.get("quiz_artists_known", [])
            # Find matching artist (case-insensitive)
            matching = [a for a in quiz_artists if a.lower() == artist_name.lower()]
            if matching:
                await (
                    self.firestore.collection("decide_users")
                    .document(doc_id)
                    .update(
                        {
                            "quiz_artists_known": ArrayRemove(matching),
                            "updated_at": datetime.now(UTC).isoformat(),
                        }
                    )
                )
                removed_from.append("quiz")

        # Remove from user_artists collection
        synced_artists = await self.firestore.query_documents(
            collection="user_artists",
            filters=[("user_id", "==", user_id)],
        )
        for artist in synced_artists:
            if artist.get("artist_name", "").lower() == artist_name.lower():
                await self.firestore.delete_document("user_artists", artist["id"])
                source = artist.get("source", "synced")
                if source not in removed_from:
                    removed_from.append(source)

        return {
            "removed": artist_name,
            "removed_from": removed_from,
            "success": len(removed_from) > 0,
        }

    async def exclude_artist(self, user_id: str, artist_name: str) -> dict[str, Any]:
        """Exclude an artist from recommendations.

        Adds artist name to excluded_artists array in user document.
        This is a soft hide - the artist data is preserved but won't
        be used for recommendations. Persists through re-syncs.

        Args:
            user_id: User ID
            artist_name: Artist name to exclude (case-insensitive storage)

        Returns:
            Success status and artist name
        """
        user_doc, doc_id = await self._get_user_document(user_id)

        # Normalize for consistent storage
        artist_lower = artist_name.lower()

        if doc_id:
            # Check if already excluded
            excluded = user_doc.get("excluded_artists", []) if user_doc else []
            if artist_lower not in [a.lower() for a in excluded]:
                await (
                    self.firestore.collection("decide_users")
                    .document(doc_id)
                    .update(
                        {
                            "excluded_artists": ArrayUnion([artist_lower]),
                            "updated_at": datetime.now(UTC).isoformat(),
                        }
                    )
                )
        else:
            # Create user document with exclusion
            await self.firestore.set_document(
                "decide_users",
                user_id,
                {
                    "user_id": user_id,
                    "excluded_artists": [artist_lower],
                    "updated_at": datetime.now(UTC).isoformat(),
                },
                merge=True,
            )

        return {
            "artist_name": artist_name,
            "excluded": True,
            "success": True,
        }

    async def include_artist(self, user_id: str, artist_name: str) -> dict[str, Any]:
        """Remove an artist from exclusions (un-hide).

        Removes artist name from excluded_artists array in user document.

        Args:
            user_id: User ID
            artist_name: Artist name to include (case-insensitive matching)

        Returns:
            Success status and artist name
        """
        user_doc, doc_id = await self._get_user_document(user_id)

        if not user_doc or not doc_id:
            return {
                "artist_name": artist_name,
                "excluded": False,
                "success": True,  # Already not excluded
            }

        # Find matching exclusion (case-insensitive)
        excluded = user_doc.get("excluded_artists", [])
        matching = [a for a in excluded if a.lower() == artist_name.lower()]

        if matching:
            await (
                self.firestore.collection("decide_users")
                .document(doc_id)
                .update(
                    {
                        "excluded_artists": ArrayRemove(matching),
                        "updated_at": datetime.now(UTC).isoformat(),
                    }
                )
            )

        return {
            "artist_name": artist_name,
            "excluded": False,
            "success": True,
        }

    async def get_excluded_artists(self, user_id: str) -> list[str]:
        """Get list of excluded artist names for a user.

        Returns:
            List of excluded artist names (lowercase)
        """
        user_doc, _ = await self._get_user_document(user_id)
        if not user_doc:
            return []
        excluded: list[str] = user_doc.get("excluded_artists", [])
        return excluded


# Singleton pattern with lazy initialization
_user_data_service: UserDataService | None = None


def get_user_data_service(firestore: FirestoreService) -> UserDataService:
    """Get or create UserDataService instance."""
    global _user_data_service
    if _user_data_service is None:
        _user_data_service = UserDataService(firestore)
    return _user_data_service
