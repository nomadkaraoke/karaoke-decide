"""Client for flacfetch (v0.35+) on flacup: batch Spotify downloads under its listening budget.

``start_batch`` asks for a ``priority="batch"`` Spotify download that flacfetch uploads to
``gs://<flacfetch's GCS_BUCKET>/<gcs_dir>/<filename>.flac`` and deletes locally. flacfetch may
refuse it (429 ``{"detail": {"code", "retry_after_s"}}``): that comes back as ``Refused``.
Outages (503 ``budget_unavailable``, other 5xx, connection errors, timeouts) raise
``FlacfetchUnavailableError``: the caller stops for this run. A 400 / 422 for one download raises
``FlacfetchRejectedError`` (that song's problem). Any other HTTP error (401 / 403: bad key) raises
``httpx.HTTPStatusError``: that is misconfiguration, a real failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

TIMEOUT_S = 30.0


@dataclass(frozen=True)
class Refused:
    """flacfetch declined a batch download for now (``spotify_budget``, ``batch_busy``,
    ``interactive_active`` or ``batch_gap``)."""

    code: str
    retry_after_s: int


class FlacfetchRejectedError(Exception):
    """flacfetch rejected this one download request (400 / 422): a problem with the song, not
    with flacfetch or our credentials."""


class FlacfetchUnavailableError(Exception):
    """flacfetch (or its Spotify ledger) can't take requests right now."""

    def __init__(self, message: str, code: str = "unavailable") -> None:
        super().__init__(message)
        self.code = code


def _detail(resp: httpx.Response) -> dict[str, Any]:
    try:
        detail = resp.json().get("detail")
    except (ValueError, AttributeError):
        return {}
    return detail if isinstance(detail, dict) else {}


class FlacfetchClient:
    def __init__(self, base_url: str, api_key: str, http: httpx.Client) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.http = http

    def _request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        try:
            resp = self.http.request(
                method, f"{self.base_url}{path}", headers={"X-API-Key": self.api_key}, timeout=TIMEOUT_S, **kw
            )
        except httpx.TransportError as e:  # connect / read errors and timeouts
            raise FlacfetchUnavailableError(f"{method} {path}: {e!r}") from e
        if resp.status_code == 503:
            code = _detail(resp).get("code", "unavailable")
            raise FlacfetchUnavailableError(f"{method} {path}: HTTP 503 {code}", code=code)
        if resp.status_code >= 500:
            raise FlacfetchUnavailableError(f"{method} {path}: HTTP {resp.status_code}")
        return resp

    def budget(self) -> dict[str, Any]:
        """``GET /spotify/budget``: ``{window_s, total_s, batch_s, batch_budget_s, hard_cap_s,
        batch_in_flight, interactive_in_flight, batch_allowed_now, retry_after_s}``."""
        resp = self._request("GET", "/spotify/budget")
        resp.raise_for_status()
        body: dict[str, Any] = resp.json()
        return body

    def start_batch(self, spotify_id: str, duration_s: int, gcs_dir: str, filename: str) -> str | Refused:
        """Start a batch Spotify download; returns its download id, or ``Refused``."""
        body = {
            "source_name": "Spotify",
            "source_id": spotify_id,
            "download_url": f"https://open.spotify.com/track/{spotify_id}",
            "upload_to_gcs": True,
            "gcs_path": gcs_dir,
            "output_filename": filename,
            "priority": "batch",
            "expected_duration_s": duration_s,
            "delete_local_after_upload": True,
        }
        resp = self._request("POST", "/download-by-id", json=body)
        if resp.status_code == 429:
            detail = _detail(resp)
            retry = detail.get("retry_after_s", resp.headers.get("Retry-After", 60))
            return Refused(str(detail.get("code", "refused")), int(retry))
        if resp.status_code in (400, 422):
            raise FlacfetchRejectedError(f"flacfetch rejected the download: HTTP {resp.status_code} {resp.text[:300]}")
        resp.raise_for_status()
        return str(resp.json()["download_id"])

    def status(self, download_id: str) -> dict[str, Any] | None:
        """``GET /download/{id}/status`` (``status``: queued | downloading | uploading | seeding |
        complete | failed | cancelled; ``gcs_path`` = ``gs://…`` once uploaded; ``error``).
        None when flacfetch doesn't know the id (its task list is in memory: lost on restart)."""
        resp = self._request("GET", f"/download/{download_id}/status")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        body: dict[str, Any] = resp.json()
        return body
