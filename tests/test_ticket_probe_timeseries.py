"""Unit tests for ticket_probe timeseries helpers (issue #12 / T4)."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROBE_PATH = ROOT / "tools" / "bench" / "ticket_probe.py"


def _load_mod():
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

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def __getattr__(self, name):
            return self

        def command(self, *a, **k):
            return {}

    sys.modules["pymongo"].MongoClient = _FakeClient
    spec = importlib.util.spec_from_file_location("ticket_probe_ts", PROBE_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_mod()


def test_timeseries_guards_refuse_without_checkpoints(mod):
    buckets = [
        {"t": i, "ckptRunning": 0, "bytesWrittenFromCacheDelta": 0} for i in range(10)
    ]
    g = mod.timeseries_guards(buckets, checkpoints_observed=1, sampler_errors=0)
    assert g["refuseToConclude"] is True
    assert any("checkpoints_observed" in f for f in g["flags"])


def test_timeseries_guards_ok_when_toggled_and_wrote(mod):
    buckets = []
    for i in range(20):
        running = 1 if 5 <= i <= 7 else 0
        buckets.append(
            {
                "t": i,
                "ckptRunning": running,
                "bytesWrittenFromCacheDelta": 1000 if running else 10,
            }
        )
    g = mod.timeseries_guards(buckets, checkpoints_observed=4, sampler_errors=0)
    assert g["ok"] is True
    assert g["ckptRunningToggled"] is True
    assert g["wroteDuringCheckpoint"] is True


def test_per_second_buckets_join_ckpt(mod):
    t0 = 1_000_000.0
    latencies = [(t0 + 0.2, 10.0), (t0 + 0.8, 20.0), (t0 + 1.1, 50.0)]
    samples = [
        {"t": t0 + 0.5, "ckptRunning": 1, "ckptGeneration": 3, "bytesWrittenFromCache": 100},
        {"t": t0 + 1.5, "ckptRunning": 0, "ckptGeneration": 4, "bytesWrittenFromCache": 150},
    ]
    rows = mod._per_second_buckets(latencies, samples, t0)
    assert rows[0]["ckptRunning"] == 1
    assert rows[0]["ops"] == 2
    assert rows[1]["ops"] == 1
