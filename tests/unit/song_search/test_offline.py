"""Offline index (the phone's ``offline.db``): karaoke songs + rivals, dual eval,
and the committed mobile parity fixtures."""

import gzip
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from karaoke_decide.song_search import eval as song_eval
from karaoke_decide.song_search import offline
from karaoke_decide.song_search.availability import build_availability_db
from karaoke_decide.song_search.build_song_id import build as build_song_id
from karaoke_decide.song_search.identify import SongIdentifier
from karaoke_decide.song_search.offline import (
    OFFLINE_MARGIN_BONUS,
    OFFLINE_SCHEMA_VERSION,
    build_offline_db,
    offline_groups,
    rival_key,
    select_rivals,
)
from karaoke_decide.song_search.text_normalize import NORMALIZER_VERSION, song_key

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "song_search"
REPO = Path(__file__).resolve().parents[3]


def _gz(src: Path, dst: Path) -> Path:
    with open(src, "rb") as f, gzip.open(dst, "wb") as g:
        shutil.copyfileobj(f, g)
    return dst


@pytest.fixture(scope="module")
def sources(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """(song_id.db, availability.db) built from the A2/A3 fixtures."""
    d = tmp_path_factory.mktemp("offline-src")
    song_id_db = d / "song_id.db"
    build_song_id([FIXTURES / "songs_fixture.tsv"], song_id_db, run="test")
    availability_db = d / "availability.db"
    build_availability_db(
        _gz(FIXTURES / "kn_full_fixture.json", d / "full.json.gz"),
        _gz(FIXTURES / "kn_community_fixture.json", d / "community.json.gz"),
        song_id_db,
        availability_db,
        run="test",
    )
    return song_id_db, availability_db


@pytest.fixture(scope="module")
def built(sources: tuple[Path, Path], tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, Any]]:
    out = tmp_path_factory.mktemp("offline") / "offline.db"
    return out, build_offline_db(sources[0], sources[1], out, run="20261018-000000")


