"""Availability index (KN full + community merged per song).

Fixtures: kn_full_fixture.json / kn_community_fixture.json are synthetic rows over
the A2 songs_fixture.tsv songs (plus a few named edge cases); never real KN data.
"""

import gzip
import shutil
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from karaoke_decide.song_search.availability import (
    AVAILABILITY_SCHEMA_VERSION,
    MAX_LIMIT,
    AvailabilityIndex,
    _normalize_youtube_url,
    build_availability_db,
)
from karaoke_decide.song_search.build_song_id import build as build_song_id
from karaoke_decide.song_search.text_normalize import NORMALIZER_VERSION, SEARCH_INDEX_VERSION, song_key
from karaoke_decide.song_search.version_priority import COMMERCIAL_BRANDS, COMMUNITY_BRANDS

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "song_search"

STARLIGHT = song_key("Muse", "Starlight")
JOURNEY = song_key("Journey", "Don't Stop Believin'")


def _gz(src: Path, dst: Path) -> Path:
    with open(src, "rb") as f, gzip.open(dst, "wb") as g:
        shutil.copyfileobj(f, g)
    return dst


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, Any]]:
    d = tmp_path_factory.mktemp("availability")
    song_id_db = d / "song_id.db"
    build_song_id([FIXTURES / "songs_fixture.tsv"], song_id_db, run="test")
    out = d / "availability.db"
    stats = build_availability_db(
        _gz(FIXTURES / "kn_full_fixture.json", d / "full.json.gz"),
        _gz(FIXTURES / "kn_community_fixture.json", d / "community.json.gz"),
        song_id_db,
        out,
        run="20261018-000000",
    )
    return out, stats


@pytest.fixture(scope="module")
def idx(built: tuple[Path, dict[str, Any]]) -> AvailabilityIndex:
    return AvailabilityIndex(built[0])


def _brands(group: dict[str, Any]) -> list[str]:
    return [v["brand_code"] for v in group["versions"]]


# --- merge -----------------------------------------------------------------


def test_merge_dedupes_community_codes_in_full_brands(idx: AvailabilityIndex) -> None:
    song = idx.song(STARLIGHT)
    assert song is not None
    cc = [v for v in song["versions"] if v["brand_code"] == "CC"]
    assert len(cc) == 1
    assert cc[0]["kind"] == "community"
    assert cc[0]["youtube_url"].startswith("https://www.youtube.com/watch?v=")
    assert sorted(_brands(song)) == ["CC", "KV", "NOMAD", "SC", "SF"]
    assert song["version_count"] == 5


def test_disc_only_commercial_has_no_url(idx: AvailabilityIndex) -> None:
    song = idx.song(STARLIGHT)
    assert song is not None
    sc = next(v for v in song["versions"] if v["brand_code"] == "SC")
    assert sc["youtube_url"] is None
    assert sc["kind"] == "commercial"
    assert sc["brand_name"] == "Sound Choice"


def test_versions_sorted_by_rank(idx: AvailabilityIndex) -> None:
    song = idx.song(STARLIGHT)
    assert song is not None
    assert _brands(song) == ["CC", "NOMAD", "KV", "SC", "SF"]
    assert [v["kind"] for v in song["versions"]] == ["community"] * 2 + ["commercial"] * 3
    ranks = [v["rank"] for v in song["versions"]]
    assert ranks == sorted(ranks)


def test_full_only_song_and_unknown_brand(idx: AvailabilityIndex) -> None:
    song = idx.song(song_key("Obscure Band", "Disc Only Song"))
    assert song is not None
    assert song["versions"] == [
        {"brand_code": "XYZ", "brand_name": "XYZ", "kind": "commercial", "youtube_url": None, "rank": 3006}
    ]


def test_spelling_variants_merge_into_one_song(idx: AvailabilityIndex) -> None:
    # feat. clause (identity key), "AC-DC" vs "AC/DC", and a "(MPX)" file tag all fold.
    zedd = idx.song(song_key("Zedd", "Stay the Night"))
    assert zedd is not None and "OBSK" in _brands(zedd)
    acdc = idx.song(song_key("AC/DC", "Highway to Hell"))
    assert acdc is not None and sorted(_brands(acdc)) == ["FBK", "SC", "ZM"]
    fob = idx.song(song_key("Fall Out Boy", "Centuries"))
    assert fob is not None and "IKV" in _brands(fob)


def _song_id_rows(path: Path, where: str = "1") -> list[tuple[str, str, int | None]]:
    conn = sqlite3.connect(path.parent / "song_id.db")
    try:
        return conn.execute(f"SELECT artist, title, pop FROM songs WHERE {where}").fetchall()
    finally:
        conn.close()


