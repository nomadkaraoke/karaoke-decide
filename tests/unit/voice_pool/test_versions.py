"""Community-version choice: YouTube ids, the oEmbed embeddability probe, ordering."""

from __future__ import annotations

import httpx
import pytest

from karaoke_decide.voice_pool.models import Version
from karaoke_decide.voice_pool.versions import (
    RateLimiter,
    VersionStats,
    choose_versions,
    probe_embeddable,
    youtube_id,
)

NO_WAIT = RateLimiter(0)


def v(video_id: str, brand: str = "CC", rank: int = 0, embeddable: bool | None = None) -> Version:
    return Version(video_id=video_id, brand_code=brand, brand_name=brand, rank=rank, embeddable=embeddable)


def test_youtube_id_forms():
    assert youtube_id("https://youtu.be/AbCdEfGhIj0") == "AbCdEfGhIj0"
    assert youtube_id("https://youtu.be/AbCdEfGhIj0?t=30") == "AbCdEfGhIj0"
    assert youtube_id("https://www.youtube.com/watch?v=AbCdEfGhIj0") == "AbCdEfGhIj0"
    assert youtube_id("https://www.youtube.com/watch?v=AbCdEfGhIj0&t=42s") == "AbCdEfGhIj0"
    assert youtube_id("https://www.youtube.com/watch?feature=share&v=Ab-dEf_hIj0&t=1") == "Ab-dEf_hIj0"
    assert youtube_id("https://www.youtube.com/embed/AbCdEfGhIj0?autoplay=1") == "AbCdEfGhIj0"
    assert youtube_id("https://m.youtube.com/shorts/AbCdEfGhIj0") == "AbCdEfGhIj0"
    assert youtube_id("https://example.com/watch?v=AbCdEfGhIj0") is None
    assert youtube_id("https://www.youtube.com/watch?v=short") is None
    assert youtube_id("https://www.youtube.com/watch?v=AbCdEfGhIj0123") is None  # too long
    assert youtube_id("not a url") is None
    assert youtube_id("") is None


@pytest.mark.parametrize(("status", "expected"), [(200, True), (401, False), (403, False), (404, False), (500, None)])
def test_probe_maps_status_codes(status: int, expected: bool | None):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json={} if status == 200 else None)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert probe_embeddable(client, "AbCdEfGhIj0", limiter=NO_WAIT) is expected
    (req,) = seen
    assert req.url.host == "www.youtube.com" and req.url.path == "/oembed"
    assert req.url.params["url"] == "https://www.youtube.com/watch?v=AbCdEfGhIj0"
    assert req.url.params["format"] == "json"


def test_probe_timeout_is_unknown():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert probe_embeddable(client, "AbCdEfGhIj0", limiter=NO_WAIT) is None


def test_rate_limiter_spaces_requests():
    now = [100.0]
    slept: list[float] = []

    def sleep(s: float) -> None:
        slept.append(s)
        now[0] += s

    limiter = RateLimiter(1.0, clock=lambda: now[0], sleep=sleep)
    limiter.wait()  # first call never waits
    now[0] += 0.25
    limiter.wait()
    now[0] += 2.0
    limiter.wait()  # already a second later
    assert slept == [pytest.approx(0.75)]


def test_choose_versions_prefers_embeddable_then_quality():
    blocked = v("blocked0000", rank=0, embeddable=False)
    top = v("toprank0000", rank=0)
    second = v("second00000", rank=1, embeddable=True)
    # Without stats: rank prior decides among the embeddable / unknown ones; blocked is last.
    assert [x.video_id for x in choose_versions([blocked, second, top])] == [
        "toprank0000",
        "second00000",
        "blocked0000",
    ]

    # Stats: rank 0 sung badly, rank 1 sung well -> quality overturns the rank prior.
    stats = {
        "toprank0000": VersionStats(good=0, not_great=8, early_exit=4, plays=12),
        "second00000": VersionStats(good=10, not_great=0, plays=10),
    }
    assert [x.video_id for x in choose_versions([blocked, top, second], stats)][:2] == ["second00000", "toprank0000"]

    # Stats can mark a version non-embeddable (reported by the app).
    stats = {"toprank0000": VersionStats(embeddable=False)}
    assert choose_versions([top, second], stats)[0].video_id == "second00000"


def test_choose_versions_ties_break_on_brand_priority():
    # Same rank, no stats -> equal score: NOMAD first, then COMMUNITY_BRANDS order, unknown last.
    a = v("ccccccccccc", brand="CC", rank=0)
    b = v("nnnnnnnnnnn", brand="NOMAD", rank=0)
    c = v("fffffffffff", brand="FBK", rank=0)
    d = v("zzzzzzzzzzz", brand="ZZZ", rank=0)
    assert [x.brand_code for x in choose_versions([d, c, a, b])] == ["NOMAD", "CC", "FBK", "ZZZ"]
    assert choose_versions([]) == []
