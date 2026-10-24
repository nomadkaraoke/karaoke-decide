"""Sheet label parsing (invented notes only)."""

import pytest

from karaoke_decide.voice_session.labels import parse_good, parse_note, row_tags, rows_from_csv


@pytest.mark.parametrize(
    ("value", "expected"),
    [("y", "yes"), ("Yes", "yes"), ("good", "yes"), ("✓", "yes"), ("✔", "yes"), ("1", "yes"),
     ("n", "no"), ("NO", "no"), ("x", "no"), ("✗", "no"), ("0", "no"),
     ("sometimes", "maybe"), ("  ", None), ("", None), (None, None)],
)  # fmt: skip
def test_parse_good_variants(value: str | None, expected: str | None) -> None:
    assert parse_good(value) == expected


@pytest.mark.parametrize(
    ("note", "tags"),
    [
        ("needs a warm-up first", {"needs_warmup"}),
        ("Only after I warmed up", {"needs_warmup"}),
        ("good if warm", {"good_if_warm"}),
        ("only in the mood for it", {"mood_only"}),
        ("a bit flamboyant", {"mood_only"}),
        ("too low if I stay down, too high if I go up", {"octave_split"}),
        ("octave is mixed", {"section_octave_jump"}),
        ("verse and chorus sit apart", {"section_octave_jump"}),
        ("sing it a lower octave", {"key_change"}),
        ("raise the key a bit", {"key_change"}),
        ("needs practise", {"needs_practice"}),
        ("needs practice", {"needs_practice"}),
        ("no intro", {"no_intro"}),
        ("comes in immediately", {"no_intro"}),
        ("Funbox version is great", {"version_good"}),
        ("this version is good", {"version_good"}),
        ("yes if warm", {"good_if_warm"}),
        ("needs warm-up", {"needs_warmup"}),
        ("need to be a little warmed up but not too much", {"needs_warmup"}),
        ("only if feeling flamboyant", {"mood_only"}),
        ("too low if you sing it low, too high if high", {"octave_split"}),
        ("octave is mixed between verse and chorus", {"section_octave_jump"}),
        ("stay in the lower octave or try to raise the key", {"key_change"}),
        ("need to practice at home first", {"needs_practice"}),
        ("too hard without practice", {"needs_practice"}),
        ("comes in immediately with no intro time", {"no_intro"}),
        ("the key is too high", {"key_change"}),
        ("monkey turkey hockey", set()),
        ("", set()),
        ("lovely tune", set()),
    ],
)
def test_parse_note_examples(note: str, tags: set[str]) -> None:
    assert parse_note(note) == tags


def test_parse_note_multiple_tags() -> None:
    assert parse_note("needs practice and a warm-up, no intro") == {"needs_practice", "needs_warmup", "no_intro"}


def test_rows_from_csv_header_case() -> None:
    text = (
        "ARTIST,title,DUET?,Good?,sung?,NOTES\nThe Lanterns,Paper Harbour,y,Y,n,good if warm\n,,,,,\nAdele,Hello,,,,\n"
    )
    rows = rows_from_csv(text)
    assert len(rows) == 2
    r = rows[0]
    assert (r.artist, r.title, r.duet, r.good, r.sung, r.notes) == (
        "The Lanterns", "Paper Harbour", True, "Y", False, "good if warm",
    )  # fmt: skip
    assert rows[1].duet is None and rows[1].good is None and rows[1].notes == ""


_SHEET_GOOD = [
    ("yes", "yes"), ("yes!", "yes"), ("no", "no"), ("no!", "no"), ("meh", "maybe"), ("?", "maybe"),
    ("yes if warm", "yes"), ("maybe", "maybe"), ("no! avoid!", "no"), ("no! hard!", "no"),
    ("??", "maybe"), ("???", "maybe"), ("eh", "maybe"), ("ish", "maybe"), ("kinda", "maybe"),
    ("probably!", "yes"), ("maybe!", "maybe"), ("yes, if warm", "yes"), ("yes! if warmed up", "yes"),
    ("yes (but quiet)", "yes"), ("yes (if warmed up!)", "yes"), ("yes, ish", "maybe"),
    ("yes (ish)", "maybe"), ("yes (but stay in the lower octave…)", "yes"),
    ("yes (hard and super high, need warmup)", "yes"), ("", None),
]  # fmt: skip


@pytest.mark.parametrize(("value", "expected"), _SHEET_GOOD)
def test_parse_good_sheet_values(value: str, expected: str | None) -> None:
    assert parse_good(value) == expected


@pytest.mark.parametrize(
    ("good", "notes", "tags"),
    [
        ("yes if warm", "", {"good_if_warm"}),
        ("yes! if warmed up", "", {"needs_warmup", "good_if_warm"}),
        ("yes (but stay in the lower octave…)", "needs practice", {"key_change", "needs_practice"}),
        ("yes", "no intro", {"no_intro"}),
        ("yes", "", set()),
        (None, None, set()),
    ],
)
def test_row_tags_unions_good_qualifier(good: str | None, notes: str, tags: set[str]) -> None:
    assert row_tags(good, notes) == tags
