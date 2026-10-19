"""song_id.db builder (vendored from kjbox scripts/build_song_id_db.py).

The spelling-fold cases are ported from kjbox tests/unit/test_song_identify.py.
"""

import gzip
import sqlite3
from pathlib import Path

import pytest

from karaoke_decide.song_search.build_song_id import SCHEMA_VERSION, _fold_title, build, stored_meta

FIXTURE_TSV = Path(__file__).resolve().parents[2] / "fixtures" / "song_search" / "songs_fixture.tsv"
FIXTURE_ROWS = 459  # data lines in songs_fixture.tsv
FIXTURE_SONGS = 450  # minus 4 junk rows (cover, karaoke credit, 2 self-titled), 5 merged spellings


def _write_gz(path: Path, rows: list[tuple[object, ...]]) -> Path:
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for r in rows:
            f.write("\t".join(str(x) for x in r) + "\n")
    return path


def _build_rows(tmp_path: Path, rows: list[tuple[object, ...]]) -> list[tuple[str, str, int | None, int]]:
    db = tmp_path / "song_id.db"
    build([_write_gz(tmp_path / "songs.tsv.gz", rows)], db, run="test")
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT artist, title, pop, karaoke FROM songs ORDER BY artist, title").fetchall()
    finally:
        conn.close()


def test_fixture_build_meta_and_counts(tmp_path: Path) -> None:
    db = tmp_path / "song_id.db"
    out = build([FIXTURE_TSV], db, run="20261018-000000")
    assert sum(1 for line in FIXTURE_TSV.read_text(encoding="utf-8").splitlines() if line.strip()) == FIXTURE_ROWS
    assert out["songs"] == FIXTURE_SONGS
    assert out["run"] == "20261018-000000"
    assert set(out) == {"songs", "words", "built_at", "run"}
    meta = stored_meta(db)
    assert meta["schema_version"] == "3" == str(SCHEMA_VERSION)
    assert meta["normalizer_version"] == "3"
    assert meta["songs"] == str(FIXTURE_SONGS)
    assert meta["words"] == str(out["words"])
    assert meta["built_at"] == str(out["built_at"])
    assert meta["run"] == "20261018-000000"
    assert not (tmp_path / "song_id.db.new").exists()


def test_fixture_merge_keeps_the_karaoke_spelling(tmp_path: Path) -> None:
    """A36: spelling variants fold into one song showing the karaoke spelling."""
    db = tmp_path / "song_id.db"
    build([FIXTURE_TSV], db, run="r")
    conn = sqlite3.connect(db)
    try:
        smash = conn.execute(
            "SELECT title, pop, karaoke FROM songs WHERE artist = 'Smash Mouth' AND title LIKE 'Walk%'"
        ).fetchall()
        missy = conn.execute(
            "SELECT title, pop, karaoke FROM songs WHERE artist = 'Missy Elliott' AND title LIKE 'Get%'"
        ).fetchall()
        junk = conn.execute(
            "SELECT COUNT(*) FROM songs WHERE artist LIKE '%Cover%' OR (artist = 'Future' AND title = 'Future')"
        ).fetchone()
    finally:
        conn.close()
    assert smash == [("Walkin' on the Sun", 70, 1)]
    assert missy == [("Get Ur Freak On", 72, 1)]
    assert junk == (0,)


def test_multiple_shards_and_plain_tsv(tmp_path: Path) -> None:
    a = _write_gz(tmp_path / "a.tsv.gz", [("The Strokes", "Machu Picchu", 64, 1)])
    b = tmp_path / "b.tsv"
    b.write_text("Rihanna\tPush Up On Me\t47\t0\nNarrow Head\tSee You Around\t\t1\nshort\trow\n", encoding="utf-8")
    db = tmp_path / "song_id.db"
    out = build([a, b], db, run="r")
    assert out["songs"] == 3
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT pop, karaoke FROM songs WHERE artist = 'Narrow Head'").fetchone() == (None, 1)
        assert conn.execute("SELECT artist FROM artists ORDER BY artist").fetchall() == [
            ("Narrow Head",),
            ("Rihanna",),
            ("The Strokes",),
        ]
    finally:
        conn.close()


def test_rebuild_replaces_the_previous_index(tmp_path: Path) -> None:
    db = tmp_path / "song_id.db"
    build([_write_gz(tmp_path / "one.tsv.gz", [("Seal", "Crazy", 66, 1)])], db, run="one")
    build(
        [_write_gz(tmp_path / "two.tsv.gz", [("Seal", "Crazy", 66, 1), ("Seal", "Kiss from a Rose", 75, 1)])],
        db,
        run="two",
    )
    assert stored_meta(db)["run"] == "two"
    assert stored_meta(db)["songs"] == "2"


def test_stored_meta_of_missing_or_broken_db(tmp_path: Path) -> None:
    assert stored_meta(tmp_path / "nope.db") == {}
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"not a database")
    assert stored_meta(broken) == {}


def test_fold_title() -> None:
    assert _fold_title("walking on the sun") == _fold_title("walkin on the sun") == "walkinonthesun"
    assert _fold_title("get ur freak on") == _fold_title("get your freak on")
    assert _fold_title("thing") == "thing"  # 5-letter -ing words never fold
    assert _fold_title("sing") == "sing"


