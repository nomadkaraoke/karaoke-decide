"""lazy_module / preload: deferred client-library imports for the API's cold start."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from unittest.mock import patch

import pytest

from karaoke_decide.core.lazy_import import LazyModule, lazy_module, preload


def test_import_is_deferred_until_first_attribute() -> None:
    code = (
        "import json, sys\n"
        "from karaoke_decide.core.lazy_import import lazy_module\n"
        "m = lazy_module('colorsys')\n"
        "before = 'colorsys' in sys.modules\n"
        "value = m.rgb_to_hsv(1.0, 0.0, 0.0)\n"
        "print(json.dumps([before, 'colorsys' in sys.modules, list(value)]))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == [False, True, [0.0, 1.0, 1.0]]


def test_attributes_delegate_to_the_real_module() -> None:
    import json as real

    m = lazy_module("json")
    assert isinstance(m, LazyModule)
    assert m.dumps is real.dumps
    assert repr(m) == "<lazy module 'json'>"


def test_unknown_attribute_raises_attribute_error() -> None:
    with pytest.raises(AttributeError):
        _ = lazy_module("json").no_such_thing


def test_mock_patch_through_the_stand_in_sets_and_restores() -> None:
    m = lazy_module("json")
    import json as real

    with patch.object(m, "dumps", return_value="patched"):
        assert m.dumps({}) == "patched"
        assert real.dumps({}) == "{}"  # the real module is untouched
    assert m.dumps is real.dumps


def test_preload_imports_and_survives_a_broken_module(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="karaoke_decide.core.lazy_import")
    thread = preload(["colorsys", "no_such_module_for_preload"])
    thread.join(10)
    assert not thread.is_alive()
    assert "colorsys" in sys.modules
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "no_such_module_for_preload" in warnings[0].getMessage()
    assert any("Preloaded client libraries" in r.getMessage() for r in caplog.records)
