"""The burst probe's guards, and the parsing they rest on.

A harness that produces a clean-looking ratio table from a broken run is the
exact failure issue #8 is about. These lock the two things that keep this one
honest: the 1-second-log parser, and the control guard that refuses to import
when the constant-rate run did not come back at ~1.0.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS / "bench"))

import burst_probe_analyze as bpa  # noqa: E402
import import_burst_probe as ibp  # noqa: E402


def _write_log(tmp_path: Path, name: str, rows: list[tuple[int, float]]) -> Path:
    p = tmp_path / f"{name}_iops.1.log"
    p.write_text("".join(f"{t}, {v}, 0, 4096, 0\n" for t, v in rows), encoding="utf-8")
    return p


def test_parser_sums_read_and_write_at_the_same_timestamp(tmp_path):
    """Mixed workloads log read and write as separate rows at one timestamp;
    the total IOPS is their sum, not either one alone."""
    p = tmp_path / "mix_iops.1.log"
    p.write_text("1000, 100, 0, 4096, 0\n1000, 40, 1, 4096, 0\n", encoding="utf-8")
    samples = bpa.parse_fio_iops_log(p)
    assert samples == [(1000, 140.0)]


def test_window_ratios_bucket_into_minutes_and_take_peak_over_mean():
    # 120 one-second samples: window 0 flat at 100 (ratio 1.0), window 1 has a
    # single 900 spike among 100s (peak/mean well above 1).
    samples = [((i + 1) * 1000, 100.0) for i in range(60)]
    samples += [((60 + i + 1) * 1000, (900.0 if i == 0 else 100.0)) for i in range(60)]
    ratios, windows = bpa.window_ratios(samples)
    assert len(ratios) == 2
    assert ratios[0] == pytest.approx(1.0)
    assert ratios[1] > 3.0


def test_constant_rate_control_reads_as_ratio_near_one(tmp_path):
    log = _write_log(tmp_path, "control", [((i + 1) * 1000, 200.0) for i in range(120)])
    stats = bpa.analyse_run("control", log)
    assert stats.ratio_median == pytest.approx(1.0, abs=0.05)


def _args(**kw):
    base = dict(machine_class="m6i.large", observed_on="2026-08-21", publisher="test", tag=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _doc(control_median=1.01, batch_median=1.05, bursty_median=8.8):
    def run(name, med):
        return {
            "name": name, "windows": 3, "samples": 180, "mean_iops": 500.0,
            "peak_iops": med * 500.0, "ratio_min": med - 0.01, "ratio_median": med,
            "ratio_max": med + 0.01, "per_window_ratios": [med], "trending_up": False,
        }
    return {
        "machine": "test-host",
        "runs": [run("control", control_median), run("batch", batch_median), run("bursty", bursty_median)],
        "guards": {"control": f"ok (median {control_median})"},
    }


def test_importer_records_ratio_observations_without_touching_the_coefficient():
    sources, observations = ibp.build_rows(_doc(), _args())
    assert len(sources) == 1 and sources[0]["source_type"] == "benchmark"
    assert "tools/bench/burst_probe.sh" in sources[0]["notes"]
    slugs = {o["parameter"] for o in observations}
    assert slugs == {"io.peak_to_mean_ratio"}
    assert all(o["system"] == "ebs" for o in observations)
    assert {o["value"] for o in observations} == {1.05, 8.8}


def test_importer_refuses_when_the_control_guard_failed():
    """The gate nobody has watched fire. A broken control means broken parsing,
    and no Shape A/B number should reach the corpus."""
    doc = _doc()
    doc["guards"]["control"] = "FAILED — constant-rate control median ratio 4.2 is not ~1.0"
    with pytest.raises(SystemExit, match="control guard"):
        ibp.build_rows(doc, _args())


def test_importer_emits_no_coefficient_or_validation_rows():
    """The whole point of #4's restraint: observations only, coefficient left as
    the estimate it is. build_rows returns exactly (sources, observations)."""
    result = ibp.build_rows(_doc(), _args())
    assert len(result) == 2  # sources, observations — no coefficients, no validation
