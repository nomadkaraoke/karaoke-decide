"""Filename parsing for the standalone NomadPC stem script."""

import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[3] / "scripts" / "vocal_range" / "analyze_nomad_stems.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("analyze_nomad_stems", _PATH)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_filename_parse(mod):
    assert mod.parse_name("NOMAD-0001 - Zed Quartz - Midnight Tram.flac") == (
        "NOMAD-0001",
        "Zed Quartz",
        "Midnight Tram",
    )
    assert mod.parse_name("NOMAD-1234 - Beyoncé Ñandú - Canción de ñu.mp3") == (
        "NOMAD-1234",
        "Beyoncé Ñandú",
        "Canción de ñu",
    )


def test_filename_parse_ambiguous_dash_splits_on_first(mod):
    # Artist containing " - " is inherently ambiguous: first separator wins,
    # the rest stays in the title.
    assert mod.parse_name("NOMAD-0042 - A - B - C.flac") == ("NOMAD-0042", "A", "B - C")


@pytest.mark.parametrize(
    "name",
    ["random.flac", "NOMAD-12 - A - B.flac", "NOMAD-0001 - OnlyArtist.flac", "NOMAD-0001 - - T.flac"],
)
def test_filename_parse_unparseable(mod, name):
    assert mod.parse_name(name) is None


def test_done_ids_resume(mod, tmp_path):
    out = tmp_path / "o.csv"
    assert mod.done_ids(out) == set()
    out.write_text("source_id,artist,title\nNOMAD-0001,a,b\n")
    assert mod.done_ids(out) == {"NOMAD-0001"}
