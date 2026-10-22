from pathlib import Path

import pytest

from karaoke_decide.vocal_range.ultrastar import (
    UltraStarNote,
    note_stats,
    parse_ultrastar,
)

FIX = Path(__file__).parents[2] / "fixtures" / "vocal_range" / "ultrastar"


def load(name: str):
    return parse_ultrastar((FIX / name).read_bytes())


def pitch_len(song, part="all"):
    return sorted((n.pitch, n.length) for n in song.parts[part])


def test_solo_stats():
    song = load("solo_utf8.txt")
    assert song is not None
    assert (song.artist, song.title) == ("The Lalas", "Doo Wah Day")
    assert (song.language, song.year, song.bpm, song.gap_ms) == ("English", 1999, 120.0, 1500.0)
    # Weights by pitch: 0 -> 4, 4 -> 8+2+2 = 12, 7 -> 4+4+2 = 10; total 26.
    # MIDI 60 (cum 4), 64 (cum 16), 67 (cum 26). Nearest rank:
    # p5 1.3 -> 60; p10 2.6 -> 60; p50 13 -> 64; p90 23.4 -> 67; p95 24.7 -> 67.
    st = note_stats(song.parts["all"])
    assert st is not None
    assert (st.low, st.p10, st.p50, st.p90, st.high) == (60, 60, 64, 67, 67)
    assert st.span == 7 and st.sung_beats == 26
    assert (st.raw_min, st.raw_max) == (60, 67)


def test_weights_1_1_2_4():
    notes = [
        UltraStarNote(0, 1, 0, ":"),
        UltraStarNote(1, 1, 2, ":"),
        UltraStarNote(2, 2, 4, ":"),
        UltraStarNote(4, 4, 7, ":"),
    ]
    # cum weights 1,2,4,8 over MIDI 60,62,64,67; total 8.
    # p5 .4 -> 60; p10 .8 -> 60; p50 4 -> 64 (cum 4 >= 4); p90 7.2 -> 67; p95 7.6 -> 67
    st = note_stats(notes)
    assert st is not None
    assert (st.low, st.p10, st.p50, st.p90, st.high) == (60, 60, 64, 67, 67)
    assert st.span == 7 and st.sung_beats == 8
    assert (st.raw_min, st.raw_max) == (60, 67)


def test_note_stats_empty():
    assert note_stats([]) is None


def test_cp1252_title():
    song = load("solo_cp1252.txt")
    assert song is not None and song.title == "Café Doo"


def test_freestyle_rap_golden():
    song = load("freestyle_rap_golden.txt")
    assert song is not None
    assert pitch_len(song) == [(0, 4), (5, 4)]
    assert {n.kind for n in song.parts["all"]} == {":", "*"}
    st = note_stats(song.parts["all"])
    assert st is not None and st.high == 65 and st.sung_beats == 8
    assert (st.raw_min, st.raw_max) == (60, 65)


def test_relative_parses():
    rel, absolute = load("relative.txt"), load("solo_utf8.txt")
    assert rel is not None and absolute is not None
    assert rel.relative is True and absolute.relative is False
    assert pitch_len(rel) == pitch_len(absolute)
    assert [n.beat for n in rel.parts["all"]] == [n.beat for n in absolute.parts["all"]]


def test_duet_parts():
    song = load("duet_p1_p2.txt")
    assert song is not None
    assert song.parts.keys() == {"P1", "P2"}
    assert pitch_len(song, "P1") == [(0, 4), (2, 4), (3, 4)]
    assert pitch_len(song, "P2") == [(12, 4), (14, 4)]


def test_video_id_extracted():
    raw = b"#TITLE:T\n#ARTIST:A\n#BPM:100\n#VIDEO:a=abc123XYZ_-,co=cover.jpg\n: 0 4 0 la\nE\n"
    song = parse_ultrastar(raw)
    assert song is not None and song.video_id == "abc123XYZ_-"
    assert load("solo_utf8.txt").video_id is None  # type: ignore[union-attr]


def test_broken_returns_none():
    assert load("broken_no_bpm.txt") is None
    assert parse_ultrastar(b"#BPM:100\n: 0 4 0 la\n") is None  # no artist/title
    assert parse_ultrastar(b"#TITLE:T\n#ARTIST:A\n#BPM:100\nR 0 4 0 la\nE\n") is None  # no singable


def test_comma_bpm_and_bom_encoding_tag():
    song = load("bom_encoding_tag.txt")
    assert song is not None
    assert song.bpm == pytest.approx(90.5)
    assert song.title == "Bom Doo"


BASE = b"#TITLE:T\n#ARTIST:A\n#BPM:100\n: 0 4 0 la\nE\n"


def test_never_raises_on_malformed_input():
    import random

    song = parse_ultrastar(BASE.replace(b"#BPM:100", "#BPM:100\n#YEAR:\u00b2".encode()))
    assert song is not None and song.year is None
    assert parse_ultrastar(BASE.replace(b"100", b"fast")) is None
    for bad in (b"nan", b"inf", b"-inf", b"1e999"):
        assert parse_ultrastar(BASE.replace(b"100", bad)) is None
    assert parse_ultrastar(BASE.replace(b"#BPM:100", b"#BPM:100\n#GAP:nan")) is not None
    assert parse_ultrastar(b"#ENCODING:\x00bogus\n" + BASE) is not None
    rng = random.Random(0)
    for _ in range(200):
        parse_ultrastar(bytes(rng.randrange(256) for _ in range(rng.randrange(0, 300))))