def _rows(db: Path, sql: str, args: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def _karaoke_title_keys(song_id_db: Path) -> set[str]:
    return {rival_key(r[0]) for r in _rows(song_id_db, "SELECT title FROM songs WHERE karaoke = 1")}


# ---- contents ---------------------------------------------------------------------


def test_offline_has_only_karaoke_songs_and_rivals(sources: tuple[Path, Path], built: tuple[Path, Any]) -> None:
    song_id_db, _ = sources
    out, stats = built
    karaoke = set(_rows(song_id_db, "SELECT artist, title, pop FROM songs WHERE karaoke = 1"))
    rivals = {(a, t, p) for a, t, p, _k in select_rivals(song_id_db, _karaoke_title_keys(song_id_db))}
    assert karaoke and rivals and not karaoke & rivals
    assert set(_rows(out, "SELECT artist, title, pop FROM songs WHERE karaoke = 1")) == karaoke
    assert set(_rows(out, "SELECT artist, title, pop FROM songs WHERE karaoke = 0")) == rivals
    assert stats["songs"] == len(karaoke) and stats["rivals"] == len(rivals)
    # Fewer songs than the cloud: the non-karaoke songs that rival nothing are left out.
    n_cloud = _rows(song_id_db, "SELECT count(*) FROM songs")[0][0]
    assert stats["songs"] + stats["rivals"] < n_cloud
    assert stats["bytes"] == out.stat().st_size


def test_select_rivals_rule(sources: tuple[Path, Path]) -> None:
    """Non-karaoke songs sharing a karaoke song's base title (version suffix dropped,
    plural "s" folded), any popularity, the top N per title by COALESCE(pop, 30)."""
    song_id_db, _ = sources
    rivals = select_rivals(song_id_db, {rival_key("Baby")})
    assert ("Madison Beer", "Baby", 55, 0) in rivals
    assert all(k == 0 for *_rest, k in rivals)
    assert {t for _a, t, _p, _k in rivals} == {"Baby"}
    assert [p for _a, _t, p, _k in rivals] == sorted((p for _a, _t, p, _k in rivals), reverse=True)
    assert select_rivals(song_id_db, {rival_key("Baby")}, per_title_cap=1) == rivals[:1]
    assert select_rivals(song_id_db, set()) == []


def test_rival_key_folds_suffixes_and_plurals() -> None:
    assert rival_key("Lanterns") == rival_key("Lantern") == rival_key("Lantern (Live) - 2004 Remaster")
    assert rival_key("Baby") != rival_key("Babe")
    assert rival_key("(Untitled)") == rival_key("(untitled)") != ""


def test_song_key_column_and_versions_join(built: tuple[Path, Any]) -> None:
    out, stats = built
    for artist, title, key in _rows(out, "SELECT artist, title, song_key FROM songs"):
        assert key == song_key(artist, title)
    starlight = song_key("Muse", "Starlight")
    assert _rows(out, "SELECT count(*) FROM versions WHERE song_key = ?", (starlight,))[0][0] > 0
    assert stats["versions"] == _rows(out, "SELECT count(*) FROM versions")[0][0]
    # Every version belongs to a karaoke song of the index.
    orphans = _rows(
        out, "SELECT count(*) FROM versions WHERE song_key NOT IN (SELECT song_key FROM songs WHERE karaoke = 1)"
    )
    assert orphans == [(0,)]
    linked = {r[0] for r in _rows(out, "SELECT library_code FROM song_libraries WHERE song_key = ?", (starlight,))}
    assert linked == {"CC", "NOMAD", "KV", "SC", "SF"}
    assert _rows(out, "SELECT name FROM libraries WHERE code = 'SC'") == [("Sound Choice",)]


def test_rival_never_produces_versions(built: tuple[Path, Any]) -> None:
    out, _ = built
    rival_keys = {r[0] for r in _rows(out, "SELECT song_key FROM songs WHERE karaoke = 0")}
    assert rival_keys
    marks = ",".join("?" * len(rival_keys))
    assert _rows(out, f"SELECT count(*) FROM versions WHERE song_key IN ({marks})", tuple(rival_keys)) == [(0,)]
    assert _rows(out, f"SELECT count(*) FROM song_libraries WHERE song_key IN ({marks})", tuple(rival_keys)) == [(0,)]


def test_artists_carry_the_cloud_popularity(sources: tuple[Path, Path], built: tuple[Path, Any]) -> None:
    """The artist-first path orders artists by popularity: offline uses the cloud's."""
    cloud = dict(_rows(sources[0], "SELECT na, pop FROM artists"))
    offline_artists = _rows(built[0], "SELECT na, pop FROM artists")
    assert offline_artists and all(cloud[na] == pop for na, pop in offline_artists)
    assert {r[0] for r in offline_artists} == {r[0] for r in _rows(built[0], "SELECT DISTINCT na FROM songs")}


def test_meta_versions(built: tuple[Path, Any]) -> None:
    meta = dict(_rows(built[0], "SELECT key, value FROM meta"))
    assert meta["offline_schema_version"] == str(OFFLINE_SCHEMA_VERSION) == "1"
    assert meta["normalizer_version"] == str(NORMALIZER_VERSION) == "3"
    assert meta["schema_version"] == "3"
    assert meta["run"] == "20261018-000000"
    assert meta["rivals"] == str(built[1]["rivals"])


def test_readable_by_the_unchanged_identifier(built: tuple[Path, Any]) -> None:
    r = SongIdentifier(built[0], margin_bonus=OFFLINE_MARGIN_BONUS).identify("starlite muse")
    assert r["status"] == "confident"
    assert (r["best"]["artist"], r["best"]["title"]) == ("Muse", "Starlight")


def test_rebuild_replaces_and_leaves_no_temp_files(sources: tuple[Path, Path], tmp_path: Path) -> None:
    out = tmp_path / "offline.db"
    out.write_bytes(b"stale")
    build_offline_db(sources[0], sources[1], out, run="r2")
    assert dict(_rows(out, "SELECT key, value FROM meta"))["run"] == "r2"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["offline.db"]


# ---- rivals keep offline honest ---------------------------------------------------


def test_rival_blocks_false_confidence(
    sources: tuple[Path, Path], built: tuple[Path, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "baby": the cloud asks "Which one?" (several artists' "Baby"). Offline does too,
    because the non-karaoke "Baby"s ride along as rivals. Without them the phone
    would only hold two karaoke "Baby"s and confidently name Justin Bieber."""
    cloud = SongIdentifier(sources[0]).identify("baby")
    assert cloud["status"] == "candidates"
    with_rivals = SongIdentifier(built[0], margin_bonus=OFFLINE_MARGIN_BONUS).identify("baby")
    assert with_rivals["status"] == "candidates"

    monkeypatch.setattr(offline, "select_rivals", lambda *_a, **_k: [])
    bare = tmp_path / "offline-no-rivals.db"
    assert build_offline_db(sources[0], sources[1], bare, run="r")["rivals"] == 0
    without = SongIdentifier(bare, margin_bonus=OFFLINE_MARGIN_BONUS).identify("baby")
    assert without["status"] == "confident"
    assert (without["best"]["artist"], without["best"]["title"]) == ("Justin Bieber", "Baby")


def _pair(tmp_path: Path, rows: list[tuple[str, str, Any, int]]) -> tuple[SongIdentifier, SongIdentifier]:
    """(cloud, offline) identifiers over a tiny song-id index of ``rows``."""
    src = tmp_path / "songs.tsv"
    src.write_text("".join(f"{a}\t{t}\t{p}\t{k}\n" for a, t, p, k in rows), encoding="utf-8")
    song_id_db = tmp_path / "song_id.db"
    build_song_id([src], song_id_db, run="t")
    full = tmp_path / "full.json"
    full.write_text(json.dumps([{"Artist": a, "Title": t, "Brands": "SC"} for a, t, _p, k in rows if k]))
    community = tmp_path / "community.json"
    community.write_text('{"Items": []}')
    availability_db = tmp_path / "availability.db"
    build_availability_db(
        _gz(full, tmp_path / "f.json.gz"), _gz(community, tmp_path / "c.json.gz"), song_id_db, availability_db, "t"
    )
    off = tmp_path / "offline.db"
    build_offline_db(song_id_db, availability_db, off, run="t")
    return SongIdentifier(song_id_db), SongIdentifier(off, margin_bonus=OFFLINE_MARGIN_BONUS)


@pytest.mark.parametrize(
    "rows,q",
    [
        # (a) a low-popularity same-title rival still makes the title "generic"
        ([("Alpha", "Baby", 78, 1), ("Beta", "Baby", 62, 1), ("Gamma", "Baby", 25, 0)], "baby"),
        # (b) blank popularity counts as 30 in the matcher
        ([("Alpha", "Baby", 78, 1), ("Beta", "Baby", 62, 1), ("Gamma", "Baby", "", 0)], "baby"),
        # (c) an obscure karaoke song vs a slightly less obscure non-karaoke one
        ([("Obscure", "Lantern", 10, 1), ("Indie", "Lantern", 25, 0)], "lantern"),
    ],
)
def test_low_or_blank_pop_rivals_keep_the_cloud_verdict(
    tmp_path: Path, rows: list[tuple[str, str, Any, int]], q: str
) -> None:
    cloud, off = _pair(tmp_path, rows)
    rc, ro = cloud.identify(q), off.identify(q)
    assert rc["status"] == "candidates"
    assert ro["status"] == rc["status"]


def test_near_title_rival_blocks_naming_another_song(tmp_path: Path) -> None:
    """Cloud names the non-karaoke "Lantern"; offline must not confidently name
    the karaoke "Lanterns" (a different song)."""
    cloud, off = _pair(tmp_path, [("Alpha", "Lanterns", 50, 1), ("Indie", "Lantern", 55, 0)])
    for q in ("lantern", "lanterns"):
        rc, ro = cloud.identify(q), off.identify(q)
        if ro["status"] == "confident":
            assert rc["status"] == "confident", q
            assert song_key(ro["best"]["artist"], ro["best"]["title"]) == song_key(
                rc["best"]["artist"], rc["best"]["title"]
            ), q
    assert song_eval.dual_eval(cloud, off, [{"q": "lantern"}, {"q": "lanterns"}])["confident_disagree"] == 0


def test_rival_can_win_the_identification(sources: tuple[Path, Path], built: tuple[Path, Any]) -> None:
    """A rival is a real answer ("no karaoke version yet"), it just never has versions."""
    r = SongIdentifier(built[0], margin_bonus=OFFLINE_MARGIN_BONUS).identify("hurt nine inch nails")
    assert r["status"] == "confident"
    assert (r["best"]["artist"], r["best"]["title"], r["best"]["karaoke"]) == ("Nine Inch Nails", "Hurt", False)
    assert offline_groups(built[0], r) == []


# ---- groups -----------------------------------------------------------------------


def test_offline_groups_confident(built: tuple[Path, Any]) -> None:
    r = SongIdentifier(built[0], margin_bonus=OFFLINE_MARGIN_BONUS).identify("starlite muse")
    groups = offline_groups(built[0], r)
    assert [g["song_key"] for g in groups] == [song_key("Muse", "Starlight")]
    g = groups[0]
    assert (g["artist"], g["title"], g["karaoke"]) == ("Muse", "Starlight", True)
    assert g["version_count"] == len(g["versions"]) > 0
    urls = [v["youtube_url"] for v in g["versions"]]
    assert urls == sorted(urls, key=lambda u: u is None)  # playable first
    assert set(g["versions"][0]) == {"brand_code", "brand_name", "kind", "youtube_url", "rank"}


def test_offline_groups_candidates_and_none(built: tuple[Path, Any]) -> None:
    ident = SongIdentifier(built[0], margin_bonus=OFFLINE_MARGIN_BONUS)
    r = ident.identify("baby")
    groups = offline_groups(built[0], r)
    keys = [g["song_key"] for g in groups]
    karaoke_cands = [song_key(c["artist"], c["title"]) for c in r["candidates"] if c["karaoke"]]
    assert keys == karaoke_cands and len(keys) >= 2
    assert offline_groups(built[0], ident.identify("xqzv blorp wibble")) == []
    assert offline_groups(built[0], {"status": "none", "best": None, "candidates": []}) == []


# ---- dual eval --------------------------------------------------------------------


def test_dual_eval_counts(sources: tuple[Path, Path], built: tuple[Path, Any]) -> None:
    cases = song_eval.load_cases(FIXTURES / "song_id_eval.jsonl")
    cloud = SongIdentifier(sources[0])
    off = SongIdentifier(built[0], margin_bonus=OFFLINE_MARGIN_BONUS)
    out = song_eval.dual_eval(cloud, off, cases)
    assert out["total"] == len(cases) == 123
    assert out["confident_disagree"] == 0, out["disagreements"]
    assert out["offline_wrong_auto"] <= out["cloud_wrong_auto"], out["wrong_auto_cases"]
    assert out["labelled"] == len(cases)
    assert out["cloud_wrong_auto"] == song_eval.run_eval(cloud, cases)["wrong_auto"]
    assert out["offline_wrong_auto"] == song_eval.run_eval(off, cases)["wrong_auto"]
    for kind in song_eval.DISAGREEMENT_ORDER:
        assert out[kind] == sum(1 for d in out["disagreements"] if d["type"] == kind)
    assert out["offline_downgraded"] == out["downgraded_cloud_best_karaoke"] + out["downgraded_cloud_best_non_karaoke"]


def _fake(results: dict[str, dict[str, Any]]) -> Any:
    class Fake:
        def identify(self, q: str, limit: int = 5) -> dict[str, Any]:
            return results[q]

    return Fake()


def _r(status: str, artist: str | None = None, title: str | None = None, karaoke: bool = True) -> dict[str, Any]:
    best = {"artist": artist, "title": title, "score": 0.9, "karaoke": karaoke} if artist else None
    return {"status": status, "best": best, "candidates": [best] if best else []}


def test_dual_eval_classifies_each_pair() -> None:
    cloud = _fake(
        {
            "a": _r("confident", "Abba", "One"),  # both sure, different songs → confident_disagree (gated)
            "b": _r("candidates", "Abba", "One"),  # offline sure on the cloud's own best guess
            "b2": _r("candidates", "Abba", "Two"),  # offline sure on another song than the cloud's guess
            "b3": _r("none"),  # cloud has nothing, offline is sure
            "c": _r("confident", "Abba", "One"),  # offline asks → downgraded
            "d": _r("confident", "Abba", "One (feat. Yes)"),  # version suffix → same song
            "d2": _r("confident", "Abba & Yes", "One (Live)"),  # credit + version variant → same song
            "e": _r("none"),
            "f": _r("confident", "N", "Two", karaoke=False),  # non-karaoke cloud answer, offline has none
        }
    )
    off = _fake(
        {
            "a": _r("confident", "Zed", "One"),
            "b": _r("confident", "Abba", "One"),
            "b2": _r("confident", "Abba", "One"),
            "b3": _r("confident", "Abba", "One"),
            "c": _r("candidates", "Abba", "One"),
            "d": _r("confident", "abba", "One"),
            "d2": _r("confident", "Abba feat. Yes", "One"),
            "e": _r("none"),
            "f": _r("none"),
        }
    )
    qs = ["a", "b", "b2", "b3", "c", "d", "d2", "e", "f"]
    out = song_eval.dual_eval(cloud, off, [{"q": q, "artist": None, "title": None} for q in qs])
    assert out["total"] == 9
    assert out["confident_disagree"] == 1
    assert (out["offline_more_confident"], out["offline_confident_cloud_unsure_other"]) == (1, 2)
    assert out["offline_downgraded"] == 2
    assert (out["downgraded_cloud_best_karaoke"], out["downgraded_cloud_best_non_karaoke"]) == (1, 1)
    # every case here is a negative (no label): any confident answer is wrong
    assert (out["labelled"], out["cloud_wrong_auto"], out["offline_wrong_auto"]) == (9, 5, 6)
    by_q = {d["q"]: d["type"] for d in out["disagreements"]}
    assert by_q == {
        "a": "confident_disagree",
        "b": "offline_more_confident",
        "b2": "offline_confident_cloud_unsure_other",
        "b3": "offline_confident_cloud_unsure_other",
        "c": "offline_downgraded",
        "f": "offline_downgraded",
    }
    assert [d["q"] for d in out["disagreements"]] == ["a", "b", "b2", "b3", "c", "f"]  # case order
    a = out["disagreements"][0]
    assert a["cloud"] == {"status": "confident", "artist": "Abba", "title": "One"}
    assert a["offline"] == {"status": "confident", "artist": "Zed", "title": "One"}


@pytest.mark.parametrize(
    ("cloud", "offline"),
    [
        (("Gretchen Wilson", "Redneck Woman (live)"), ("Gretchen Wilson", "Redneck Woman")),
        (("Britney Spears", "Hold It Against Me (remix)"), ("Britney Spears", "Hold It Against Me (Jump Smokers)")),
        (("Michael Andrews & Gary Jules", "Mad World"), ("Michael Andrews feat. Gary Jules", "Mad World")),
        (("Little Mix & Sean Paul", "Hair (Fsolo)"), ("Little Mix ft Sean Paul", "Hair")),
        (("Bob Seger & the Silver Bullet Band", "Mainstreet"), ("Bob Seger", "Mainstreet")),
        (("Shankar Mahadevan", "Dil Chahta Hai(Reprise)"), ("Shankar Mahadevan", "Dil Chahta Hai")),
        (("Masked Wolf", "Astronaut In The Ocean - Alok Remix"), ("Masked Wolf", "Astronaut In The Ocean")),
    ],
)
def test_classify_pair_version_and_credit_variants_are_the_same_song(
    cloud: tuple[str, str], offline: tuple[str, str]
) -> None:
    """Real A10 prod disagreements that were the same song (old song_key comparison)."""
    rc, ro = _r("confident", *cloud), _r("confident", *offline)
    assert song_eval.classify_pair(rc, ro) is None
    assert song_eval.classify_pair(_r("candidates", *cloud), ro) == "offline_more_confident"


def test_classify_pair_both_confident_different_song_is_counted() -> None:
    rc, ro = _r("confident", "Elephante", "Troubled"), _r("confident", "Cage The Elephant", "Trouble")
    assert song_eval.classify_pair(rc, ro) == "confident_disagree"
    # same title, different artist; and a sequel ("Pt. II" vs "Pt. III") are different songs
    assert song_eval.classify_pair(_r("confident", "Kesha", "Die Young"), _r("confident", "Sylvan Esso", "Die Young"))
    assert song_eval.classify_pair(
        _r("confident", "Pink Floyd", "Another Brick in the Wall, Pt. II"),
        _r("confident", "Pink Floyd", "Another Brick in the Wall, Pt. III"),
    )


def test_classify_pair_cloud_unsure_is_reported_not_gated() -> None:
    off = _r("confident", "Lana Del Rey", "Dealer")
    assert (
        song_eval.classify_pair(_r("candidates", "Lola Young", "Dealer"), off) == "offline_confident_cloud_unsure_other"
    )
    assert song_eval.classify_pair(_r("none"), off) == "offline_confident_cloud_unsure_other"
    assert song_eval.classify_pair(_r("candidates", "Lana Del Rey", "Dealer"), off) == "offline_more_confident"
    assert song_eval.classify_pair(_r("candidates", "A", "B"), _r("candidates", "C", "D")) is None


def test_dual_eval_label_wrong_counts() -> None:
    """Label correctness is run_eval's (``outcome``/``_is``): version suffixes stripped, no
    fuzzy titles, and any confident answer on a negative case is wrong."""
    cloud = _fake(
        {
            "right": _r("confident", "Gretchen Wilson", "Redneck Woman (live)"),  # variant of the label: right
            "cloud_wrong": _r("confident", "Elephante", "Troubled"),
            "off_wrong": _r("candidates", "Cage The Elephant", "Trouble"),
            "fuzzy": _r("confident", "Cage The Elephant", "Trouble"),
            "title_only": _r("confident", "Anyone", "Hurt"),
            "artist_only": _r("confident", "Muse", "Uprising"),
            "negative": _r("confident", "Abba", "Waterloo"),
            "negative_unsure": _r("candidates", "Abba", "Waterloo"),
        }
    )
    off = _fake(
        {
            "right": _r("confident", "Gretchen Wilson", "Redneck Woman"),
            "cloud_wrong": _r("confident", "Cage The Elephant", "Trouble"),
            "off_wrong": _r("confident", "Elephante", "Troubled"),
            "fuzzy": _r("confident", "Cage The Elephant", "Troubled"),  # >=92% fuzzy, still not the label
            "title_only": _r("confident", "Someone", "Hurt (Live)"),
            "artist_only": _r("confident", "Blur", "Song 2"),
            "negative": _r("confident", "Abba", "Waterloo"),
            "negative_unsure": _r("confident", "Abba", "Waterloo"),
        }
    )
    trouble = {"artist": "Cage The Elephant", "title": "Trouble"}
    cases = [
        {"q": "right", "artist": "Gretchen Wilson", "title": "Redneck Woman"},
        {"q": "cloud_wrong", **trouble},
        {"q": "off_wrong", **trouble},
        {"q": "fuzzy", **trouble},
        {"q": "title_only", "artist": None, "title": "Hurt"},
        {"q": "artist_only", "artist": "Muse", "title": None},
        {"q": "negative", "artist": None, "title": None, "kind": "negative"},
        {"q": "negative_unsure", "artist": None, "title": None, "kind": "negative"},
    ]
    out = song_eval.dual_eval(cloud, off, cases)
    assert (out["labelled"], out["cloud_wrong_auto"], out["offline_wrong_auto"]) == (8, 2, 5)
    wrong = {w["q"]: (w["cloud_wrong"], w["offline_wrong"]) for w in out["wrong_auto_cases"]}
    assert wrong == {
        "cloud_wrong": (True, False),
        "off_wrong": (False, True),
        "fuzzy": (False, True),
        "artist_only": (False, True),
        "negative": (True, True),
        "negative_unsure": (False, True),
    }
    assert out["wrong_auto_cases"][0]["want"] == trouble
    # one definition: dual_eval's cloud side agrees with run_eval on the same cases
    assert out["cloud_wrong_auto"] == song_eval.run_eval(cloud, cases)["wrong_auto"]
    assert out["offline_wrong_auto"] == song_eval.run_eval(off, cases)["wrong_auto"]


# ---- mobile parity fixtures ---------------------------------------------------------


def test_parity_fixture_is_fresh(tmp_path: Path) -> None:
    """tests/fixtures/song_search/{offline_fixture.db,parity.json} match a fresh
    regeneration (refresh: python scripts/emit_search_parity_fixtures.py)."""
    subprocess.run(
        [sys.executable, str(REPO / "scripts" / "emit_search_parity_fixtures.py"), "--out-dir", str(tmp_path)],
        check=True,
        cwd=REPO,
    )
    assert (tmp_path / "parity.json").read_text(encoding="utf-8") == (FIXTURES / "parity.json").read_text(
        encoding="utf-8"
    )

    def dump(db: Path) -> list[str]:
        conn = sqlite3.connect(db)
        try:
            return list(conn.iterdump())
        finally:
            conn.close()

    assert dump(tmp_path / "offline_fixture.db") == dump(FIXTURES / "offline_fixture.db")


def test_parity_fixture_shape() -> None:
    cases = json.loads((FIXTURES / "parity.json").read_text(encoding="utf-8"))
    eval_qs = [c["q"] for c in song_eval.load_cases(FIXTURES / "song_id_eval.jsonl")]
    qs = [c["q"] for c in cases]
    assert qs[: len(eval_qs)] == eval_qs
    assert {"", "karaoke", "x", "🎤🎤", "a" * 250, "שיר", "歌"} <= set(qs)
    for c in cases:
        assert set(c) == {"q", "identify", "kind", "groups"}
        ident = c["identify"]
        if ident["best"] is None:
            assert ident["status"] == "none" and c["kind"] is None and c["groups"] == []
            continue
        assert ident["best"]["song_key"] == song_key(ident["best"]["artist"], ident["best"]["title"])
        assert all("score" in m and "song_key" in m for m in ident["candidates"])
        assert c["kind"] in {"same", "cosmetic", "completed", "content"}


# ---- vocal range (Phase 3c) ---------------------------------------------------------------

VR_FIXTURES = FIXTURES.parent / "vocal_range"


def _vr() -> Any:
    from karaoke_decide.vocal_range import tsv

    return tsv.load(
        VR_FIXTURES / "song_vocal_range_fixture.tsv", VR_FIXTURES / "artist_vocal_range_fixture.tsv", run="vr-run-1"
    )


@pytest.fixture(scope="module")
def built_vr(sources: tuple[Path, Path], tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("offline-vr") / "offline.db"
    build_offline_db(sources[0], sources[1], out, run="20261018-000000", vocal_range=_vr())
    return out


VR_COLS = ("vr_low", "vr_high", "vr_p10", "vr_p50", "vr_p90", "vr_tier", "vr_octave_conf")


def test_offline_schema_stays_1_with_vr_columns(built_vr: Path) -> None:
    """Additive columns, no version bump: the app requires an exact schema match."""
    meta = dict(_rows(built_vr, "SELECT key, value FROM meta"))
    assert OFFLINE_SCHEMA_VERSION == 1 and meta["offline_schema_version"] == "1"
    assert meta["vocal_range_run"] == "vr-run-1"
    cols = {r[1]: r[2] for r in _rows(built_vr, "PRAGMA table_info(songs)")}
    assert {c: cols[c] for c in VR_COLS} == {
        "vr_low": "REAL", "vr_high": "REAL", "vr_p10": "REAL", "vr_p50": "REAL", "vr_p90": "REAL",
        "vr_tier": "INTEGER", "vr_octave_conf": "REAL",
    }  # fmt: skip


def _vr_row(db: Path, key: str) -> tuple[Any, ...]:
    rows = _rows(db, f"SELECT {', '.join(VR_COLS)} FROM songs WHERE song_key = ?", (key,))
    assert rows, key
    return rows[0]


def test_offline_vr_columns_song_then_artist_fallback(built_vr: Path) -> None:
    assert _vr_row(built_vr, "muse|uprising") == (48.0, 70.0, 52.0, 59.0, 66.0, 3, 0.92)
    assert _vr_row(built_vr, "muse|starlight")[5] == 4
    hello = _vr_row(built_vr, "adele|hello")
    assert hello[5] == 2 and hello[6] is None  # Gemini confidence never becomes octave confidence
    assert _vr_row(built_vr, "taylor swift|shake it off") == (57.0, 76.0, 60.0, 66.0, 72.0, 1, None)  # rough
    assert _vr_row(built_vr, "lady gaga|shallow") == (None,) * 7
    # the artist fallback covers every song of the artist without its own row
    assert _vr_row(built_vr, "muse|hysteria")[5] == 1


def test_offline_builds_without_vocal_range(built: tuple[Path, Any]) -> None:
    meta = dict(_rows(built[0], "SELECT key, value FROM meta"))
    assert meta["vocal_range_run"] == ""
    assert _rows(built[0], "SELECT count(*) FROM songs WHERE vr_p50 IS NOT NULL OR vr_tier IS NOT NULL") == [(0,)]
    r = SongIdentifier(built[0], margin_bonus=OFFLINE_MARGIN_BONUS).identify("starlite muse")
    assert r["status"] == "confident"


def test_parity_fixture_carries_vocal_ranges() -> None:
    assert _vr_row(FIXTURES / "offline_fixture.db", "muse|starlight")[5] == 4
    meta = dict(_rows(FIXTURES / "offline_fixture.db", "SELECT key, value FROM meta"))
    assert meta["vocal_range_run"] == "fixture"
