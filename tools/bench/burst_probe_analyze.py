#!/usr/bin/env python3
"""Analyse fio IOPS logs into per-minute peak-to-mean ratios — issue #4.

`ebs.peak-to-mean-iops-ratio` is the widest, least-defensible band in the corpus
(1.5–3.0–10.0, graded `estimate`): the gap between the peak SECOND EBS throttles
on and the mean MINUTE CloudWatch publishes. This turns a fio run's 1-second
IOPS log into that ratio the way the metric would see it — bucket the 1-second
samples into non-overlapping 60-second windows, and per window take
peak = max(sample), mean = mean(samples), ratio = peak / mean.

It reports the DISTRIBUTION of per-window ratios (min / median / max), not one
scalar, for two reasons the plan is explicit about: a single number invites the
false precision this corpus avoids, and the spread across windows is how you see
whether the quantity is converging (flat) or still growing (trending up) on a
15-minute timescale.

The control run is the guard. A `--rate_iops` constant-rate job must come back at
ratio ≈ 1.0; if it does not, the log parsing or window alignment is broken and no
Shape A/B number should be trusted regardless of how plausible it looks.

See docs/plans/issue-4-ebs-burst-factor-iostat.md.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

WINDOW_MS = 60_000  # the CloudWatch-minute analogue
CONTROL_TOLERANCE = 0.20  # a constant-rate control must land within 20% of 1.0


@dataclass
class RunStats:
    name: str
    windows: int
    samples: int
    mean_iops: float
    peak_iops: float
    ratio_min: float
    ratio_median: float
    ratio_max: float
    per_window_ratios: list[float]
    trending_up: bool  # last window's ratio materially above the first — not converged


def parse_fio_iops_log(path: Path) -> list[tuple[int, float]]:
    """fio --write_iops_log rows: 'time_ms, value, ddir, bs[, offset[, prio]]'.

    Read and write directions are logged as separate rows at the same timestamp;
    sum them so a mixed workload's total IOPS is bucketed, not one direction.
    """
    totals: dict[int, float] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            t_ms = int(float(parts[0]))
            value = float(parts[1])
        except ValueError:
            continue
        totals[t_ms] = totals.get(t_ms, 0.0) + value
    return sorted(totals.items())


def window_ratios(samples: list[tuple[int, float]]) -> tuple[list[float], list[list[float]]]:
    if not samples:
        return [], []
    t0 = samples[0][0]
    buckets: dict[int, list[float]] = {}
    for t_ms, value in samples:
        w = (t_ms - t0) // WINDOW_MS
        buckets.setdefault(w, []).append(value)
    ratios: list[float] = []
    ordered: list[list[float]] = []
    for w in sorted(buckets):
        vals = buckets[w]
        mean = statistics.mean(vals)
        if mean <= 0:
            continue
        ratios.append(max(vals) / mean)
        ordered.append(vals)
    return ratios, ordered


def analyse_run(name: str, path: Path) -> RunStats:
    samples = parse_fio_iops_log(path)
    ratios, windows = window_ratios(samples)
    if not ratios:
        raise SystemExit(f"{name}: no usable windows parsed from {path}")
    all_vals = [v for w in windows for v in w]
    trending = len(ratios) >= 3 and ratios[-1] > ratios[0] * 1.25
    return RunStats(
        name=name,
        windows=len(ratios),
        samples=len(samples),
        mean_iops=round(statistics.mean(all_vals), 1),
        peak_iops=round(max(all_vals), 1),
        ratio_min=round(min(ratios), 3),
        ratio_median=round(statistics.median(ratios), 3),
        ratio_max=round(max(ratios), 3),
        per_window_ratios=[round(r, 3) for r in ratios],
        trending_up=trending,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "logs",
        nargs="+",
        help="name=path pairs, e.g. control=control_iops.1.log batch=batch_iops.1.log",
    )
    ap.add_argument("--machine", default=None, help="host/instance label carried into the JSON")
    args = ap.parse_args()

    runs: list[RunStats] = []
    for spec in args.logs:
        if "=" not in spec:
            raise SystemExit(f"expected name=path, got {spec!r}")
        name, _, path = spec.partition("=")
        runs.append(analyse_run(name, Path(path)))

    control = next((r for r in runs if r.name == "control"), None)
    guards: dict = {}
    if control is None:
        guards["control"] = "MISSING — no control run; ratios below are unguarded"
    elif abs(control.ratio_median - 1.0) > CONTROL_TOLERANCE:
        guards["control"] = (
            f"FAILED — constant-rate control median ratio {control.ratio_median} "
            f"is not ~1.0; parsing or window alignment is broken, do not trust "
            f"Shape A/B ratios"
        )
    else:
        guards["control"] = f"ok (median {control.ratio_median})"

    trending = [r.name for r in runs if r.trending_up and r.name != "control"]
    if trending:
        guards["not_converged"] = (
            f"{', '.join(trending)}: per-window ratio still climbing across the "
            f"run — 15 minutes may not settle this quantity (see plan §2)"
        )

    out = {"machine": args.machine, "runs": [asdict(r) for r in runs], "guards": guards}
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