def test_spellings_merge_under_song_id_display(idx: AvailabilityIndex, built: tuple[Path, dict[str, Any]]) -> None:
    # song_id.db folds "Get Ur Freak On" / "Get Your Freak On" into one song; KN has
    # both spellings. Availability must hold ONE song under song_id's display key.
    ((artist, title, pop),) = _song_id_rows(built[0], "artist = 'Missy Elliott' AND title LIKE 'Get %Freak On'")
    song = idx.song(song_key(artist, title))
    assert song is not None
    assert (song["artist"], song["title"], song["pop"]) == (artist, title, pop)
    assert sorted(_brands(song)) == ["FAKEY", "NOMAD", "SK", "VS"]
    other = "Get Your Freak On" if title == "Get Ur Freak On" else "Get Ur Freak On"
    assert idx.song(song_key("Missy Elliott", other)) is None
    # Both spellings stay searchable.
    for q in ("missy get ur freak on", "missy get your freak on"):
        assert idx.search(q)[0]["song_key"] == song["song_key"], q


def test_every_song_id_karaoke_song_is_in_availability(
    idx: AvailabilityIndex, built: tuple[Path, dict[str, Any]]
) -> None:
    rows = _song_id_rows(built[0], "karaoke = 1")
    assert len(rows) > 390
    missing = [(a, t) for a, t, _ in rows if idx.song(song_key(a, t)) is None]
    assert missing == []


def test_file_tagged_kn_row_joins_untagged_song_id_song(idx: AvailabilityIndex) -> None:
    song = idx.song(song_key("Fall Out Boy", "Centuries"))
    assert song is not None
    assert (song["artist"], song["title"]) == ("Fall Out Boy", "Centuries")
    assert "IKV" in _brands(song) and song["pop"] is not None
    assert idx.song(song_key("Fall Out Boy (MPX)", "Centuries")) is None


def test_unresolved_rows_fall_back_to_their_own_spelling(idx: AvailabilityIndex) -> None:
    song = idx.song(song_key("Andrew Lloyd Webber", "Starlight Express"))
    assert song is not None
    assert (song["artist"], song["title"], song["pop"]) == ("Andrew Lloyd Webber", "Starlight Express", None)


def test_unresolved_tagged_and_untagged_rows_are_one_song(tmp_path: Path) -> None:
    """No song_id match: "(MPX)" / "[HM]" tags must not split a KN song in two."""
    full = tmp_path / "full.json"
    full.write_text(
        '[{"Artist": "Obscure Band (MPX)", "Title": "Deep Cut", "Brands": "SC"},'
        ' {"Artist": "Obscure Band", "Title": "Deep Cut [HM]", "Brands": "SF"}]',
        encoding="utf-8",
    )
    community = tmp_path / "community.json"
    community.write_text(
        '{"Items": [{"Artist": "Obscure Band", "Title": "Deep Cut", "Brand": "NOMAD", "Watch": ""}]}',
        encoding="utf-8",
    )
    out = tmp_path / "a.db"
    stats = build_availability_db(
        _gz(full, tmp_path / "f.json.gz"), _gz(community, tmp_path / "c.json.gz"), None, out, run="r"
    )
    assert stats["songs"] == 1
    song = AvailabilityIndex(out).song(song_key("Obscure Band", "Deep Cut"))
    assert song is not None
    assert (song["artist"], song["title"]) == ("Obscure Band", "Deep Cut")
    assert sorted(_brands(song)) == ["NOMAD", "SC", "SF"]


def test_unresolved_display_prefers_the_untagged_spelling(tmp_path: Path) -> None:
    full = tmp_path / "full.json"
    full.write_text(
        '[{"Artist": "Obscure Band (MPX)", "Title": "Deep Cut", "Brands": "SC"},'
        ' {"Artist": "Obscure Band", "Title": "Deep Cut", "Brands": "SF"}]',
        encoding="utf-8",
    )
    community = tmp_path / "community.json"
    community.write_text('{"Items": []}', encoding="utf-8")
    out = tmp_path / "a.db"
    build_availability_db(_gz(full, tmp_path / "f.json.gz"), _gz(community, tmp_path / "c.json.gz"), None, out, run="r")
    song = AvailabilityIndex(out).song(song_key("Obscure Band", "Deep Cut"))
    assert song is not None and (song["artist"], song["title"]) == ("Obscure Band", "Deep Cut")


def test_featured_spelling_stays_searchable(idx: AvailabilityIndex) -> None:
    # "Zedd feat. Hayley Williams" merged into "Zedd - Stay the Night"; the featured
    # name is still indexed.
    res = idx.search("hayley stay the night")
    assert res and res[0]["song_key"] == song_key("Zedd", "Stay the Night")
    assert res[0]["artist"] == "Zedd"


