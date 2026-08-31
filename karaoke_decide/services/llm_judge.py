"""LLM karaoke-suitability judge (Gemini via Vertex AI).

Metadata alone can't tell "the vocals only cover a third of the song" or "these
lyrics are the wrong song" — the failure modes that dominate on electronic/DnB
libraries. This judge reads the actual lyrics plus the metadata and returns a
keep/reject verdict with a reason.

Uses the same Vertex AI path as the translation pipeline (ADC auth, no API key).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from karaoke_decide.core.exceptions import ExternalServiceError

_SYSTEM = """You are judging whether a song is a good KARAOKE candidate — a song a \
person would actually enjoy singing at a karaoke night. The user reviews every \
suggestion before producing it and can trim/fade tracks, so bias toward KEEP: \
only REJECT when you are clearly confident it is a bad karaoke song.

REJECT only if ONE of these is clearly true:
- It is MOSTLY INSTRUMENTAL — the vocals genuinely occupy only a small fraction \
of the track (a handful of short lines with long stretches of nothing to sing).
- The lyrics look wrong, garbled, or clearly not the real lyrics for THIS song \
(mismatched, machine-mangled, or obviously a different song's lyrics).
- It is SO repetitive there is almost no real content to sing (e.g. one phrase \
looped for the whole song).

Do NOT reject just because:
- The track is long or has an instrumental intro/outro — the user can fade/trim it.
- It is electronic/DnB — a strong vocal hook plus coherent verses is great karaoke.
- `instrumentalness` is high — that estimate is noisy; trust the actual lyrics.

If a song has a solid, coherent set of real singable lyrics (a real chorus and \
verses), KEEP it even if there are instrumental sections. When genuinely \
uncertain, KEEP.

Respond with ONLY a JSON object: {"verdict": "keep" | "reject", "confidence": \
0.0-1.0, "reason": "<one short sentence>"}."""


@dataclass(frozen=True)
class Verdict:
    keep: bool
    confidence: float
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"keep": self.keep, "confidence": self.confidence, "reason": self.reason}


class LlmJudge:
    """Karaoke-suitability judge backed by Vertex AI Gemini."""

    def __init__(self, project: str, location: str, model: str):
        self.project = project
        self.location = location
        self.model = model
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            from google import genai  # imported lazily; heavy optional dep

            self._client = genai.Client(vertexai=True, project=self.project, location=self.location)
        return self._client

    def _build_prompt(self, artist: str, title: str, lyrics: str, metadata: dict[str, Any]) -> str:
        meta = ", ".join(f"{k}={v}" for k, v in metadata.items())
        # Cap lyrics length to keep the call cheap.
        lyrics = lyrics[:4000]
        return (
            f"{_SYSTEM}\n\n"
            f"ARTIST: {artist}\nTITLE: {title}\nMETADATA: {meta}\n\n"
            f"LYRICS (from LRCLIB):\n{lyrics}\n"
        )

    def judge(self, artist: str, title: str, lyrics: str, metadata: dict[str, Any]) -> Verdict:
        """Return a keep/reject Verdict. Raises ExternalServiceError on failure."""
        from google.genai import types

        prompt = self._build_prompt(artist, title, lyrics, metadata)
        try:
            resp = self._get_client().models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    response_mime_type="application/json",
                ),
            )
            text = (resp.text or "").strip()
        except Exception as exc:  # noqa: BLE001 - normalize SDK errors
            raise ExternalServiceError("VertexAI", str(exc)) from exc

        data = self._parse(text)
        verdict = str(data.get("verdict", "")).lower()
        return Verdict(
            keep=verdict != "reject",  # fail-safe: only an explicit reject drops
            confidence=float(data.get("confidence", 0.0) or 0.0),
            reason=str(data.get("reason", "")).strip(),
        )

    @staticmethod
    def _parse(text: str) -> dict[str, Any]:
        if not text:
            raise ExternalServiceError("VertexAI", "empty response")
        # Be tolerant of accidental code fences.
        if text.startswith("```"):
            text = text.strip("`")
            text = text[text.find("{") : text.rfind("}") + 1]
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ExternalServiceError("VertexAI", f"bad JSON: {text[:120]}") from exc
        if not isinstance(obj, dict):
            raise ExternalServiceError("VertexAI", "response was not an object")
        return obj
