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
    "go_tos": 10,
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


def because_seeds(remaining: list[Scored], ctx: UserContextV2) -> list[str]:
    """The ``BECAUSE_SECTIONS`` highest-weight seeds with at least ``BECAUSE_MIN_CANDIDATES``
    distinct eligible songs left once earlier sections took theirs (``PER_ARTIST`` cap applied)."""
    per_artist: Counter[tuple[str, str]] = Counter()
    counts: Counter[str] = Counter()
    for s in remaining:
        seed = s.c.similar_via
        if (
            not seed
            or not _discovery(s, ctx)
            or s.c.song_key in ctx.survey_no
            or per_artist[(seed, s.c.artist_key)] >= PER_ARTIST
        ):
            continue
        per_artist[(seed, s.c.artist_key)] += 1
        counts[seed] += 1
    eligible = [k for k, n in counts.items() if n >= BECAUSE_MIN_CANDIDATES and k in ctx.artists]
    eligible.sort(key=lambda k: (-ctx.artists[k].weight, k))
    return eligible[:BECAUSE_SECTIONS]


def _known(s: Scored, ctx: UserContextV2) -> bool:
    return ctx.known_songs.get(s.c.song_key, 0) > 0


def _discovery(s: Scored, ctx: UserContextV2) -> bool:
    """Karaoke songs the user doesn't know yet (discovery shelves never repeat known songs)."""
    return s.c.has_karaoke and not _known(s, ctx)


def _similar_to(seed: str, ctx: UserContextV2) -> Callable[[Scored], bool]:
    return lambda s: _discovery(s, ctx) and s.c.similar_via == seed and s.c.song_key not in ctx.survey_no


def _fill(slots: list[_Slot], songs: list[Scored]) -> list[Scored]:
    """Offer each song, in order, to the first slot that takes it; return the songs no slot took."""
    left: list[Scored] = []
    for s in songs:
        if not any(slot.offer(s) for slot in slots):
            left.append(s)
    return left


def _best(scored: list[Scored], ctx: UserContextV2) -> list[Section]:
    """Greedy placement in score order. Go-tos (karaoke songs in ``ctx.go_tos``) lead in their own
    shelf (overflow is dropped, never moved to ``songs_you_know``); other known songs only go to
    ``songs_you_know`` (or ``make_it`` when there is no karaoke version); the other shelves are for
    discovery. Survey-"bad" songs never fill a ``because_*`` shelf.

    Two passes give the same result as one (a song's fate in the first three shelves never depends on
    later ones), and the second pass picks ``because_*`` seeds from what is actually left."""
    cold = ctx.is_cold

    def crowd(s: Scored) -> bool:
        if not _discovery(s, ctx):
            return False
        return cold or s.c.is_classic or (s.c.popularity or 0) >= CROWD_MIN_POPULARITY

    ordered = [s for s in sorted(scored, key=lambda s: (-s.score, s.c.song_key)) if s.c.song_key not in ctx.hidden]
    first = [
        _Slot(
            _section("go_tos"),
            SECTION_LIMITS["go_tos"],
            lambda s: s.c.has_karaoke and s.c.song_key in ctx.go_tos,
        ),
        _Slot(
            _section("songs_you_know"),
            SECTION_LIMITS["songs_you_know"],
            lambda s: s.c.has_karaoke and _known(s, ctx) and s.c.song_key not in ctx.go_tos,
        ),
        _Slot(
            _section("artists_you_love"),
            SECTION_LIMITS["artists_you_love"],
            lambda s: _discovery(s, ctx) and s.c.artist_key in ctx.artists and ctx.artists[s.c.artist_key].weight > 0,
        ),
    ]
    remaining = _fill(first, ordered)

    rest = [
        _Slot(
            _section(f"because_{seed}", "because", artist=ctx.artists[seed].name),
            SECTION_LIMITS["because"],
            _similar_to(seed, ctx),
        )
        for seed in because_seeds(remaining, ctx)
    ]
    rest.append(
        _Slot(
            _section("crowd_pleasers"),
            COLD_CROWD_PLEASERS if cold else SECTION_LIMITS["crowd_pleasers"],
            crowd,
        )
    )
    rest.append(_Slot(_section("make_it"), SECTION_LIMITS["make_it"], lambda s: not s.c.has_karaoke))
    _fill(rest, remaining)
    return [slot.section for slot in [*first, *rest] if slot.section.songs]


BUCKET_RANK = {"fits": 0, "fitsIn": 1, "stretch": 2, "outside": 3}


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
    if sort == "fit":  # bucket first (an octave-shifted score never outranks a better bucket), then score; no fit last
        return lambda s: (
            s.c.fit is None,
            BUCKET_RANK[s.c.fit.bucket] if s.c.fit is not None else 0,
            s.c.fit.score if s.c.fit is not None else 0.0,
            *nulls_last(s.c.popularity, True),
            s.c.song_key,
        )
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
