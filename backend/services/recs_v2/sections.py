"""v2 sections: explained shelves for ``sort=best``, one flat ``all`` list otherwise.

``best``: songs go, in descending score order, into the first section whose predicate
they meet and which still has room (section limit and at most ``PER_ARTIST`` songs per
artist). A song lands in one section at most; songs that fit nowhere are dropped.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from backend.services.recs_v2.context import UserContextV2
from backend.services.recs_v2.filters import Sort
from backend.services.recs_v2.scoring import Scored

SECTION_LIMITS: dict[str, int] = {
    "songs_you_know": 10,
    "artists_you_love": 15,
    "because": 10,
    "crowd_pleasers": 15,
    "make_it": 10,
}
COLD_CROWD_PLEASERS = 30
PER_ARTIST = 3
BECAUSE_SECTIONS = 2
BECAUSE_MIN_CANDIDATES = 3
CROWD_MIN_POPULARITY = 70


@dataclass
class Section:
    id: str
    title_key: str
    reason_key: str
    title_params: dict[str, str] = field(default_factory=dict)
    songs: list[Scored] = field(default_factory=list)


@dataclass
class _Slot:
    section: Section
    limit: int
    accepts: Callable[[Scored], bool]
    per_artist: Counter[str] = field(default_factory=Counter)

    def offer(self, s: Scored) -> bool:
        if len(self.section.songs) >= self.limit or not self.accepts(s):
            return False
        if self.per_artist[s.c.artist_key] >= PER_ARTIST:
            return False
        self.per_artist[s.c.artist_key] += 1
        self.section.songs.append(s)
        return True


def _section(sid: str, key: str | None = None, **params: str) -> Section:
    k = key or sid
    return Section(
        id=sid, title_key=f"recs.section.{k}.title", reason_key=f"recs.section.{k}.reason", title_params=params
    )


def because_seeds(scored: list[Scored], ctx: UserContextV2) -> list[str]:
    """The ``BECAUSE_SECTIONS`` highest-weight seed artists with enough similar karaoke candidates."""
    counts = Counter(s.c.similar_via for s in scored if s.c.similar_via and s.c.has_karaoke)
    eligible = [k for k, n in counts.items() if n >= BECAUSE_MIN_CANDIDATES and k in ctx.artists]
    eligible.sort(key=lambda k: (-ctx.artists[k].weight, k))
    return eligible[:BECAUSE_SECTIONS]


def _similar_to(seed: str) -> Callable[[Scored], bool]:
    return lambda s: s.c.has_karaoke and s.c.similar_via == seed


def _best(scored: list[Scored], ctx: UserContextV2) -> list[Section]:
    cold = ctx.is_cold

    def crowd(s: Scored) -> bool:
        if not s.c.has_karaoke:
            return False
        return cold or s.c.is_classic or (s.c.popularity or 0) >= CROWD_MIN_POPULARITY

    slots = [
        _Slot(
            _section("songs_you_know"),
            SECTION_LIMITS["songs_you_know"],
            lambda s: s.c.has_karaoke and ctx.known_songs.get(s.c.song_key, 0) > 0,
        ),
        _Slot(
            _section("artists_you_love"),
            SECTION_LIMITS["artists_you_love"],
            lambda s: s.c.has_karaoke and s.c.artist_key in ctx.artists and ctx.artists[s.c.artist_key].weight > 0,
        ),
    ]
    for seed in because_seeds(scored, ctx):
        slots.append(
            _Slot(
                _section(f"because_{seed}", "because", artist=ctx.artists[seed].name),
                SECTION_LIMITS["because"],
                _similar_to(seed),
            )
        )
    slots.append(
        _Slot(
            _section("crowd_pleasers"),
            COLD_CROWD_PLEASERS if cold else SECTION_LIMITS["crowd_pleasers"],
            crowd,
        )
    )
    slots.append(_Slot(_section("make_it"), SECTION_LIMITS["make_it"], lambda s: not s.c.has_karaoke))

    for s in sorted(scored, key=lambda s: (-s.score, s.c.song_key)):
        if s.c.song_key in ctx.hidden:
            continue
        for slot in slots:
            if slot.offer(s):
                break
    return [slot.section for slot in slots if slot.section.songs]


def _flat_key(sort: Sort) -> Callable[[Scored], tuple[object, ...]]:
    """Python twin of ``filters.ORDER_SQL`` (NULLS LAST, ``song_key`` tiebreak)."""

    def nulls_last(v: float | int | None, desc: bool) -> tuple[bool, float]:
        return (v is None, 0.0 if v is None else (-v if desc else v))

    if sort == "popular":
        return lambda s: (*nulls_last(s.c.popularity, True), s.c.song_key)
    if sort == "newest":
        return lambda s: (*nulls_last(s.c.year, True), s.c.song_key)
    if sort == "oldest":
        return lambda s: (*nulls_last(s.c.year, False), s.c.song_key)
    if sort == "shortest":
        return lambda s: (*nulls_last(s.c.duration_ms, False), s.c.song_key)
    if sort == "az":
        return lambda s: (s.c.title.casefold(), s.c.artist.casefold(), s.c.song_key)
    raise ValueError(f"no flat order for sort {sort!r}")


def _flat(scored: list[Scored], ctx: UserContextV2, sort: Sort) -> list[Section]:
    section = _section("all")
    per_artist: Counter[str] = Counter()
    for s in sorted(scored, key=_flat_key(sort)):
        if s.c.song_key in ctx.hidden or per_artist[s.c.artist_key] >= PER_ARTIST:
            continue
        per_artist[s.c.artist_key] += 1
        section.songs.append(s)
    return [section] if section.songs else []


def build_sections(scored: list[Scored], ctx: UserContextV2, sort: Sort) -> list[Section]:
    return _best(scored, ctx) if sort == "best" else _flat(scored, ctx, sort)
