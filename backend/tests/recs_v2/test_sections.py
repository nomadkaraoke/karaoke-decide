"""v2 sections: order, caps, single placement, flat sorts, known songs shown."""

from __future__ import annotations

from collections import Counter

import pytest

from backend.services.recs_v2.candidates import gather_candidates
from backend.services.recs_v2.context import ArtistSignal, UserContextV2
from backend.services.recs_v2.filters import RecFilters
from backend.services.recs_v2.scoring import Scored, score
from backend.services.recs_v2.sections import SECTION_LIMITS, Section, build_sections
from backend.tests.recs_v2.factories import cand
from karaoke_decide.catalog.reader import CatalogReader

ORDER = ["go_tos", "songs_you_know", "artists_you_love", "because", "crowd_pleasers", "make_it"]


def _rank(sid: str) -> int:
    return ORDER.index("because" if sid.startswith("because_") else sid)


def _ctx() -> UserContextV2:
    """Two loved artists (each with similar artists), several known songs."""
    return UserContextV2(
        user_id="u",
        artists={
            "loved1": ArtistSignal(0.9, "quiz", "Loved One"),
            "loved2": ArtistSignal(0.7, "quiz", "Loved Two"),
            "loved3": ArtistSignal(0.5, "quiz", "Loved Three"),
        },
        known_songs={f"loved1|k{i}": 1.0 for i in range(6)} | {"other|k": 0.7},
        genres={"rock"},
    )


def _scored(ctx: UserContextV2) -> list[Scored]:
    cs = [cand(f"loved1|k{i}", "Loved1") for i in range(6)]
    cs += [cand("other|k", "Other")]
    cs += [cand(f"loved1|s{i}", "Loved1") for i in range(5)]
    cs += [cand(f"loved2|s{i}", "Loved2") for i in range(5)]
    for seed, n in (("loved1", 5), ("loved2", 4), ("loved3", 6)):
        cs += [cand(f"near{seed}{i}|s", f"Near{seed}{i}", similar_via=seed, similar_score=0.5) for i in range(n)]
    cs += [cand(f"crowd{i}|s", f"Crowd{i}", popularity=80) for i in range(4)]
    cs += [cand(f"deep{i}|s", f"Deep{i}", popularity=20) for i in range(4)]  # not crowd pleasers (warm user)
    cs += [cand(f"loved1|m{i}", "Loved1", has_karaoke=False) for i in range(5)]
    return [score(c, ctx) for c in cs]


def _keys(sections: list[Section]) -> list[str]:
    return [s.c.song_key for sec in sections for s in sec.songs]


def test_no_song_in_two_sections() -> None:
    keys = _keys(build_sections(_scored(_ctx()), _ctx(), "best"))
    assert len(keys) == len(set(keys))


def test_at_most_three_per_artist_per_section() -> None:
    for sec in build_sections(_scored(_ctx()), _ctx(), "best"):
        assert max(Counter(s.c.artist_key for s in sec.songs).values()) <= 3, sec.id


def test_section_order_and_ids() -> None:
    sections = build_sections(_scored(_ctx()), _ctx(), "best")
    ids = [s.id for s in sections]
    assert ids == sorted(ids, key=_rank)
    # loved3 has the most similar candidates but loved1 / loved2 weigh more; loved2 has >= 3.
    assert ids == [
        "songs_you_know",
        "artists_you_love",
        "because_loved1",
        "because_loved2",
        "crowd_pleasers",
        "make_it",
    ]
    because = sections[2]
    assert (because.title_key, because.title_params) == ("recs.section.because.title", {"artist": "Loved One"})
    assert all(s.c.similar_via == "loved1" for s in because.songs)
    crowd = next(s for s in sections if s.id == "crowd_pleasers")
    assert crowd.title_key == "recs.section.crowd_pleasers.title"
    assert all((s.c.popularity or 0) >= 70 or s.c.is_classic for s in crowd.songs)
    assert all(not s.c.has_karaoke for s in sections[-1].songs)


def test_because_needs_three_similar_candidates() -> None:
    ctx = _ctx()
    scored = [s for s in _scored(ctx) if not s.c.song_key.startswith("nearloved2") or s.c.song_key < "nearloved22"]
    ids = [s.id for s in build_sections(scored, ctx, "best")]
    assert "because_loved2" not in ids
    assert "because_loved3" in ids


