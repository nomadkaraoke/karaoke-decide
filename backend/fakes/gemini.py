"""Fake screenshot reader: the same three candidates for any image, or a failure under knob
``screenshot=fail`` (the extract route then answers 503 ``screenshot_unavailable``)."""

from __future__ import annotations

from backend.fakes.state import STATE
from backend.services.screenshot_import_service import RawCandidate


class FakeScreenshotFailedError(RuntimeError):
    """Knob ``screenshot=fail``: stands in for a Gemini error."""


FAKE_CANDIDATES = (
    RawCandidate("artist", "Muse", None, 0.95),
    RawCandidate("song", "Adele", "Hello", 0.9),
    RawCandidate("artist", "The Invented Band", None, 0.6),  # not in the fixture catalog
)


class FakeScreenshotExtractor:
    async def extract(self, images: list[tuple[bytes, str]]) -> list[RawCandidate]:
        if STATE.knob("screenshot") == "fail":
            raise FakeScreenshotFailedError("fake Gemini failure (knob screenshot=fail)")
        return list(FAKE_CANDIDATES)
