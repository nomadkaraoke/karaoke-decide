"""run_eval / CLI over the fixture index (vendored from kjbox scripts/song_id_eval.py).

Fixture coverage of the labelled set is partial: these tests pin the counting, not
kjbox's full-index numbers (Task A10 checks those in GCP)."""

from pathlib import Path

import pytest

from karaoke_decide.song_search import eval as song_eval
from karaoke_decide.song_search.build_song_id import build
from karaoke_decide.song_search.identify import SongIdentifier

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "song_search"


@pytest.fixture(scope="module")
def db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("songid-eval") / "song_id.db"
    build([FIXTURES / "songs_fixture.tsv"], out, run="eval")
    return out


def _case(q: str, artist: str | None, title: str | None) -> dict[str, str | None]:
    return {"q": q, "artist": artist, "title": title, "kind": "t"}


def test_run_eval_counts_each_outcome(db: Path) -> None:
    cases = [
        _case("starlite muse", "Muse", "Starlight"),  # confident-correct
        _case("starlite muse", "Taylor Swift", "Starlight"),  # confident-WRONG (other artist)
        _case("xqzv blorp wibble", None, None),  # none (negative: fine)
        _case("baby", "Brandy", "Baby"),  # candidates (Brandy among them)
        _case("Seal kiss", "Seal", "Kiss from a Rose"),  # confident-correct
    ]
    out = song_eval.run_eval(SongIdentifier(db), cases)
    assert {k: out[k] for k in ("total", "auto", "wrong_auto", "candidates", "none")} == {
        "total": 5,
        "auto": 2,
        "wrong_auto": 1,
        "candidates": 1,
        "none": 1,
    }
    assert out["wrong"] == [
        {
            "q": "starlite muse",
            "kind": "t",
            "want": {"artist": "Taylor Swift", "title": "Starlight"},
            "got": {"artist": "Muse", "title": "Starlight", "score": out["wrong"][0]["got"]["score"]},
        }
    ]


def test_confident_on_a_negative_case_is_wrong(db: Path) -> None:
    assert song_eval.run_eval(SongIdentifier(db), [_case("wonderwall", None, None)])["wrong_auto"] == 1


def test_is_ignores_version_suffixes_and_matches_any_credit() -> None:
    case = {"artist": "Zedd", "title": "Clarity"}
    assert song_eval._is(case, {"artist": "Zedd feat. Foxes", "title": "Clarity - Remastered"})
    assert not song_eval._is(case, {"artist": "Foxes", "title": "Clarity"})
    assert song_eval._is({"artist": None, "title": "Hallelujah"}, {"artist": "Jeff Buckley", "title": "Hallelujah"})


def test_labelled_set_on_the_fixture_has_no_wrong_auto(db: Path) -> None:
    cases = song_eval.load_cases(FIXTURES / "song_id_eval.jsonl")
    out = song_eval.run_eval(SongIdentifier(db), cases)
    assert out["total"] == 123
    assert out["auto"] + out["wrong_auto"] + out["candidates"] + out["none"] == out["total"]
    assert out["wrong_auto"] == 0, out["wrong"]


def test_synthetic_cases_are_seeded(db: Path) -> None:
    a = song_eval.synthetic_cases(db, 5)
    assert a == song_eval.synthetic_cases(db, 5)
    assert len(a) == 30
    assert {c["kind"] for c in a} >= {"syn-title-only", "syn-exact", "syn-typo-title"}


def test_cli_smoke(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert song_eval.main(["--db", str(db), "--cases", str(FIXTURES / "song_id_eval.jsonl")]) == 0
    out = capsys.readouterr().out
    assert "TOTAL 123:" in out and "latency ms:" in out
    assert song_eval.main(["--db", str(db), "--synthetic", "3", "--margin-bonus", "0.03", "--verbose"]) == 0
    assert "TOTAL 18:" in capsys.readouterr().out
