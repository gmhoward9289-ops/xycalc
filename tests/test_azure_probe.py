"""The Azure Premium SSD v2 probe's guards.

The probe records two quantities that must not be confused — the control-plane
ceiling (what the model predicts) and delivered throughput (what fio measured).
These lock the guard that keeps them honest: delivery cannot exceed the settable
ceiling on a managed disk, so a reading above it means the wrong device was
measured, and the importer must then refuse to build a validation case.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS / "bench"))

import azure_premium_v2_probe as probe  # noqa: E402
import import_azure_probe as iap  # noqa: E402


def _fio_run(kind, bw_mbps, iops):
    return probe.FioRun(
        kind=kind, rw="write" if kind == "throughput" else "randread",
        bs_kib=256.0 if kind == "throughput" else 4.0, iodepth=32,
        iops=iops, bw_kib_s=bw_mbps * 1_000_000 / 1024, bw_mbps=bw_mbps,
        lat_us=1000.0, iodepth_mean=31.5, direct=True, ok=True,
    )


def test_delivery_within_the_ceiling_raises_no_flag():
    runs = [_fio_run("throughput", 1950.0, 7600), _fio_run("iops", 123.0, 30000)]
    from dataclasses import asdict
    warns = probe.check_delivery_against_ceiling([r for r in runs], settable_mbps=2000.0)
    assert warns == []


def test_delivery_above_the_ceiling_flags_wrong_device():
    """A managed disk cannot deliver more than it was allowed to be set to.
    3,000 MB/s against a 2,000 ceiling is the signature of fio hitting the local
    NVMe temp disk instead of the Premium SSD v2."""
    runs = [_fio_run("throughput", 3000.0, 12000)]
    warns = probe.check_delivery_against_ceiling(runs, settable_mbps=2000.0)
    assert warns and "wrong device" in warns[0].lower()


def test_missing_ceiling_is_itself_flagged():
    warns = probe.check_delivery_against_ceiling([_fio_run("throughput", 500.0, 2000)], settable_mbps=None)
    assert warns and "settable" in warns[0].lower()


def _args(**kw):
    base = dict(machine_class="Standard_D8s_v5", workload=None, observed_on="2026-08-21",
                publisher="test", tag=None, validate=True)
    base.update(kw)
    return SimpleNamespace(**base)


def _doc(settable=2000.0, delivered=1950.0, warns=None):
    return {
        "device": "/dev/sdc", "transport": "scsi", "model": "Virtual Disk",
        "provisioned_iops": 8000, "settable_mbps": settable, "disk_size_gib": 256.0,
        "runs": [
            {"kind": "throughput", "rw": "write", "bs_kib": 256.0, "iodepth": 32,
             "iops": 7600.0, "bw_kib_s": delivered * 1_000_000 / 1024, "bw_mbps": delivered,
             "lat_us": 4100.0, "iodepth_mean": 31.6, "direct": True, "ok": True, "reject_reason": None},
            {"kind": "iops", "rw": "randread", "bs_kib": 4.0, "iodepth": 32,
             "iops": 30000.0, "bw_kib_s": 120000.0, "bw_mbps": 122.9, "lat_us": 1050.0,
             "iodepth_mean": 31.9, "direct": True, "ok": True, "reject_reason": None},
        ],
        "guards": {"delivery_vs_ceiling_warnings": warns or [], "rejected_runs": []},
    }


def test_importer_builds_a_ceiling_validation_case_when_clean():
    sources, observations, validations = iap.build_rows(_doc(), _args())
    assert sources[0]["source_type"] == "benchmark"
    assert "tools/bench/azure_premium_v2_probe.sh" in sources[0]["notes"]
    # delivered throughput + IOPS as observations, kept in azure-disks
    params = {o["parameter"] for o in observations}
    assert params == {"io.throughput_mbps", "io.iops"}
    assert all(o["system"] == "azure-disks" for o in observations)
    # exactly one ceiling validation case, actual = the enforced ceiling
    assert len(validations) == 1
    v = validations[0]
    assert v["model"] == "azure.premium-v2-throughput-ceiling"
    assert v["inputs"] == {"provisioned_iops": 8000}
    assert v["actual"] == 2000.0


def test_importer_refuses_the_case_when_the_wrong_device_guard_fired():
    """Observations may still be recorded, but a suspect run must not produce a
    validation case."""
    doc = _doc(delivered=3000.0, warns=["delivered 3000 MB/s exceeds the 2000 MB/s settable ceiling — wrong device?"])
    sources, observations, validations = iap.build_rows(doc, _args())
    assert validations == []
    assert observations  # observations still captured


def test_importer_skips_the_case_without_a_settable_ceiling():
    doc = _doc(settable=None)
    doc["provisioned_iops"] = None
    sources, observations, validations = iap.build_rows(doc, _args())
    assert validations == []
