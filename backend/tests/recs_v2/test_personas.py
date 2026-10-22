"""Personas on the fixture catalog: different users must get meaningfully different picks."""

from __future__ import annotations

from backend.services.recs_v2.candidates import gather_candidates
from backend.services.recs_v2.context import ArtistSignal, UserContextV2
from backend.services.recs_v2.filters import RecFilters
from backend.services.recs_v2.scoring import Scored, score
from backend.services.recs_v2.sections import Section, build_sections
from karaoke_decide.catalog.reader import CatalogReader


def _run(reader: CatalogReader, ctx: UserContextV2) -> tuple[list[Scored], list[Section]]:
    scored = sorted(
        (score(c, ctx) for c in gather_candidates(reader, ctx, RecFilters())),
        key=lambda s: (-s.score, s.c.song_key),
    )
    return scored, build_sections(scored, ctx, "best")


def _rock_artists(reader: CatalogReader, n: int) -> dict[str, ArtistSignal]:
    """The ``n`` artists with the most songs, all of them tagged rock."""
    rows = reader.conn().execute(
        """
        SELECT s.artist_key, MIN(s.artist) AS name, COUNT(*) AS n FROM songs s
        GROUP BY s.artist_key
        HAVING SUM(EXISTS (SELECT 1 FROM song_genres g WHERE g.song_key = s.song_key AND g.genre_id = 'rock')) = COUNT(*)
        ORDER BY n DESC, s.artist_key LIMIT ?
        """,
        (n,),
    )
    return {r["artist_key"]: ArtistSignal(0.9, "quiz", r["name"]) for r in rows}


def persona_a(reader: CatalogReader) -> UserContextV2:
    """Rock, 1990s, high energy, loves two rock artists."""
    artists = _rock_artists(reader, 2)
    assert len(artists) == 2
    return UserContextV2(user_id="a", artists=artists, genres={"rock"}, decades={"1990s"}, energy_pref="high")


def persona_b() -> UserContextV2:
    """Pop, 2010s, chill, no artists (cold)."""
    return UserContextV2(user_id="b", genres={"pop"}, decades={"2010s"}, energy_pref="chill")


def persona_c(reader: CatalogReader) -> UserContextV2:
    """Last.fm-style heavy listener: 50 ranked top artists and 300 known songs."""
    c = reader.conn()
    top = [r[0] for r in c.execute("SELECT artist_key FROM artists ORDER BY lb_listeners DESC, artist_key LIMIT 50")]
    artists = {k: ArtistSignal(max(0.5, 1 - rank / 200), "lastfm", k.title()) for rank, k in enumerate(top, 1)}
    marks = ",".join("?" * len(top))
    rows = c.execute(
        f"""
        SELECT song_key FROM songs ORDER BY (artist_key IN ({marks})) DESC, lb_listeners DESC, song_key LIMIT 300
        """,
        top,
    )
    known = {r[0]: (1.0, 0.7, 0.4)[i % 3] for i, r in enumerate(rows)}
    assert len(known) == 300
    return UserContextV2(user_id="c", artists=artists, known_songs=known)


def _top(scored: list[Scored], n: int) -> list[Scored]:
    return scored[:n]


def test_rock_and_pop_personas_differ(reader: CatalogReader) -> None:
    a, _ = _run(reader, persona_a(reader))
    b, _ = _run(reader, persona_b())
    overlap = {s.c.song_key for s in _top(a, 20)} & {s.c.song_key for s in _top(b, 20)}
    assert len(overlap) <= 4, overlap


def test_rock_persona_gets_rock(reader: CatalogReader) -> None:
    a, _ = _run(reader, persona_a(reader))
    assert sum("rock" in s.c.genres for s in _top(a, 10)) >= 6


def test_pop_persona_gets_2010s_pop(reader: CatalogReader) -> None:
    b, _ = _run(reader, persona_b())
    top = _top(b, 10)
    assert sum("pop" in s.c.genres for s in top) >= 6
    assert sum(s.c.decade == "2010s" for s in top) >= 5


def test_heavy_profile_leads_with_songs_you_know(reader: CatalogReader) -> None:
    _, sections = _run(reader, persona_c(reader))
    assert sections[0].id == "songs_you_know"
    assert all(s.reason_type == "known_song" for s in sections[0].songs)


def test_heavy_profile_still_discovers(reader: CatalogReader) -> None:
    """Known songs stay in "Songs you know" (or Make it); the rest of the page is new to the user."""
    ctx = persona_c(reader)
    _, sections = _run(reader, ctx)
    by_id = {s.id: s for s in sections}
    for sid in ("artists_you_love", "crowd_pleasers"):
        assert by_id[sid].songs, sid
        assert not [s.c.song_key for s in by_id[sid].songs if s.c.song_key in ctx.known_songs], sid
    for sec in sections:
        if sec.id.startswith("because_"):
            assert not [s for s in sec.songs if s.c.song_key in ctx.known_songs]
    unknown = {s.c.song_key for sec in sections for s in sec.songs if s.c.song_key not in ctx.known_songs}
    assert len(unknown) >= 25


def test_every_persona_gets_crowd_pleasers(reader: CatalogReader) -> None:
    for ctx in (persona_a(reader), persona_b(), persona_c(reader)):
        _, sections = _run(reader, ctx)
        crowd = [s for s in sections if s.id == "crowd_pleasers"]
        assert crowd and crowd[0].songs, ctx.user_id


def test_hidden_songs_never_recommended(reader: CatalogReader) -> None:
    ctx = persona_a(reader)
    first, _ = _run(reader, ctx)
    ctx.hidden = {s.c.song_key for s in first[:5]}
    again, sections = _run(reader, ctx)
    assert not ctx.hidden & {s.c.song_key for s in again}
    assert not ctx.hidden & {s.c.song_key for sec in sections for s in sec.songs}
