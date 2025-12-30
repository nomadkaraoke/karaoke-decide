"""KaraokeNerds catalog client for Karaoke Decide."""

from typing import Any

import httpx

from karaoke_decide.core.exceptions import ExternalServiceError
from karaoke_decide.core.models import KaraokeSong, SongSource


class KaraokeNerdsClient:
    """Client for KaraokeNerds catalog."""

    # KaraokeNerds provides a JSON catalog export
    CATALOG_URL = "https://karaokenerds.com/api/songs"

    async def fetch_catalog(self) -> list[dict[str, Any]]:
        """Fetch the full song catalog from KaraokeNerds.

        Returns:
            List of song data dictionaries.
        """
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(self.CATALOG_URL)

            if response.status_code != 200:
                raise ExternalServiceError(
                    "KaraokeNerds",
                    f"Failed to fetch catalog: {response.status_code}",
                )

            return response.json()

    def parse_song(self, data: dict[str, Any]) -> KaraokeSong:
        """Parse a song from the KaraokeNerds catalog format.

        Args:
            data: Raw song data from the API.

        Returns:
            Parsed KaraokeSong model.
        """
        from slugify import slugify

        artist = data.get("artist", "").strip()
        title = data.get("title", "").strip()

        # Generate normalized ID
        song_id = slugify(f"{artist}-{title}", lowercase=True)

        return KaraokeSong(
            id=song_id,
            artist=artist,
            title=title,
            sources=[
                SongSource(
                    source="karaokenerds",
                    external_id=str(data.get("id", "")),
                    url=data.get("url"),
                )
            ],
            duration_ms=data.get("duration_ms"),
            genres=data.get("genres", []),
        )
