"""Pure request-building / parsing / cost helpers for the Gemini batch run."""

import json

import pytest

from karaoke_decide.vocal_range.gemini import (
    BUDGET_USD,
    DEFAULT_MODEL,
    PRICES_PER_M,
    build_request,
    estimate_cost,
    parse_response,
)


def test_request_shape():
    line = build_request("song", "song:abc", "Some Artist", "Some Title")
    assert line["key"] == "song:abc"
    json.dumps(line)  # JSONL-serialisable
    cfg = line["request"]["generation_config"]
    assert cfg["response_mime_type"] == "application/json"
    assert cfg["thinking_config"] == {"thinking_budget": 0}
    assert cfg["max_output_tokens"] == 200
    assert cfg["temperature"] == 0
    props = cfg["response_schema"]["properties"]
    assert set(props) == {"low_note", "high_note", "typical_low", "typical_high", "voice_type", "confidence", "unknown"}
    assert set(props["voice_type"]["enum"]) == {"bass", "baritone", "tenor", "alto", "mezzo", "soprano"}
    text = line["request"]["contents"][0]["parts"][0]["text"]
    assert "Some Artist" in text and "Some Title" in text
    # Gemini Developer API only: no Vertex-only fields.
    assert "vertex" not in json.dumps(line).lower()


def test_artist_request_has_no_title():
    line = build_request("artist", "artist:mbid", "Some Artist", None)
    text = line["request"]["contents"][0]["parts"][0]["text"]
    assert "Some Artist" in text and "None" not in text


GOOD = {
    "low_note": "A2",
    "high_note": "E4",
    "typical_low": "C3",
    "typical_high": "C4",
    "voice_type": "baritone",
    "confidence": 0.7,
    "unknown": False,
}


def test_parse_valid():
    out = parse_response(json.dumps(GOOD))
    assert out == {
        "low_midi": 45,
        "high_midi": 64,
        "p10": 48,
        "p90": 60,
        "p50": 54,
        "voice_type": "baritone",
        "confidence": 0.7,
    }


def test_parse_accepts_flats_and_sharps():
    out = parse_response(json.dumps({**GOOD, "low_note": "B♭2", "high_note": "F#4"}))
    assert out is not None and out["low_midi"] == 46 and out["high_midi"] == 66


def test_parse_unknown_none():
    assert parse_response(json.dumps({**GOOD, "unknown": True})) is None


def test_parse_bad_note_none():
    assert parse_response(json.dumps({**GOOD, "high_note": "H9"})) is None


def test_parse_low_above_high_none():
    assert parse_response(json.dumps({**GOOD, "low_note": "C5", "high_note": "C3"})) is None


def test_parse_garbage_none():
    assert parse_response("not json") is None
    assert parse_response(json.dumps([1, 2])) is None
    assert parse_response(json.dumps({"unknown": False})) is None


def test_parse_bad_voice_type_confidence_clamped():
    out = parse_response(json.dumps({**GOOD, "voice_type": "robot", "confidence": 3}))
    assert out is not None and out["voice_type"] is None and out["confidence"] == 1.0


def test_estimate_cost_70k():
    assert estimate_cost(70000, 120, 80, "gemini-2.5-flash") == pytest.approx(8.26, rel=0.01)


def test_unknown_model_raises():
    with pytest.raises(ValueError):
        estimate_cost(1, 1, 1, "gemini-nope")


def test_budget_and_prices():
    assert BUDGET_USD == 80.0
    assert "gemini-2.5-flash" in PRICES_PER_M


def test_default_model_has_a_price():
    assert DEFAULT_MODEL in PRICES_PER_M
