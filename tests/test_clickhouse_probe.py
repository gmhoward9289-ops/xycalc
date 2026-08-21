"""Unit tests for clickhouse_probe helpers (no Docker required)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

PROBE = Path(__file__).resolve().parents[1] / "tools" / "bench" / "clickhouse_probe.py"


def _load():
    # clickhouse_connect is only needed at runtime against a server; the
    # helpers under test do not call it. Stub the import so CI without the
    # optional client package can still load the module.
    if "clickhouse_connect" not in sys.modules:
        import types

        sys.modules["clickhouse_connect"] = types.ModuleType("clickhouse_connect")
    spec = importlib.util.spec_from_file_location("clickhouse_probe", PROBE)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def probe():
    return _load()


def test_expected_thresholds_are_the_23_6_boundary(probe):
    assert probe.EXPECTED["pre23_6"] == {
        "parts_to_delay_insert": 150,
        "parts_to_throw_insert": 300,
    }
    assert probe.EXPECTED["post23_6"] == {
        "parts_to_delay_insert": 1000,
        "parts_to_throw_insert": 3000,
    }


def test_too_many_parts_detection(probe):
    assert probe._is_too_many_parts(
        Exception("Code: 252. DB::Exception: Too many parts (301). Merges are processing...")
    )
    assert not probe._is_too_many_parts(Exception("Connection reset by peer"))
