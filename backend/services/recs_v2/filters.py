"""Recommendation v2 filters and sorts, expressed as parameterised SQL.

Filters apply to ``catalog.db``'s ``songs`` table (aliased ``s``); availability-based
filters read the search service's ``availability.db`` ATTACHed as ``av``.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from karaoke_decide.catalog.moods import ENERGY_BANDS
from karaoke_decide.song_search.library_filter import library_clause

# Must stay equal to karaoke_decide.catalog.moods.MOODS (asserted in tests).
Mood = Literal["happy", "chill", "sad", "intense"]
Sort = Literal["best", "popular", "newest", "oldest", "shortest", "az"]


SHORT_MS = 180_000  # short < 180000, medium 180000..300000 inclusive, long > 300000
LONG_MS = 300_000

ORDER_SQL: dict[Sort, str] = {
    "popular": "popularity DESC NULLS LAST, s.song_key",
    "newest": "year DESC NULLS LAST, s.song_key",
    "oldest": "year ASC NULLS LAST, s.song_key",
    "shortest": "duration_ms ASC NULLS LAST, s.song_key",
    "az": "title COLLATE NOCASE, artist COLLATE NOCASE, s.song_key",
}  # "best" is ranked in Python by the scorer

_ALIAS = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class RecFilters(BaseModel):
    genre: list[str] = []
    mood: list[Mood] = []
    energy: Literal["low", "mid", "high"] | None = None
    decade: list[str] = []
    popularity: int | None = Field(default=None, ge=0, le=100)
    popularity_max: int | None = Field(default=None, ge=0, le=100)
    lead_gender: Literal["male", "female", "mixed"] | None = None
    duration: Literal["short", "medium", "long"] | None = None
    classic: bool = False
    explicit: Literal["exclude", "include"] = "include"
    has_karaoke: Literal["yes", "make", "any"] = "any"
    library: Literal["any", "community", "karafun"] = "any"


def _in(col: str, values: list[str], params: list[object]) -> str:
    params.extend(values)
    return f"{col} IN ({','.join('?' * len(values))})"


def where_sql(f: RecFilters, alias: str = "s", *, has_availability: bool = True) -> tuple[str, list[object]]:
    """``(condition, params)`` for ``WHERE``; ``"1"`` when no filter is active.

    Without ``availability.db`` (``has_availability`` false) the karaoke / library filters
    fall back to the ``in_kn`` flag (approximate; see ``filters_applied``).
    """
    if not _ALIAS.match(alias):
        raise ValueError(f"invalid SQL alias: {alias!r}")
    a = alias
    conds: list[str] = []
    p: list[object] = []

    if f.genre:
        marks = ",".join("?" * len(f.genre))
        conds.append(
            f"EXISTS (SELECT 1 FROM song_genres g WHERE g.song_key = {a}.song_key AND g.genre_id IN ({marks}))"
        )
        p.extend(f.genre)
    if f.mood:
        conds.append(_in(f"{a}.mood", list(f.mood), p))
    if f.energy:
        lo, hi = ENERGY_BANDS[f.energy]
        conds.append(f"{a}.energy >= ? AND {a}.energy < ?")
        p.extend([lo, hi])
    if f.decade:
        conds.append(_in(f"{a}.decade", f.decade, p))
    if f.popularity is not None:
        conds.append(f"{a}.popularity >= ?")
        p.append(f.popularity)
    if f.popularity_max is not None:
        conds.append(f"{a}.popularity <= ?")
        p.append(f.popularity_max)
    if f.lead_gender:
        conds.append(f"{a}.lead_gender = ?")
        p.append(f.lead_gender)
    if f.duration == "short":
        conds.append(f"{a}.duration_ms < ?")
        p.append(SHORT_MS)
    elif f.duration == "medium":
        conds.append(f"{a}.duration_ms >= ? AND {a}.duration_ms <= ?")
        p.extend([SHORT_MS, LONG_MS])
    elif f.duration == "long":
        conds.append(f"{a}.duration_ms > ?")
        p.append(LONG_MS)
    if f.classic:
        conds.append(f"{a}.is_classic = 1")
    if f.explicit == "exclude":
        conds.append(f"COALESCE({a}.explicit, 0) = 0")

    if has_availability:
        if f.has_karaoke == "yes":
            conds.append(f"EXISTS (SELECT 1 FROM av.songs x WHERE x.song_key = {a}.song_key)")
        elif f.has_karaoke == "make":
            conds.append(f"NOT EXISTS (SELECT 1 FROM av.songs x WHERE x.song_key = {a}.song_key)")
        if f.library != "any":
            clause, lib_params = library_clause(f.library, f"{a}.song_key", schema="av")
            conds.append(clause)
            p.extend(lib_params)
    else:
        if f.has_karaoke == "make":
            conds.append(f"COALESCE({a}.in_kn, 0) = 0")
        elif f.has_karaoke == "yes" or f.library != "any":
            conds.append(f"{a}.in_kn = 1")

    return (" AND ".join(f"({c})" for c in conds) or "1"), p


def filters_applied(f: RecFilters, *, has_availability: bool = True) -> dict[str, object]:
    """Only the non-default filter values, for the response echo.

    ``availability: "approximate"`` is added when a karaoke/library filter was answered
    from ``in_kn`` because ``availability.db`` isn't loaded."""
    defaults = RecFilters()
    out: dict[str, object] = {
        name: getattr(f, name) for name in RecFilters.model_fields if getattr(f, name) != getattr(defaults, name)
    }
    if not has_availability and ("has_karaoke" in out or "library" in out):
        out["availability"] = "approximate"
    return out
