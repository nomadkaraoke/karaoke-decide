"""Runs the Cloudflare Worker's routing tests (node:test) under pytest."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

WORKER_DIR = Path(__file__).resolve().parents[2] / "infrastructure" / "cloudflare-worker"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_api_proxy_worker_routing() -> None:
    result = subprocess.run(
        ["node", "--test", str(WORKER_DIR / "api-proxy.test.mjs")],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
