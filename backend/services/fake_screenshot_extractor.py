"""``get_screenshot_extractor`` imports the fake from here when DECIDE_FAKE_EXTERNALS is on."""

from backend.fakes.gemini import FakeScreenshotExtractor

__all__ = ["FakeScreenshotExtractor"]