def test_community_without_url_sorts_after_playable(idx: AvailabilityIndex) -> None:
    song = idx.song(song_key("Bob Seger", "Night Moves"))
    assert song is not None
    zp = next(v for v in song["versions"] if v["brand_code"] == "ZP")
    assert zp["kind"] == "community" and zp["youtube_url"] is None
    # Playable first; then the URL-less rest by rank (community ZP before commercial KV).
    assert [v["brand_code"] for v in song["versions"] if v["youtube_url"] is None] == ["ZP", "KV"]
    assert song["versions"][0]["youtube_url"] is not None
    assert song["versions"][-2]["brand_code"] == "ZP"


def test_rows_without_title_or_brands_are_skipped(idx: AvailabilityIndex) -> None:
    assert idx.song(song_key("Nobody", "")) is None
    assert idx.song(song_key("Empty Brands", "No Releases")) is None


def test_pop_from_song_id_db(idx: AvailabilityIndex) -> None:
    song = idx.song(STARLIGHT)
    assert song is not None and song["pop"] == 72
    assert song["artist"] == "Muse" and song["title"] == "Starlight"
    # Not in song_id.db → no popularity.
    assert idx.song(song_key("Andrew Lloyd Webber", "Starlight Express"))["pop"] is None  # type: ignore[index]


def test_build_without_song_id_db(tmp_path: Path) -> None:
    out = tmp_path / "a.db"
    stats = build_availability_db(
        _gz(FIXTURES / "kn_full_fixture.json", tmp_path / "f.json.gz"),
        _gz(FIXTURES / "kn_community_fixture.json", tmp_path / "c.json.gz"),
        None,
        out,
        run="r",
    )
    assert stats["songs"] > 400
    assert AvailabilityIndex(out).song(STARLIGHT)["pop"] is None  # type: ignore[index]
    assert not (tmp_path / "a.db.new").exists()


def test_meta_and_stats(built: tuple[Path, dict[str, Any]]) -> None:
    path, stats = built
    meta = dict(sqlite3.connect(path).execute("SELECT key, value FROM meta").fetchall())
    assert meta["schema_version"] == str(AVAILABILITY_SCHEMA_VERSION)
    assert meta["normalizer_version"] == str(NORMALIZER_VERSION)
    assert meta["search_index_version"] == SEARCH_INDEX_VERSION
    assert meta["run"] == "20261018-000000"
    assert meta["songs"] == str(stats["songs"])
    assert meta["versions"] == str(stats["versions"])
    assert stats["run"] == "20261018-000000"


def test_kind_check_constraint(built: tuple[Path, dict[str, Any]], tmp_path: Path) -> None:
    copy = tmp_path / "copy.db"
    shutil.copy(built[0], copy)
    conn = sqlite3.connect(copy)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO versions VALUES ('a|b', 'X', 'X', 'bogus', NULL, 0)")


# --- search ----------------------------------------------------------------


def test_search_token_and_any_order(idx: AvailabilityIndex) -> None:
    res = idx.search("muse starlight")
    assert res[0]["title"] == "Starlight" and res[0]["artist"] == "Muse"
    assert idx.search("starlight muse")[0]["song_key"] == STARLIGHT


def test_search_typo_needs_every_word(idx: AvailabilityIndex) -> None:
    for q in ("queen bohemian", "queen bohemain"):  # FTS path, then the trigram fuzzy path
        res = idx.search(q)
        assert res, q
        assert {(g["artist"], g["title"]) for g in res} == {("Queen", "Bohemian Rhapsody")}, q


def test_fuzzy_fallback_typo(idx: AvailabilityIndex) -> None:
    # One dropped letter: FTS misses, the trigram ladder finds it (a 3-edit "starlite" is
    # beyond the typo threshold here; identification handles that, not availability).
    assert not idx._conn().execute("SELECT 1 FROM songs_fts WHERE songs_fts MATCH '\"starlght\"'").fetchall()
    res = idx.search("muse starlght")
    assert res and res[0]["song_key"] == STARLIGHT


def test_joined_word(idx: AvailabilityIndex) -> None:
    res = idx.search("acdc")
    assert res and res[0]["song_key"] == song_key("AC-DC", "Highway to Hell")


