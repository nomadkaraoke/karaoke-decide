"""Community karaoke versions for the voice pool: YouTube ids, the oEmbed embeddability
probe and the best-first ordering the session plays from.

The app plays versions with YouTube's official iframe player, so a version whose owner
disabled embedding (oEmbed 401/403) or that was removed (404) is skipped unless no
alternative exists. oEmbed is a public youtube.com endpoint; we never download streams.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from karaoke_decide.song_search.version_priority import COMMUNITY_DEFAULTS
from karaoke_decide.voice_pool.models import Version

logger = logging.getLogger(__name__)

OEMBED_URL = "https://www.youtube.com/oembed"
OEMBED_TIMEOUT_S = 10.0

_YT_ID_RE = re.compile(
    r"(?:youtu\.be/|youtube(?:-nocookie)?\.com/(?:watch\?(?:[^&#]*&)*v=|embed/|shorts/|live/|v/))"
    r"([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])"
)

# Tie-break: NOMAD first, then the registry's community order; unknown brands after.
BRAND_ORDER: tuple[str, ...] = ("NOMAD", *(c for c in COMMUNITY_DEFAULTS if c != "NOMAD"))
_BRAND_INDEX = {code: i for i, code in enumerate(BRAND_ORDER)}


def youtube_id(url: str | None) -> str | None:
    """The 11-char video id of a YouTube watch / youtu.be / embed / shorts URL, else None."""
    m = _YT_ID_RE.search(url or "")
    return m.group(1) if m else None


class RateLimiter:
    """At most one ``wait()`` return per ``min_interval_s`` (process-local)."""

    def __init__(
        self,
        min_interval_s: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.min_interval_s = min_interval_s
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    def wait(self) -> None:
        now = self._clock()
        if self._last is not None:
            delay = self._last + self.min_interval_s - now
            if delay > 0:
                self._sleep(delay)
                now += delay
        self._last = now


OEMBED_LIMITER = RateLimiter(1.0)  # 1 request/s to youtube.com


def probe_embeddable(client: httpx.Client, video_id: str, *, limiter: RateLimiter = OEMBED_LIMITER) -> bool | None:
    """Whether YouTube lets ``video_id`` play in an embedded player.

    200 -> True; 401/403 -> False (embedding disabled); 404 -> False (removed);
    anything else, or a transport error / timeout -> None (unknown).
    """
    limiter.wait()
    params = {"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"}
    try:
        resp = client.get(OEMBED_URL, params=params, timeout=OEMBED_TIMEOUT_S)
    except httpx.HTTPError as e:
        logger.debug("oEmbed %s: %s", video_id, e)
        return None
    if resp.status_code == 200:
        return True
    if resp.status_code in (401, 403, 404):
        return False
    logger.debug("oEmbed %s: HTTP %s", video_id, resp.status_code)
    return None


@dataclass
class VersionStats:
    """Per-video singer feedback, Firestore ``voice_version_stats/{video_id}`` (written by the app API)."""

    good: int = 0
    not_great: int = 0
    early_exit: int = 0
    plays: int = 0
    embeddable: bool | None = None


def _quality(s: VersionStats | None) -> float:
    if s is None:
        return 0.5
    return (s.good + 1) / (s.good + s.not_great + 0.5 * s.early_exit + 2)


def choose_versions(versions: list[Version], stats: dict[str, VersionStats] | None = None) -> list[Version]:
    """``versions`` best first.

    Versions known to be non-embeddable go last. Then by
    ``0.6 * 1/(1+rank) + 0.4 * quality`` (descending), with
    ``quality = (good+1) / (good+not_great+0.5*early_exit+2)`` from ``stats`` (0.5 without);
    ties: brand priority (NOMAD, then ``COMMUNITY_BRANDS`` order, unknown last), then video id.
    An ``embeddable`` verdict in ``stats`` (reported by the app) overrides the version's own.
    """
    stats = stats or {}

    def key(v: Version) -> tuple[bool, float, int, str]:
        s = stats.get(v.video_id)
        embeddable = s.embeddable if s is not None and s.embeddable is not None else v.embeddable
        score = 0.6 * (1 / (1 + v.rank)) + 0.4 * _quality(s)
        return (embeddable is False, -score, _BRAND_INDEX.get(v.brand_code, len(BRAND_ORDER)), v.video_id)

    return sorted(versions, key=key)
