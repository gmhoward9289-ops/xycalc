"""Turn a burst_probe analysis into peak-to-mean IOPS observations — issue #4.

    sudo ./tools/bench/burst_probe.sh > burst.json
    python tools/import_burst_probe.py burst.json --machine-class m6i.large

Writes to local/ by default — gitignored, merged over data/ at build time. Pass
--publish only for a host whose details are fine to put on the internet.

What it records, and — as much to the point — what it does NOT:

  * It records one OBSERVATION of io.peak_to_mean_ratio per workload shape
    (batch, bursty), each the median of that run's per-minute peak/mean ratios,
    with the full min/median/max spread in the notes.

  * It does NOT touch ebs.peak-to-mean-iops-ratio. That coefficient is graded
    `estimate` with a deliberately wide band, and issue #4 is explicit: do not
    narrow it on one machine's measurements. Four runs on one host is not the
    population. The observations are evidence to weigh later, by hand, across
    machines — not a value to overwrite the coefficient with now.

  * It writes no validation case. The ratio is an amplifier inside
    ebs.iops-to-provision, not that model's output, so there is no like-for-like
    quantity to validate against here — recording the ratio as an observation is
    the honest shape.

The control run guards everything: if the constant-rate control did not come
back at ratio ~1.0, the analysis pipeline is broken and this refuses to import.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def build_rows(doc: dict, args) -> tuple[list[dict], list[dict]]:
    runs = {r["name"]: r for r in doc.get("runs", [])}
    guards = doc.get("guards", {})

    control_note = str(guards.get("control", ""))
    if control_note.startswith(("FAILED", "MISSING")):
        raise SystemExit(
            f"control guard did not pass ({control_note!r}). The constant-rate "
            "control must land at ratio ~1.0 before any Shape A/B number is "
            "trustworthy — fix the harness/parse and rerun rather than importing "
            "a suspect ratio."
        )

    when = args.observed_on or date.today().isoformat()
    machine = args.machine_class or doc.get("machine") or "unrecorded host"
    tag = args.tag or f"ebs-burst-{args.machine_class or doc.get('machine') or 'host'}-{when}".replace(" ", "-").lower()

    source = {
        "slug": f"obs-{tag}",
        "title": f"EBS burst-factor probe (peak-second vs mean-minute IOPS), {when}",
        "publisher": args.publisher,
        "retrieved_on": when,
        "source_type": "benchmark",
        "notes": (
            "Produced by tools/bench/burst_probe.sh (fio --direct=1 on an "
            f"isolated loop device) on {machine}. Control guard: "
            f"{control_note}. A benchmark's citation is its harness; this names "
            "it so the run can be reproduced. Measures the guest-driven "
            "peak/mean ratio — the same layer AWS's 'driven IOPS' language "
            "describes — not EBS's virtualized block layer itself."
        ),
    }

    observations: list[dict] = []
    for shape in ("batch", "bursty"):
        r = runs.get(shape)
        if not r:
            continue
        observations.append(
            {
                "slug": f"{tag}-{shape}",
                "system": "ebs",
                "parameter": "io.peak_to_mean_ratio",
                "value": float(r["ratio_median"]),
                "unit": "ratio",
                "workload": (
                    "fio steady sequential write, iodepth 8, uncapped"
                    if shape == "batch"
                    else "fio Poisson-arrival random read, rate_iops=400, iodepth 8"
                ),
                "machine_class": machine,
                "system_version": "loop device, fio --direct=1",
                "observed_on": when,
                "source": source["slug"],
                "notes": (
                    f"Median of {r['windows']} per-minute peak/mean ratios; "
                    f"spread {r['ratio_min']}–{r['ratio_max']} (min–max), "
                    f"mean {r['mean_iops']} IOPS, peak {r['peak_iops']} IOPS. "
                    + ("Per-window ratio still climbing — may not have converged "
                       "in this window (see plan §2). " if r.get("trending_up") else "")
                    + "Recorded as evidence for ebs.peak-to-mean-iops-ratio; the "
                    "coefficient is NOT narrowed on one host's runs."
                ),
            }
        )

    if not observations:
        raise SystemExit("no batch/bursty runs in the analysis — nothing to import")

    return [source], observations


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("analysis_json", type=Path)
    p.add_argument("--machine-class", help="instance/host label, e.g. m6i.large")
    p.add_argument("--observed-on", help="ISO date; defaults to today")
    p.add_argument("--publisher", default="local measurement")
    p.add_argument("--tag", help="slug prefix; defaults to machine-class + date")
    p.add_argument(
        "--publish",
        action="store_true",
        help="write to data/ instead of local/. Only for a host you are happy "
        "to describe on the internet.",
    )
    args = p.parse_args(argv)

    doc = json.loads(args.analysis_json.read_text(encoding="utf-8"))
    sources, observations = build_rows(doc, args)

    root = ROOT / ("data" if args.publish else "local")
    stem = args.tag or f"ebs-burst-{date.today().isoformat()}"
    written = []
    for sub, key, rows in (
        ("sources", "sources", sources),
        ("observations", "observations", observations),
    ):
        target = root / sub / f"{stem}.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(yaml.safe_dump({key: rows}, sort_keys=False), encoding="utf-8")
        written.append(target)

    for path in written:
        print(f"wrote {path.relative_to(ROOT)}")
    print("\nnow: xycalc build && xycalc audit")
    if not args.publish:
        print("(local/ is gitignored — these rows are yours, not the corpus's)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
