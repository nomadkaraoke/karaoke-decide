"""Library filter shared by the search service and recommendations.

``any`` is no filter; ``community`` keeps songs with a ``kind = 'community'`` version;
``karafun`` keeps songs in the KaraokeNerds brand ``KV`` library (KaraFun and Karaoke
Version are one catalogue). Same semantics as ``backend/services/recs_v2/filters.py``.
"""

from __future__ import annotations

from typing import Any, Literal

LibraryFilter = Literal["any", "community", "karafun"]

# Library filter value -> KaraokeNerds brand code.
LIBRARY_CODES: dict[str, str] = {"karafun": "KV"}


def library_clause(library: LibraryFilter, key_col: str = "song_key") -> tuple[str, list[str]]:
    """SQL condition (for ``availability.db``) restricting ``key_col`` to songs in ``library``.

    → ``("1", [])`` for ``any``. ``key_col`` must be a trusted column reference."""
    if library == "community":
        return f"EXISTS (SELECT 1 FROM versions lv WHERE lv.song_key = {key_col} AND lv.kind = 'community')", []
    if library == "karafun":
        return (
            f"EXISTS (SELECT 1 FROM song_libraries sl WHERE sl.song_key = {key_col} AND sl.library_code = ?)",
            [LIBRARY_CODES["karafun"]],
        )
    return "1", []


def group_matches_library(group: dict[str, Any], library: LibraryFilter) -> bool:
    """Same test on an already-loaded group dict (all versions present)."""
    if library == "community":
        return any(v["kind"] == "community" for v in group["versions"])
    if library == "karafun":
        return any(v["brand_code"] == LIBRARY_CODES["karafun"] for v in group["versions"])
    return True
