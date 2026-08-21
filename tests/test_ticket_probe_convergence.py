"""Unit tests for ticket_probe convergence verdict math (issue #3 §4b).

Import the helper without connecting to MongoDB by loading the module from
source with pymongo stubbed — the verdict function is pure.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROBE_PATH = ROOT / "tools" / "bench" / "ticket_probe.py"


def _load_convergence():
    # Stub pymongo so importing the probe module does not require the package
    # or a live MongoDB (CI runners may lack both).
    if "pymongo" not in sys.modules:
        pymongo = types.ModuleType("pymongo")
        errors = types.ModuleType("pymongo.errors")

        class _Dummy(Exception):
            pass

        errors.AutoReconnect = _Dummy
        errors.NetworkTimeout = _Dummy
        errors.ConnectionFailure = _Dummy
        errors.ServerSelectionTimeoutError = _Dummy
        pymongo.errors = errors
        pymongo.MongoClient = object
        sys.modules["pymongo"] = pymongo
        sys.modules["pymongo.errors"] = errors

    spec = importlib.util.spec_from_file_location("ticket_probe_under_test", PROBE_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Avoid executing module-level MongoClient(URI) — patch after load fails.
    # Instead exec only up through the helper by temporarily replacing
    # MongoClient with a no-op that records nothing.
    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def __getattr__(self, name):
            return self

        def command(self, *a, **k):
            return {}

    sys.modules["pymongo"].MongoClient = _FakeClient
    spec.loader.exec_module(mod)
    return mod.convergence_verdict


@pytest.fixture(scope="module")
def verdict():
    return _load_convergence()


def _flat(n: int, value: int, dt: float = 1.0) -> list[dict]:
    return [{"t": i * dt, "readTotal": value, "readOut": 0, "queueLength": 0} for i in range(n)]


def test_converged_flat(verdict):
    # 200s of flat tickets → last 90 vs prior 90 agree.
    series = _flat(200, 64)
    r = verdict(series, window_s=90, tol=0.05, offered=150)
    assert r["verdict"] == "CONVERGED"
    assert r["demandCapped"] is False
    assert r["relDelta"] < 0.05


def test_still_moving_rising(verdict):
    series = [
        {"t": float(i), "readTotal": 4 + i // 2, "readOut": 0, "queueLength": 0}
        for i in range(200)
    ]
    r = verdict(series, window_s=90, tol=0.05)
    assert r["verdict"] == "STILL_MOVING"
    assert r["relDelta"] >= 0.05


def test_demand_capped(verdict):
    series = _flat(200, 64)
    r = verdict(series, window_s=90, tol=0.05, offered=64)
    assert r["verdict"] == "CONVERGED_DEMAND_CAPPED"
    assert r["demandCapped"] is True


def test_insufficient_samples(verdict):
    r = verdict(_flat(3, 10), window_s=90)
    assert r["verdict"] == "INSUFFICIENT_SAMPLES"