def test_empty_sections_are_omitted() -> None:
    ctx = UserContextV2(user_id="u", genres={"rock"})
    sections = build_sections([score(cand(f"crowd{i}|s", f"Crowd{i}"), ctx) for i in range(5)], ctx, "best")
    assert [s.id for s in sections] == ["crowd_pleasers"]
    assert build_sections([], ctx, "best") == []


def test_section_limits() -> None:
    ctx = _ctx()
    scored = [score(cand(f"a{i}|s", f"A{i}", popularity=90), ctx) for i in range(50)]
    (crowd,) = build_sections(scored, ctx, "best")
    assert len(crowd.songs) == SECTION_LIMITS["crowd_pleasers"] == 15
    cold = UserContextV2(user_id="u")
    (crowd,) = build_sections([score(cand(f"a{i}|s", f"A{i}"), cold) for i in range(50)], cold, "best")
    assert len(crowd.songs) == 30  # cold users get a longer list, any popularity


def test_known_songs_are_shown_and_labelled() -> None:
    ctx = _ctx()
    sections = build_sections(_scored(ctx), ctx, "best")
    first = sections[0]
    assert first.id == "songs_you_know"
    assert first.title_key == "recs.section.songs_you_know.title"
    assert first.reason_key == "recs.section.songs_you_know.reason"
    assert {s.reason_type for s in first.songs} == {"known_song"}
    assert "other|k" in {s.c.song_key for s in first.songs}


def test_hidden_songs_never_appear() -> None:
    ctx = _ctx()
    ctx.hidden = {"loved1|k0", "crowd0|s"}
    assert not {"loved1|k0", "crowd0|s"} & set(_keys(build_sections(_scored(ctx), ctx, "best")))


@pytest.mark.parametrize(
    ("sort", "field", "reverse"),
    [
        ("popular", "popularity", True),
        ("newest", "year", True),
        ("oldest", "year", False),
        ("shortest", "duration_ms", False),
    ],
)
def test_flat_sorts_give_one_all_section(sort: str, field: str, reverse: bool) -> None:
    ctx = _ctx()
    scored = [
        score(cand(f"a{i}|s", f"A{i}", popularity=i, year=1960 + i, duration_ms=100_000 + i), ctx) for i in range(20)
    ]
    scored.append(score(cand("z|null", "Z", popularity=None, year=None, duration_ms=None), ctx))
    (sec,) = build_sections(scored, ctx, sort)  # type: ignore[arg-type]
    assert (sec.id, sec.title_key) == ("all", "recs.section.all.title")
    values = [getattr(s.c, field) for s in sec.songs]
    assert values[-1] is None  # NULLS LAST
    assert values[:-1] == sorted(values[:-1], reverse=reverse)
    assert len(sec.songs) == 21


def test_az_sort() -> None:
    ctx = _ctx()
    scored = [score(cand(f"x|{t}", "X" + t, title=t), ctx) for t in ("beta", "Alpha", "gamma")]
    (sec,) = build_sections(scored, ctx, "az")
    assert [s.c.title for s in sec.songs] == ["Alpha", "beta", "gamma"]


def test_real_catalog_sections(reader: CatalogReader) -> None:
    """End to end on the fixture catalog: invariants hold for a warm user."""
    rock = [
        r[0] for r in reader.conn().execute("SELECT artist_key FROM artists WHERE genres_json LIKE '%rock%' LIMIT 3")
    ]
    ctx = UserContextV2(user_id="u", artists={k: ArtistSignal(0.9, "quiz", k.title()) for k in rock}, genres={"rock"})
    scored = [score(c, ctx) for c in gather_candidates(reader, ctx, RecFilters())]
    sections = build_sections(scored, ctx, "best")
    keys = _keys(sections)
    assert len(keys) == len(set(keys))
    assert [s.id for s in sections] == sorted((s.id for s in sections), key=_rank)
    for sec in sections:
        assert max(Counter(s.c.artist_key for s in sec.songs).values()) <= 3


