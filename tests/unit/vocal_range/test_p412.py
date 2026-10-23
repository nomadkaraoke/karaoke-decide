"""Wikidata P412 (voice type) mapping."""

import csv
from collections import Counter
from pathlib import Path

from karaoke_decide.vocal_range.notes import VOICE_TYPES
from karaoke_decide.vocal_range.p412 import P412_MAP, load_p412

CSV = Path(__file__).resolve().parents[3] / "data" / "vocal_range" / "wikidata_p412.csv"


def test_p412_map_covers_frequent_labels():
    with open(CSV, newline="", encoding="utf-8") as f:
        counts = Counter(r["vtLabel"] for r in csv.DictReader(f))
    missing = [label for label, n in counts.items() if n >= 9 and label not in P412_MAP]
    assert missing == []


def test_p412_map_values_valid():
    assert all(v is None or v in VOICE_TYPES for v in P412_MAP.values())


def test_p412_pinned_examples():
    m = P412_MAP
    assert m["contralto"] == m["alto"] == m["countertenor"] == "alto"
    assert m["bass-baritone"] == m["basso profondo"] == "bass"
    assert m["baritenor"] == m["lyric baritone"] == m["baryton-Martin"] == "baritone"
    for label in ("Heldentenor", "lyric tenor", "tenore di grazia", "haute-contre", "Tenore contraltino"):
        assert m[label] == "tenor"
    assert m["mezzo-soprano"] == m["lyric mezzo-soprano"] == "mezzo"
    for label in ("coloratura soprano", "Soubrette", "dramatic soprano"):
        assert m[label] == "soprano"
    for label in ("male voice", "death growl", "castrato"):
        assert m[label] is None


def test_p412_multi_label(tmp_path):
    p = tmp_path / "p.csv"
    p.write_text("mbid,vtLabel\na,tenor\na,baritone\nb,soprano\nc,death growl\nd,unheard-of\n")
    out = load_p412(p)
    assert out == {"a": {"tenor", "baritone"}, "b": {"soprano"}}
