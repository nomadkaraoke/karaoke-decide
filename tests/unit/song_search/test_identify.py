"""Song identification over the hand-curated fixture index.

Ported from kjbox kj-controller/tests/unit/test_song_identify.py (its tiny inline
catalogue still pins the matcher rules) plus the decide-specific cases: the
fixture TSV (tests/fixtures/song_search/songs_fixture.tsv), Review Focus 1 inputs
and the offline ``margin_bonus``.
"""

import gzip
import sqlite3
from pathlib import Path

import pytest

from karaoke_decide.song_search.build_song_id import build
from karaoke_decide.song_search.identify import (
    CONFIDENT_MARGIN,
    GENERIC_TITLE_POP_LEAD,
    SAME_TITLE_POP_LEAD,
    Match,
    SongIdentifier,
    _same_song,
    _word_sim,
    song_norm,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "song_search"
SONGS_TSV = FIXTURES / "songs_fixture.tsv"

# kjbox's own tiny catalogue: artist, title, popularity, karaoke
KJBOX_SONGS = [
    ("The Strokes", "Machu Picchu", 64, 1),
    ("The Strokes", "Machu Piccu", 30, 0),  # MusicBrainz misspelled duplicate
    ("The Strokes", "The Adults Are Talking", 70, 1),
    ("The Strokes", "Reptilia", 72, 1),
    ("The Stokes", "Lonely Road", 20, 0),  # real obscure band: must not steal "the stokes"
    ("Evaluna Montaner", "Machu Picchu", 72, 0),
    ("Rihanna", "Push Up On Me", 47, 0),
    ("Rihanna", "Umbrella", 85, 1),
    ("Sabrina Carpenter", "Espresso", 90, 1),
    ("Sabrina Carpenter", "Buy Me Presents", 60, 1),
    ("Laza Bossa", "Espresso", 62, 0),
    ("Kesha", "Die Young", 81, 1),
    ("Chappell Roan", "Die Young", 61, 1),
    ("Miley Cyrus", "Party In The U.S.A.", 80, 1),
    ("Seal", "Kiss from a Rose", 75, 1),
    ("Mother Mother", "Hayloft", 73, 1),
    ("Mother Mother", "Hayloft II", 70, 0),
    ("Soft Cell", "Tainted Love", 78, 1),
    ("Eric Carmen", "All By Myself", 60, 1),
    ("Dan + Shay", "All To Myself", 65, 0),
    ("Leonard Cohen", "Hallelujah", 70, 1),
    ("Jeff Buckley", "Hallelujah", 72, 1),
    ("Maximo Park", "Books From Boxes", 53, 1),
    ("Narrow Head", "See You Around", "", 1),  # karaoke-only row (no Spotify popularity)
    ("Taylor Swift", "my tears ricochet", 80, 1),
    ("Charlie Rich", "Too Many Tears", 30, 0),
    ("Sara Bareilles", "She Used to Be Mine", 68, 1),
    ("The Used", "Tell Me", 35, 0),
    ("Rihanna", "Breakin' Dishes", 70, 1),
    ("Rihanna", "Breaking Dishes (Soul Seekerz club mix)", 50, 0),
]


@pytest.fixture(scope="module")
def ident(tmp_path_factory: pytest.TempPathFactory) -> SongIdentifier:
    d = tmp_path_factory.mktemp("songid-kjbox")
    src = d / "songs.tsv.gz"
    with gzip.open(src, "wt", encoding="utf-8") as f:
        for a, t, p, k in KJBOX_SONGS:
            f.write(f"{a}\t{t}\t{p}\t{k}\n")
    db = d / "song_id.db"
    build([src], db, run="test")
    return SongIdentifier(db)


@pytest.fixture(scope="module")
def fixture_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    db = tmp_path_factory.mktemp("songid-fixture") / "song_id.db"
    build([SONGS_TSV], db, run="fixture")
    return db


@pytest.fixture(scope="module")
def fixture_identifier(fixture_db: Path) -> SongIdentifier:
    return SongIdentifier(fixture_db)


def _best(ident: SongIdentifier, q: str) -> tuple[str, tuple[str, str] | None]:
    r = ident.identify(q)
    b = r["best"]
    return r["status"], (b["artist"], b["title"]) if b else None


# ---- kjbox parity (ported verbatim) ------------------------------------------------


@pytest.mark.parametrize(
    "q,want",
    [
        ("rihanna push up on me", ("Rihanna", "Push Up On Me")),  # just casing
        ("push up on me rihanna", ("Rihanna", "Push Up On Me")),  # swapped order
        ("the strokes max picu", ("The Strokes", "Machu Picchu")),  # mangled title
        ("the stokes max picu", ("The Strokes", "Machu Picchu")),  # + artist typo
        ("the stokes adults are talking", ("The Strokes", "The Adults Are Talking")),
        ("Buy me presents Sabrina", ("Sabrina Carpenter", "Buy Me Presents")),  # artist fragment
        ("Seal kiss", ("Seal", "Kiss from a Rose")),  # partial title + artist
        ("maximo park boks", ("Maximo Park", "Books From Boxes")),
        ("Espresso", ("Sabrina Carpenter", "Espresso")),  # title only → most popular
        ("Die young", ("Kesha", "Die Young")),
        ("Party in the usa", ("Miley Cyrus", "Party In The U.S.A.")),  # "U.S.A." == "usa"
        ("hayloft mother mother", ("Mother Mother", "Hayloft")),  # not "Hayloft II"
        ("all by myself", ("Eric Carmen", "All By Myself")),  # "by" is a real word
        ("soft cell tainted love", ("Soft Cell", "Tainted Love")),
        ("narrowhead see you around", ("Narrow Head", "See You Around")),  # karaoke-only row
        ("rihanna breaking dishes", ("Rihanna", "Breakin' Dishes")),  # dropped g
    ],
)
def test_confident_identifications(ident: SongIdentifier, q: str, want: tuple[str, str]) -> None:
    assert _best(ident, q) == ("confident", want)


@pytest.mark.parametrize(
    "q,want",
    [
        ("my tears richo", ("Taylor Swift", "my tears ricochet")),  # mistyped half-typed last word
        ("She used to be mi e", ("Sara Bareilles", "She Used to Be Mine")),  # + a stray space
    ],
)
def test_mistyped_partial_last_word_still_finds_the_title(ident: SongIdentifier, q: str, want: tuple[str, str]) -> None:
    r = ident.identify(q)
    assert r["status"] in ("confident", "candidates")
    assert (r["candidates"][0]["artist"], r["candidates"][0]["title"]) == want


def test_sequel_is_not_merged_with_the_original(ident: SongIdentifier) -> None:
    r = ident.identify("hayloft ii mother mother")
    assert (r["best"]["artist"], r["best"]["title"]) == ("Mother Mother", "Hayloft II")
    assert any(c["title"] == "Hayloft" for c in r["candidates"])


def test_roman_numeral_parts_stay_distinct() -> None:
    def part(t: str) -> Match:
        return Match("Pink Floyd", t, 1.0, 60, True)

    assert not _same_song(part("Another Brick in the Wall, Pt. II"), part("Another Brick in the Wall, Pt. III"))
    assert not _same_song(part("Symphony No. 5"), part("Symphony No. 6"))


def test_same_song_credit_variants_and_misspellings() -> None:
    assert _same_song(
        Match("Wisin & Yandel", "Adore", 1.0, 60, True), Match("Yandel feat. Wisin", "Adore", 1.0, 50, False)
    )
    assert _same_song(
        Match("The Strokes", "Machu Picchu", 1.0, 64, True), Match("The Strokes", "Machu Piccu", 1.0, 30, False)
    )
    assert not _same_song(Match("Kesha", "Die Young", 1.0, 80, True), Match("Sylvan Esso", "Die Young", 1.0, 50, False))


def test_word_sim() -> None:
    assert _word_sim("muse", "muse") == 1.0
    assert _word_sim("breaking", "breakin") == 0.95  # dropped g
    assert _word_sim("ric", "ricochet") == 0.9  # prefix typing
    assert _word_sim("ricoc", "ricochet") == 0.9
    assert _word_sim("rococ", "ricochet") == 0.75  # mistyped start
    assert _word_sim("zzz", "muse") == 0.0


def test_misspelled_duplicate_does_not_block_confidence(ident: SongIdentifier) -> None:
    r = ident.identify("the stokes max picu")
    assert r["status"] == "confident" and r["best"]["title"] == "Machu Picchu"
    assert all(c["title"] != "Machu Piccu" for c in r["candidates"])


def test_same_title_close_popularity_asks_which_one(ident: SongIdentifier) -> None:
    r = ident.identify("hallelujah")
    assert r["status"] == "candidates"
    assert {c["artist"] for c in r["candidates"][:2]} == {"Jeff Buckley", "Leonard Cohen"}


@pytest.mark.parametrize("q", ["xqzv blorp wibble", "zz", "", "that song from titanic"])
def test_nothing_or_unexplained_query_is_none(ident: SongIdentifier, q: str) -> None:
    assert ident.identify(q)["status"] == "none"


def test_song_norm_joins_initials_and_keeps_ft_words() -> None:
    assert song_norm("Party In The U.S.A.") == "party in the usa"
    assert song_norm("Soft Cell") == "soft cell"
    assert song_norm("Taylor Swift ft. Drake") == "taylor swift"


def test_missing_index_is_unavailable(tmp_path: Path) -> None:
    missing = SongIdentifier(tmp_path / "nope.db")
    assert missing.available is False
    assert missing.identify("espresso") == {"status": "none", "best": None, "candidates": [], "unavailable": True}


# ---- decide fixture index ------------------------------------------------------------


def test_result_shape(fixture_identifier: SongIdentifier) -> None:
    r = fixture_identifier.identify("starlite muse")
    assert set(r) == {"status", "best", "candidates"}
    assert set(r["best"]) == {"artist", "title", "score", "popularity", "karaoke", "detail"}
    assert set(r["candidates"][0]) == {"artist", "title", "score", "popularity", "karaoke"}


def test_typo_with_artist_is_confident(fixture_identifier: SongIdentifier) -> None:
    assert _best(fixture_identifier, "starlite muse") == ("confident", ("Muse", "Starlight"))


def test_title_then_artist_prefers_the_named_artist(fixture_identifier: SongIdentifier) -> None:
    """A11 (INBOX): "Starlight Muse" — three other artists have a "Starlight"."""
    assert _best(fixture_identifier, "Starlight Muse") == ("confident", ("Muse", "Starlight"))


def test_artist_word_picks_the_named_artist_among_same_titles(fixture_identifier: SongIdentifier) -> None:
    """Spec §1 (R15): the identified song has no karaoke version; the other two
    "Machu Picchu"s (Camilo & Evaluna Montaner) do, and must not be picked."""
    r = fixture_identifier.identify("machu picchu strokes")
    assert r["status"] == "confident"
    assert (r["best"]["artist"], r["best"]["title"]) == ("The Strokes", "Machu Picchu")
    assert r["best"]["karaoke"] is False
    rivals = [c for c in r["candidates"] if c["title"] == "Machu Picchu" and c["artist"] != "The Strokes"]
    assert rivals and all(c["karaoke"] for c in rivals)


def test_generic_title_shared_by_several_artists_asks_which_one(fixture_identifier: SongIdentifier) -> None:
    """A21: "baby" — Justin Bieber, Clean Bandit, Brandy, Ashanti… is a real question."""
    r = fixture_identifier.identify("baby")
    assert r["status"] == "candidates"
    assert r["best"]["artist"] == "Justin Bieber"
    assert {"Justin Bieber", "Brandy", "Ashanti"} <= {
        c["artist"] for c in fixture_identifier.identify("baby", limit=10)["candidates"]
    }
    # Guard: the same-title runner-up's popularity lead must sit between the normal
    # and the generic-title bar, so only the generic-title rule makes this "candidates".
    best, runner = r["candidates"][0], r["candidates"][1]
    assert runner["title"] == "Baby" and best["karaoke"] and runner["karaoke"]
    assert SAME_TITLE_POP_LEAD <= best["popularity"] - runner["popularity"] < GENERIC_TITLE_POP_LEAD


def test_same_lead_with_only_two_same_title_artists_is_confident(tmp_path: Path) -> None:
    """Companion to A21: the same 16-point lead auto-picks when only two artists share
    the title (the normal SAME_TITLE_POP_LEAD bar applies)."""
    src = tmp_path / "songs.tsv"
    src.write_text("Justin Bieber\tBaby\t78\t1\nClean Bandit\tBaby\t62\t1\n", encoding="utf-8")
    db = tmp_path / "song_id.db"
    build([src], db, run="t")
    assert _best(SongIdentifier(db), "baby") == ("confident", ("Justin Bieber", "Baby"))


def test_exact_score_ties_are_ordered_deterministically(tmp_path: Path) -> None:
    """Decide addition: equal scores rank by popularity, then artist, then title —
    never by index row order (the TS port must reproduce the same order)."""
    orders = []
    for rows in (
        ["Zeta\tTiedsong\t50\t1", "Alpha\tTiedsong\t50\t1"],
        ["Alpha\tTiedsong\t50\t1", "Zeta\tTiedsong\t50\t1"],
    ):
        src = tmp_path / "songs.tsv"
        src.write_text("\n".join(rows) + "\n", encoding="utf-8")
        db = tmp_path / "song_id.db"
        build([src], db, run="t")
        cands = SongIdentifier(db).identify("tiedsong")["candidates"]
        assert cands[0]["score"] == cands[1]["score"]
        orders.append([c["artist"] for c in cands])
    assert orders == [["Alpha", "Zeta"], ["Alpha", "Zeta"]]


def test_same_title_clear_popularity_lead_picks_the_leader(fixture_identifier: SongIdentifier) -> None:
    r = fixture_identifier.identify("die young")
    assert r["best"]["artist"] == "Kesha"
    assert r["status"] == "confident"


@pytest.mark.parametrize("q", ["", " ", "karaoke", "x", "🎤🎤", "a" * 250, "שיר", "歌"])
def test_queries_never_seen_are_none(fixture_identifier: SongIdentifier, q: str) -> None:
    """Review Focus 1: empty after cleanup, 1 char, emoji, 200+ chars, RTL/CJK."""
    r = fixture_identifier.identify(q)
    assert r["status"] == "none"
    assert r["best"] is None and r["candidates"] == []


def test_overlong_query_is_truncated_to_200_chars(fixture_identifier: SongIdentifier) -> None:
    q = "bohemian rhapsody queen" + " " * 180 + "x" * 50
    assert len(q) > 200
    assert _best(fixture_identifier, q) == ("confident", ("Queen", "Bohemian Rhapsody"))


# A non-same-title confident answer whose score margin over the runner-up is in
# [0.06, 0.09): confident in the cloud, "Which one?" offline (+0.03).
# "here go": OK Go's "Here It Goes Again" leads Céline Dion's "My Heart Will Go On" by ~0.078.
MARGIN_QUERY = "here go"


def test_margin_query_is_in_the_bonus_band(fixture_identifier: SongIdentifier) -> None:
    r = fixture_identifier.identify(MARGIN_QUERY)
    best, runner = r["candidates"][0], r["candidates"][1]
    assert best["title"] != runner["title"]
    assert CONFIDENT_MARGIN <= best["score"] - runner["score"] < CONFIDENT_MARGIN + 0.03


def test_margin_bonus_downgrades_close_confident_answer(fixture_identifier: SongIdentifier, fixture_db: Path) -> None:
    assert fixture_identifier.identify(MARGIN_QUERY)["status"] == "confident"
    offline = SongIdentifier(fixture_db, margin_bonus=0.03)
    r = offline.identify(MARGIN_QUERY)
    assert r["status"] == "candidates"
    assert (r["best"]["artist"], r["best"]["title"]) == ("OK Go", "Here It Goes Again")


def test_margin_bonus_keeps_same_title_popularity_lead(fixture_db: Path) -> None:
    """The popularity-lead rule decides same-title rivals on its own terms."""
    offline = SongIdentifier(fixture_db, margin_bonus=0.03)
    assert _best(offline, "die young") == ("confident", ("Kesha", "Die Young"))


def test_reload_reopens_the_index(fixture_db: Path) -> None:
    s = SongIdentifier(fixture_db)
    assert s.identify("wonderwall")["best"]["artist"] == "Oasis"
    s.reload()
    assert s.identify("wonderwall")["best"]["artist"] == "Oasis"
    assert s.stats()["schema_version"] == "3"


def test_close_closes_every_threads_connection_and_reopens(fixture_db: Path) -> None:
    import threading

    si = SongIdentifier(fixture_db)
    opened: list[sqlite3.Connection] = []
    ready, release = threading.Event(), threading.Event()

    def worker() -> None:
        opened.append(si._db())
        ready.set()
        release.wait()

    t = threading.Thread(target=worker)
    t.start()
    ready.wait()
    try:
        assert si.identify("starlite muse")["status"] == "confident"
        opened.append(si._db())
        assert len(si._conns) == 2  # both threads alive
        si.close()
        assert len(si._conns) == 0
        for c in opened:
            with pytest.raises(sqlite3.ProgrammingError):
                c.execute("SELECT 1")
        assert si.identify("starlite muse")["status"] == "confident"  # reopens
    finally:
        release.set()
        t.join()
        si.close()


def test_connections_of_exited_threads_are_closed_and_dropped(fixture_db: Path) -> None:
    import threading

    si = SongIdentifier(fixture_db)
    opened: list[sqlite3.Connection] = []
    for _ in range(20):  # short-lived worker threads, one after another
        t = threading.Thread(target=lambda: opened.append(si._db()))
        t.start()
        t.join()
        assert len(si._conns) <= 2
    si._db()  # this thread registers → the last exited thread's connection goes too
    assert len(si._conns) == 1
    for c in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            c.execute("SELECT 1")
    si.close()
