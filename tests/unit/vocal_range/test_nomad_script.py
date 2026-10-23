"""Filename parsing for the standalone NomadPC stem script."""

import importlib.util
import os
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


def test_thread_env_defaults_set(mod):
    import os

    for var in ("OMP_NUM_THREADS", "NUMBA_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        assert var in os.environ


def test_thread_env_respects_preset(monkeypatch):
    import subprocess
    import sys

    code = (
        "import importlib.util,os,sys;"
        f"s=importlib.util.spec_from_file_location('m',{str(_PATH)!r});m=importlib.util.module_from_spec(s);"
        "s.loader.exec_module(m);print(os.environ['OMP_NUM_THREADS'],os.environ['MKL_NUM_THREADS'])"
    )
    env = {"PATH": os.environ["PATH"], "OMP_NUM_THREADS": "3"}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["3", "1"]


def test_trim_torn_line(mod, tmp_path):
    out = tmp_path / "o.csv"
    out.write_text("h\nrow1\nrow2-partial")
    assert mod.trim_torn_line(out) is True
    assert out.read_text() == "h\nrow1\n"
    assert mod.trim_torn_line(out) is False
    only = tmp_path / "p.csv"
    only.write_text("no newline")
    assert mod.trim_torn_line(only) is True
    assert only.read_text() == ""
    assert mod.trim_torn_line(tmp_path / "missing.csv") is False