def test_group_relevance_title_equals_query_first(idx: AvailabilityIndex) -> None:
    res = idx.search("starlight")
    titles = [g["title"] for g in res]
    # Starlight Express has 21 versions, but the title equalling the query wins (A79);
    # among equal titles: playable, then version count (Muse 5 > Taylor Swift 2).
    assert titles[:2] == ["Starlight", "Starlight"]
    assert [g["artist"] for g in res[:2]] == ["Muse", "Taylor Swift"]
    assert set(titles[2:]) == {"Starlight Express", "Starlight (Live)"}
    # Prefix title + playable outranks prefix title + disc-only.
    assert titles.index("Starlight Express") < titles.index("Starlight (Live)")


def test_search_group_shape(idx: AvailabilityIndex) -> None:
    g = idx.search("muse starlight")[0]
    assert set(g) == {"song_key", "artist", "title", "pop", "version_count", "versions"}
    assert set(g["versions"][0]) == {"brand_code", "brand_name", "kind", "youtube_url", "rank"}
    assert g == idx.song(STARLIGHT)


def test_limit_clamped(idx: AvailabilityIndex) -> None:
    assert 0 < len(idx.search("a", limit=-1)) <= MAX_LIMIT
    assert len(idx.search("a", limit=10_000)) == MAX_LIMIT
    assert len(idx.search("a", limit=3)) == 3


@pytest.mark.parametrize("q", ["", "   ", "!!!", "🎤", "x" * 300, "zzzzqqqq", "שלום", "日本語"])
def test_queries_with_no_match_return_empty(idx: AvailabilityIndex, q: str) -> None:
    assert idx.search(q) == []


def test_short_query_substring_fallback(idx: AvailabilityIndex) -> None:
    # "ss" is no token prefix in the fixture's index text, so only the LIKE fallback finds it.
    res = idx.search("ss")
    assert res
    assert all("ss" in (g["artist"] + " " + g["title"]).lower() for g in res)


def test_many_versions_cap(idx: AvailabilityIndex) -> None:
    song = idx.song(JOURNEY)
    assert song is not None
    assert song["version_count"] == 55 and len(song["versions"]) == 55
    ranks = [v["rank"] for v in song["versions"]]
    assert ranks == sorted(ranks)
    assert sum(v["kind"] == "community" for v in song["versions"]) == 30
    assert song["versions"][0]["brand_code"] == "CC"


def test_broken_index_search_returns_empty(built: tuple[Path, dict[str, Any]], tmp_path: Path) -> None:
    copy = tmp_path / "broken.db"
    shutil.copy(built[0], copy)
    conn = sqlite3.connect(copy)
    conn.execute("DROP TABLE songs_fts")
    conn.commit()
    conn.close()
    assert AvailabilityIndex(copy).search("muse starlight") == []


def test_missing_index_raises(tmp_path: Path) -> None:
    with pytest.raises(sqlite3.Error):
        AvailabilityIndex(tmp_path / "nope.db")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://youtu.be/abcdefghijk", "https://www.youtube.com/watch?v=abcdefghijk"),
        ("https://www.youtube.com/watch?feature=x&v=abcdefghijk", "https://www.youtube.com/watch?v=abcdefghijk"),
        ("https://www.youtube.com/shorts/abcdefghijk", "https://www.youtube.com/watch?v=abcdefghijk"),
        (" https://example.com/video ", "https://example.com/video"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_youtube_url(raw: str | None, expected: str) -> None:
    assert _normalize_youtube_url(raw) == expected


def test_song_unknown_key(idx: AvailabilityIndex) -> None:
    assert idx.song("nobody|nothing") is None


def test_popular(idx: AvailabilityIndex) -> None:
    res = idx.popular(5)
    assert len(res) == 5
    pops = [g["pop"] for g in res]
    assert pops == sorted(pops, reverse=True) and pops[0] is not None
    assert len(idx.popular(-1)) <= MAX_LIMIT


# --- libraries -------------------------------------------------------------


def test_libraries_seeded_from_brands(built: tuple[Path, dict[str, Any]]) -> None:
    conn = sqlite3.connect(built[0])
    libs = {code: (name, kind, source) for code, name, kind, source in conn.execute("SELECT * FROM libraries")}
    expected = {c: "community" for c, _, _ in COMMUNITY_BRANDS} | {c: "commercial" for c, _, _ in COMMERCIAL_BRANDS}
    assert {c: k for c, (_, k, _) in libs.items()} == expected
    assert {s for _, _, s in libs.values()} == {"kn_brand"}
    assert libs["SC"][0] == "Sound Choice"
    linked = {r[0] for r in conn.execute("SELECT library_code FROM song_libraries WHERE song_key = ?", (STARLIGHT,))}
    assert linked == {"CC", "NOMAD", "KV", "SC", "SF"}
    # Unknown brands stay just brands (no library row, no link).
    assert not conn.execute("SELECT 1 FROM song_libraries WHERE library_code = 'XYZ'").fetchall()
