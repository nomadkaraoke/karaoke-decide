"""Pure helpers for the one-off Gemini Flash Batch run (request building, parsing, cost).

Prompts carry artist and title ONLY (never lyrics). Gemini Developer API (AI Studio) only.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from karaoke_decide.vocal_range.notes import VOICE_TYPES, note_name_to_midi

BUDGET_USD = 80.0
# gemini-2.5-flash* now 404s for new AI Studio keys ("no longer available to new users"), so the run
# uses gemini-3.6-flash: the cheapest non-Lite Flash that accepts thinking_budget=0 / level "minimal"
# and reported 0 thought tokens in a live check. (3.8-flash has no level below "low".)
DEFAULT_MODEL = "gemini-3.6-flash"

# Batch-tier (50% of standard) USD per 1M tokens (input, output). Verified 2026-10-22 at
# https://ai.google.dev/gemini-api/docs/pricing ; re-verify before every run. The gemini-3.x Flash
# batch prices are a promo that doubles on 2027-01-01 (0.75 / 3.75 for 3.6 and 3.8 Flash).
PRICES_PER_M: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash": (0.15, 1.25),  # unavailable to new keys; kept as the cost-test anchor
    "gemini-2.5-flash-lite": (0.05, 0.20),  # unavailable to new keys
    "gemini-3.1-flash-lite": (0.125, 0.75),
    "gemini-3.5-flash-lite": (0.15, 1.25),
    "gemini-3.6-flash": (0.375, 1.875),  # through 2026-12-31
    "gemini-3.8-flash": (0.375, 1.875),  # through 2026-12-31
}

_NOTE = {"type": "STRING", "description": "Scientific pitch notation, e.g. A2, C#4, Bb3"}
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "low_note": _NOTE,
        "high_note": _NOTE,
        "typical_low": _NOTE,
        "typical_high": _NOTE,
        "voice_type": {"type": "STRING", "enum": list(VOICE_TYPES)},
        "confidence": {"type": "NUMBER"},
        "unknown": {"type": "BOOLEAN"},
    },
    "required": ["low_note", "high_note", "typical_low", "typical_high", "voice_type", "confidence", "unknown"],
}

_SYSTEM = (
    "You estimate singing ranges for a karaoke app, from general music knowledge. "
    "Use scientific pitch notation (middle C = C4) at the pitch the lead vocal actually sings on the "
    "original recording. low_note/high_note are the lowest/highest sung notes; typical_low/typical_high "
    "bracket where most of the singing sits. If you do not recognise the artist or song well enough to "
    'estimate, set "unknown": true (and fill the other fields with your best guess anyway). '
    "confidence is 0 to 1."
)


def build_request(kind: Literal["song", "artist"], key: str, artist: str, title: str | None) -> dict[str, Any]:
    """One Batch JSONL line."""
    if kind == "song":
        ask = f'Song: "{title}" by {artist}. Estimate the lead vocal range of this song.'
    else:
        ask = f"Artist: {artist}. Estimate the lead vocal range across this artist's typical songs."
    return {
        "key": key,
        "request": {
            "contents": [{"role": "user", "parts": [{"text": ask}]}],
            "system_instruction": {"parts": [{"text": _SYSTEM}]},
            "generation_config": {
                "response_mime_type": "application/json",
                "response_schema": RESPONSE_SCHEMA,
                "max_output_tokens": 200,
                "thinking_config": {"thinking_budget": 0},
                "temperature": 0,
            },
        },
    }


def parse_response(text: str) -> dict[str, Any] | None:
    """Parse one model answer; None when unknown, malformed, or low > high."""
    try:
        d = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("unknown") is not False:
        return None
    notes = []
    for field in ("low_note", "high_note", "typical_low", "typical_high"):
        v = d.get(field)
        m = note_name_to_midi(v) if isinstance(v, str) else None
        if m is None:
            return None
        notes.append(m)
    low, high, p10, p90 = notes
    if low > high:
        return None
    vt = d.get("voice_type")
    try:
        conf = min(1.0, max(0.0, float(d.get("confidence", 0))))
    except (TypeError, ValueError):
        conf = 0.0
    return {
        "low_midi": low,
        "high_midi": high,
        "p10": p10,
        "p50": (p10 + p90) // 2,
        "p90": p90,
        "voice_type": vt if vt in VOICE_TYPES else None,
        "confidence": conf,
    }


def estimate_cost(n: int, avg_in: float, avg_out: float, model: str) -> float:
    """Batch cost in USD for ``n`` requests."""
    if model not in PRICES_PER_M:
        raise ValueError(f"no verified batch price for model {model!r}; add it to PRICES_PER_M")
    p_in, p_out = PRICES_PER_M[model]
    return n * (avg_in * p_in + avg_out * p_out) / 1e6
