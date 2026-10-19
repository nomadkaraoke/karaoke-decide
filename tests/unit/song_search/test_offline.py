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


def _karaoke_title_norms(song_id_db: Path) -> set[str]:
    return {r[0] for r in _rows(song_id_db, "SELECT nt FROM songs WHERE karaoke = 1")}


# ---- contents ---------------------------------------------------------------------


def test_offline_has_only_karaoke_songs_and_rivals(sources: tuple[Path, Path], built: tuple[Path, Any]) -> None:
    song_id_db, _ = sources
    out, stats = built
    karaoke = set(_rows(song_id_db, "SELECT artist, title, pop FROM songs WHERE karaoke = 1"))
    rivals = {(a, t, p) for a, t, p, _k in select_rivals(song_id_db, _karaoke_title_norms(song_id_db))}
    assert karaoke and rivals and not karaoke & rivals
    assert set(_rows(out, "SELECT artist, title, pop FROM songs WHERE karaoke = 1")) == karaoke
    assert set(_rows(out, "SELECT artist, title, pop FROM songs WHERE karaoke = 0")) == rivals
    assert stats["songs"] == len(karaoke) and stats["rivals"] == len(rivals)
    # Fewer songs than the cloud: the non-karaoke songs that rival nothing are left out.
    n_cloud = _rows(song_id_db, "SELECT count(*) FROM songs")[0][0]
    assert stats["songs"] + stats["rivals"] < n_cloud
    assert stats["bytes"] == out.stat().st_size


def test_select_rivals_rule(sources: tuple[Path, Path]) -> None:
    """Non-karaoke, same song_norm title as a karaoke song, pop ≥ 30, top N per title."""
    song_id_db, _ = sources
    rivals = select_rivals(song_id_db, {"baby"})
    assert ("Madison Beer", "Baby", 55, 0) in rivals
    assert all(k == 0 and p is not None and p >= 30 for _a, _t, p, k in rivals)
    assert {t for _a, t, _p, _k in rivals} == {"Baby"}
    assert select_rivals(song_id_db, {"baby"}, min_pop=56) == [r for r in rivals if r[2] >= 56]
    assert len(select_rivals(song_id_db, {"baby"}, per_title_cap=1)) == 1
    assert select_rivals(song_id_db, set()) == []


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
    assert out["offline_downgraded"] == sum(1 for d in out["disagreements"] if d["type"] == "offline_downgraded")


def _fake(results: dict[str, dict[str, Any]]) -> Any:
    class Fake:
        def identify(self, q: str, limit: int = 5) -> dict[str, Any]:
            return results[q]

    return Fake()


def _r(status: str, artist: str | None = None, title: str | None = None) -> dict[str, Any]:
    best = {"artist": artist, "title": title, "score": 0.9} if artist else None
    return {"status": status, "best": best, "candidates": [best] if best else []}


def test_dual_eval_classifies_each_pair() -> None:
    cloud = _fake(
        {
            "a": _r("confident", "X", "One"),  # offline names another song → disagree
            "b": _r("candidates", "X", "One"),  # offline confident where the cloud asks → disagree
            "c": _r("confident", "X", "One"),  # offline asks → downgraded
            "d": _r("confident", "X", "One (feat. Y)"),  # same song → agree
            "e": _r("none"),
        }
    )
    off = _fake(
        {
            "a": _r("confident", "Z", "One"),
            "b": _r("confident", "X", "One"),
            "c": _r("candidates", "X", "One"),
            "d": _r("confident", "x", "One"),
            "e": _r("none"),
        }
    )
    cases = [{"q": q, "artist": None, "title": None} for q in "abcde"]
    out = song_eval.dual_eval(cloud, off, cases)
    assert (out["total"], out["confident_disagree"], out["offline_downgraded"]) == (5, 2, 1)
    by_q = {d["q"]: d for d in out["disagreements"]}
    assert set(by_q) == {"a", "b", "c"}
    assert by_q["a"]["type"] == by_q["b"]["type"] == "confident_disagree"
    assert by_q["a"]["cloud"] == {"status": "confident", "artist": "X", "title": "One"}
    assert by_q["a"]["offline"] == {"status": "confident", "artist": "Z", "title": "One"}
    assert by_q["c"]["type"] == "offline_downgraded"


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