# ---- ported from kjbox ---------------------------------------------------------------


def test_spelling_variants_merge_and_show_the_karaoke_spelling(tmp_path: Path) -> None:
    rows = _build_rows(
        tmp_path,
        [
            ("Rihanna", "Breaking Dishes", 55, 0),  # MusicBrainz variant with an ISRC score
            ("Rihanna", "Breakin' Dishes", "", 1),  # the KaraokeNerds / canonical spelling
            ("Smash Mouth", "Walking on the Sun", 40, 0),
            ("Smash Mouth", "Walkin’ on the Sun", 70, 0),
            ("Smash Mouth", "Walkin' On The Sun", "", 1),
            ("Seal", "Kiss From A Rose", "", 1),
            ("Seal", "Kiss from a Rose", 75, 0),  # same key: the most popular spelling shows
        ],
    )
    assert rows == [
        ("Rihanna", "Breakin' Dishes", 55, 1),
        ("Seal", "Kiss from a Rose", 75, 1),
        ("Smash Mouth", "Walkin’ on the Sun", 70, 1),
    ]


def test_between_karaoke_spellings_the_more_popular_wins(tmp_path: Path) -> None:
    rows = _build_rows(
        tmp_path,
        [
            ("Missy Elliott", "Get Your Freak On", 50, 0),
            ("Missy Elliott", "Get Your Freak On", "", 1),
            ("Missy Elliott", "Get Ur Freak On", 72, 0),
            ("Missy Elliott", "Get Ur Freak On", "", 1),
        ],
    )
    assert rows == [("Missy Elliott", "Get Ur Freak On", 72, 1)]


def test_display_spelling_is_ranked_by_its_own_popularity(tmp_path: Path) -> None:
    rows = _build_rows(
        tmp_path,
        [
            ("Band", "Breakin Your Heart", 10, 1),
            ("Band", "Breaking Your Heart", 100, 0),  # raises the group's popularity only
            ("Band", "Breakin Ur Heart", 50, 1),  # the more popular karaoke spelling
        ],
    )
    assert rows == [("Band", "Breakin Ur Heart", 100, 1)]


def test_tie_prefers_the_spelling_without_a_file_tag(tmp_path: Path) -> None:
    """Merged spellings tied on popularity (incl. blank) show the untagged one."""
    rows = _build_rows(
        tmp_path,
        [
            ("Fall Out Boy (MPX)", "Centuries", "", 1),  # tagged spelling seen first
            ("Fall Out Boy", "Centuries", "", 1),
            ("Paramore", "Misery Business [HM]", 60, 1),
            ("Paramore", "Misery Business", 60, 0),
            ("Muse", "Uprising (Multiplex)", 50, 1),  # more popular tagged spelling still wins
            ("Muse", "Uprising", 40, 1),
        ],
    )
    assert rows == [
        ("Fall Out Boy", "Centuries", None, 1),
        ("Muse", "Uprising (Multiplex)", 50, 1),
        ("Paramore", "Misery Business", 60, 1),
    ]


def test_fold_tie_prefers_the_spelling_without_a_file_tag(tmp_path: Path) -> None:
    rows = _build_rows(
        tmp_path,
        [
            ("Missy Elliott", "Get Ur Freak On", "", 1),  # folds onto the tagged spelling's key
            ("Missy Elliott (MPX)", "Get Your Freak On", "", 1),
        ],
    )
    assert rows == [("Missy Elliott", "Get Ur Freak On", None, 1)]


def test_fold_keeps_different_songs_apart(tmp_path: Path) -> None:
    rows = _build_rows(
        tmp_path,
        [
            ("Queen", "Thing", 40, 0),  # 5-letter -ing words never fold
            ("Queen", "Thin", 40, 0),
            ("Queen", "Sing", 40, 0),
            ("Queen", "Sin", 40, 0),
            ("Other", "Breakin' Dishes", 30, 0),  # other artist: never merged
            ("Rihanna", "Breaking Dishes", 55, 0),
        ],
    )
    assert len(rows) == 6


def test_junk_rows_are_dropped(tmp_path: Path) -> None:
    rows = _build_rows(
        tmp_path,
        [
            ("George Benson (Hscc Cover Ft X)", "Breezin'", 10, 0),  # cover upload
            ("Karaoke Hits Band", "Wonderwall", 20, 1),  # karaoke credit kept when it IS karaoke
            ("Future", "Future", 20, 0),  # self-titled junk
            ("Weezer", "Weezer", 45, 0),  # self-titled but popular: real
            ("", "No Artist", 50, 1),
        ],
    )
    assert rows == [("Karaoke Hits Band", "Wonderwall", 20, 1), ("Weezer", "Weezer", 45, 0)]


def test_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from karaoke_decide.song_search.build_song_id import main

    db = tmp_path / "song_id.db"
    assert main(["--run", "cli", str(FIXTURE_TSV), str(db)]) == 0
    assert f"{FIXTURE_SONGS} songs" in capsys.readouterr().out
    assert stored_meta(db)["run"] == "cli"