def test_known_songs_only_in_songs_you_know_or_make_it() -> None:
    ctx = _ctx()
    ctx.known_songs |= {"loved1|m0": 1.0, "crowd0|s": 1.0, "nearloved10|s": 1.0}
    sections = build_sections(_scored(ctx), ctx, "best")
    for sec in sections:
        known = [s.c.song_key for s in sec.songs if ctx.known_songs.get(s.c.song_key, 0) > 0]
        if sec.id not in ("songs_you_know", "make_it"):
            assert known == [], sec.id
    make = next(s for s in sections if s.id == "make_it")
    assert "loved1|m0" in {s.c.song_key for s in make.songs}  # a known song with no karaoke version yet
    # loved1|k3..k5 overflowed songs_you_know's per-artist cap and are dropped, not moved to artists_you_love
    assert not {"loved1|k3", "loved1|k4", "loved1|k5"} & set(_keys(sections))


def test_because_counts_only_songs_left_after_earlier_sections() -> None:
    ctx = _ctx()
    ctx.known_songs |= {"nearloved20|s": 1.0, "nearloved21|s": 1.0}  # loved2: 4 similar, 2 known -> 2 left
    ids = [s.id for s in build_sections(_scored(ctx), ctx, "best")]
    assert "because_loved2" not in ids
    assert ids[2:4] == ["because_loved1", "because_loved3"]


def test_because_counts_per_artist_cap() -> None:
    ctx = _ctx()
    scored = _scored(ctx)
    scored = [s for s in scored if not s.c.song_key.startswith("nearloved2")]
    one_artist = [cand(f"nearloved2x|s{i}", "NearLoved2x", similar_via="loved2", similar_score=0.5) for i in range(5)]
    scored += [score(c, ctx) for c in one_artist]
    sections = build_sections(scored, ctx, "best")
    sec = next(s for s in sections if s.id == "because_loved2")  # 5 songs by one artist count as 3: eligible
    assert len(sec.songs) == 3


def test_go_tos_section_first_and_deduped() -> None:
    ctx = _ctx()
    ctx.go_tos = {"loved1|k0", "other|k", "loved1|m0", "crowd0|s"}
    ctx.known_songs |= {"loved1|m0": 1.0, "crowd0|s": 1.0}
    sections = build_sections(_scored(ctx), ctx, "best")
    go = sections[0]
    assert (go.id, go.title_key, go.reason_key) == (
        "go_tos",
        "recs.section.go_tos.title",
        "recs.section.go_tos.reason",
    )
    assert {s.c.song_key for s in go.songs} == {"loved1|k0", "other|k", "crowd0|s"}  # karaoke only
    assert {s.reason_type for s in go.songs} == {"sung_before"}
    know = next(s for s in sections if s.id == "songs_you_know")
    assert not {s.c.song_key for s in know.songs} & ctx.go_tos
    keys = _keys(sections)
    assert len(keys) == len(set(keys))
    assert SECTION_LIMITS["go_tos"] == 10
    assert [s.id for s in sections] == sorted((s.id for s in sections), key=_rank)


def test_go_tos_hidden_never_shown() -> None:
    ctx = _ctx()
    ctx.go_tos = {"loved1|k0"}
    ctx.hidden = {"loved1|k0"}
    assert "go_tos" not in [s.id for s in build_sections(_scored(ctx), ctx, "best")]


def test_survey_no_not_a_because_seed() -> None:
    ctx = _ctx()
    ctx.survey_no = {"nearloved20|s", "nearloved21|s"}  # loved2: 4 similar, 2 said no -> 2 left
    sections = build_sections(_scored(ctx), ctx, "best")
    ids = [s.id for s in sections]
    assert "because_loved2" not in ids
    assert ids[2:4] == ["because_loved1", "because_loved3"]
    for sec in sections:
        if sec.id.startswith("because_"):
            assert not {s.c.song_key for s in sec.songs} & ctx.survey_no


def test_go_to_overflow_falls_through_to_songs_you_know() -> None:
    """R21: go-tos past the go_tos per-artist cap land in songs_you_know (each song still placed once)."""
    ctx = _ctx()
    ctx.go_tos = {f"loved1|k{i}" for i in range(6)}
    sections = build_sections(_scored(ctx), ctx, "best")
    go = {s.c.song_key for s in sections[0].songs}
    assert sections[0].id == "go_tos" and len(go) == 3
    know = next(s for s in sections if s.id == "songs_you_know")
    spilled = {s.c.song_key for s in know.songs} & ctx.go_tos
    assert len(spilled) == 3 and not spilled & go
    assert {s.reason_type for s in know.songs if s.c.song_key in spilled} == {"sung_before"}
