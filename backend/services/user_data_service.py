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
            doc = await self.firestore.get_document("users", user_id)
            if doc:
                return doc, user_id
            return None, None

        # Verified users: query by user_id field
        docs = await self.firestore.query_documents(
            collection="users",
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
            await self.firestore.update_document("users", doc_id, update_data)
        else:
            # User document doesn't exist - create it with set+merge
            # This handles edge cases where user exists in JWT but not in Firestore
            update_data["user_id"] = user_id
            await self.firestore.set_document("users", user_id, update_data, merge=True)

        return await self.get_preferences(user_id)

    async def get_all_artists(self, user_id: str) -> list[dict[str, Any]]:
        """Get all artists for user from all sources.

        Combines:
        - user_artists collection (from Spotify/Last.fm sync)
        - quiz_artists_known from user profile (from quiz + manual additions)
        """
        artists: list[dict[str, Any]] = []

        # Get synced artists from user_artists collection
        synced_artists = await self.firestore.query_documents(
            collection="user_artists",
            filters=[("user_id", "==", user_id)],
        )
        for artist in synced_artists:
            artists.append(
                {
                    "artist_name": artist.get("artist_name"),
                    "source": artist.get("source"),
                    "rank": artist.get("rank", 0),
                    "time_range": artist.get("time_range") or artist.get("period", ""),
                    "popularity": artist.get("popularity"),
                    "genres": artist.get("genres", []),
                    "playcount": artist.get("playcount"),
                }
            )

        # Get quiz/manual artists from user profile
        user_doc, _ = await self._get_user_document(user_id)
        if user_doc:
            quiz_artists = user_doc.get("quiz_artists_known", [])
            for idx, artist_name in enumerate(quiz_artists):
                # Check if already in synced artists to avoid duplicates
                if not any(a["artist_name"].lower() == artist_name.lower() for a in artists):
                    artists.append(
                        {
                            "artist_name": artist_name,
                            "source": "quiz",  # Could be quiz or manual, stored together
                            "rank": idx + 1,
                            "time_range": "",
                            "popularity": None,
                            "genres": [],
                            "playcount": None,
                        }
                    )

        # Sort by "how well user knows the artist":
        # 1. playcount (actual plays from Last.fm) - higher is better
        # 2. rank (position in top list) - lower is better
        # 3. source priority (lastfm > spotify > quiz)
        def sort_key(a: dict) -> tuple[int, int, int]:
            playcount = a.get("playcount") or 0
            rank = a.get("rank") or 9999
            source_order = {"lastfm": 0, "spotify": 1, "quiz": 2}.get(a.get("source", "quiz"), 3)
            return (-playcount, rank, source_order)

        artists.sort(key=sort_key)
        return artists

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
                self.firestore.collection("users")
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
                "users",
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
                    self.firestore.collection("users")
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


# Singleton pattern with lazy initialization
_user_data_service: UserDataService | None = None


def get_user_data_service(firestore: FirestoreService) -> UserDataService:
    """Get or create UserDataService instance."""
    global _user_data_service
    if _user_data_service is None:
        _user_data_service = UserDataService(firestore)
    return _user_data_service
