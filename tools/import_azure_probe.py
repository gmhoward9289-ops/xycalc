"""Turn an azure_premium_v2_probe run into observations and a validation case.

    PROBE_RG=$RG PROBE_DISK=$DISK PROBE_DEVICE=/dev/sdc \\
      PROBE_TESTFILE=/mnt/psv2/fio.bin \\
      ./tools/bench/azure_premium_v2_probe.sh > probe.json

    python tools/import_azure_probe.py probe.json --machine-class Standard_D8s_v5

Writes to local/ by default — gitignored, merged over data/ at build time. Pass
--publish only for a disk/VM whose details are fine to put on the internet.

What it records, and why the split matters:

  * A validation case for azure.premium-v2-throughput-ceiling, whose `actual`
    is the CONTROL-PLANE ceiling Azure enforced (diskMBpsReadWrite for the
    disk's provisioned IOPS). That is the exact quantity the model predicts, so
    the case tests the model against the live control plane rather than against
    the documentation it was built from.

  * Observations of DELIVERED throughput (io.throughput_mbps) and DELIVERED
    IOPS (io.iops) from fio. These answer a different question — does the disk
    sustain what it was provisioned for — so they are recorded as observations,
    NOT wired into the ceiling validation. Comparing "what I got" against a
    model of "what I was allowed to set" would score a correct model wrong for
    a reason that has nothing to do with its accuracy.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _run(runs: list[dict], kind: str) -> dict | None:
    return next((r for r in runs if r.get("kind") == kind), None)


def build_rows(doc: dict, args) -> tuple[list[dict], list[dict], list[dict]]:
    runs = doc.get("runs") or []
    iops_set = doc.get("provisioned_iops")
    ceiling = doc.get("settable_mbps")
    when = args.observed_on or date.today().isoformat()
    machine = args.machine_class or "unrecorded Azure VM"
    tag = args.tag or f"azure-psv2-{args.machine_class or 'vm'}-{when}".replace(" ", "-").lower()

    warns = (doc.get("guards") or {}).get("delivery_vs_ceiling_warnings") or []
    for w in warns:
        print(f"warning: {w}", file=sys.stderr)

    source = {
        "slug": f"obs-{tag}",
        "title": f"Azure Premium SSD v2 delivery probe, {when}",
        "publisher": args.publisher,
        "retrieved_on": when,
        "source_type": "benchmark",
        "notes": (
            "Produced by tools/bench/azure_premium_v2_probe.sh (fio --direct=1) "
            f"on {machine}. Provisioned {iops_set} IOPS, settable ceiling "
            f"{ceiling} MB/s (from az disk show). Device "
            f"{doc.get('device')!r}, transport {doc.get('transport')!r}, "
            f"model {doc.get('model')!r}. A benchmark's citation is its harness; "
            "this names it so the run can be reproduced."
        ),
    }

    common = {
        "system": "azure-disks",
        "workload": args.workload or "fio synthetic (sequential write for MB/s, random read for IOPS)",
        "machine_class": machine,
        "system_version": "Premium SSD v2",
        "observed_on": when,
        "source": source["slug"],
    }

    observations: list[dict] = []
    tp = _run(runs, "throughput")
    if tp and tp.get("ok"):
        observations.append(
            {
                "slug": f"{tag}-delivered-throughput",
                "parameter": "io.throughput_mbps",
                "value": round(float(tp["bw_mbps"]), 2),
                "unit": "MB/s",
                "notes": (
                    f"Delivered sequential throughput, fio {tp['rw']} "
                    f"bs={tp['bs_kib']} KiB iodepth={tp['iodepth']} --direct=1. "
                    "Data-plane delivery, distinct from the settable ceiling the "
                    "ceiling model predicts."
                ),
                **common,
            }
        )
    io = _run(runs, "iops")
    if io and io.get("ok"):
        observations.append(
            {
                "slug": f"{tag}-delivered-iops",
                "parameter": "io.iops",
                "value": round(float(io["iops"]), 0),
                "unit": "iops",
                "notes": (
                    f"Delivered random-read IOPS, fio {io['rw']} "
                    f"bs={io['bs_kib']} KiB iodepth={io['iodepth']} --direct=1."
                ),
                **common,
            }
        )

    validations: list[dict] = []
    if args.validate:
        if not iops_set or not ceiling:
            print(
                "note: no provisioned IOPS / settable ceiling in the probe — "
                "skipping the ceiling validation case (nothing to compare the "
                "model's prediction against). Pass PROBE_SETTABLE_MBPS when "
                "running the probe.",
                file=sys.stderr,
            )
        elif warns:
            print(
                "refusing to write a validation case: the delivery-vs-ceiling "
                "guard fired (likely the wrong device was measured). Fix the "
                "device and rerun rather than recording a suspect case.",
                file=sys.stderr,
            )
        else:
            validations.append(
                {
                    "model": "azure.premium-v2-throughput-ceiling",
                    "case": f"{tag}-ceiling",
                    "inputs": {"provisioned_iops": int(iops_set)},
                    "actual": float(ceiling),
                    "notes": (
                        "Model prediction (0.25 MB/s per provisioned IOPS, floored "
                        "at 125, capped at 2,000) against the throughput Azure "
                        "actually let this disk be set to, read from az disk show "
                        "diskMBpsReadWrite. Validates the ceiling model against the "
                        "live control plane. NOT a delivery test — see the "
                        "io.throughput_mbps observation for what the disk sustained."
                    ),
                }
            )

    return [source], observations, validations


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("probe_json", type=Path)
    p.add_argument("--machine-class", help="Azure VM size, e.g. Standard_D8s_v5")
    p.add_argument("--workload", help="what the probe drove; defaults to the fio shape")
    p.add_argument("--observed-on", help="ISO date; defaults to today")
    p.add_argument("--publisher", default="local measurement")
    p.add_argument("--tag", help="slug prefix; defaults to machine-class + date")
    p.add_argument(
        "--no-validate",
        dest="validate",
        action="store_false",
        help="import observations without the ceiling validation case",
    )
    p.add_argument(
        "--publish",
        action="store_true",
        help="write to data/ instead of local/. Only for a disk/VM you are happy "
        "to describe on the internet.",
    )
    args = p.parse_args(argv)

    text = args.probe_json.read_text(encoding="utf-8")
    if "===JSON===" in text:
        text = text.split("===JSON===", 1)[1]
    doc = json.loads(text.strip())

    sources, observations, validations = build_rows(doc, args)
    if not observations and not validations:
        raise SystemExit("nothing to import — no valid fio runs and no ceiling case")

    root = ROOT / ("data" if args.publish else "local")
    stem = args.tag or f"azure-psv2-{date.today().isoformat()}"
    written = []
    for sub, key, rows in (
        ("sources", "sources", sources),
        ("observations", "observations", observations),
        ("validation", "validation", validations),
    ):
        if not rows:
            continue
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
