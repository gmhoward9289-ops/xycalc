"""Import hostram_probe.json into observations + mongodb.host-ram validation cases.

    ./tools/bench/hostram_probe.sh > hostram.json
    python tools/import_hostram_probe.py hostram.json --machine-class COOPER --publish

Only imports rows that passed capHonored. Does not change coefficients.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def build_rows(doc: dict, args):
    guards = doc.get("guards") or {}
    if guards.get("refusedLabels"):
        raise SystemExit(
            f"probe refused labels {guards['refusedLabels']!r} — "
            "do not import; cgroup cap was not honored"
        )
    if not guards.get("monotonicMemSizeMB", True):
        raise SystemExit("monotonicMemSizeMB guard failed — do not import")

    when = args.observed_on or date.today().isoformat()
    machine = args.machine_class or "unrecorded host"
    tag = args.tag or f"cooper-hostram-{when}"
    image = doc.get("image") or "mongo:7"

    source = {
        "slug": f"obs-{tag}",
        "title": f"MongoDB default WiredTiger cache split vs host RAM, {when}",
        "publisher": args.publisher,
        "retrieved_on": when,
        "source_type": "benchmark",
        "notes": (
            f"Produced by tools/bench/hostram_probe.sh on {machine} using {image}. "
            "mongod started with NO wiredTigerCacheSizeGB. Each capped row uses "
            "Docker --memory/--memory-swap; capHonored requires memSizeMB to track "
            f"the requested cgroup within 10%. Guards: {guards}. "
            "Validates mongodb.host-ram by feeding measured cache size and comparing "
            "predicted host RAM to memSizeMB (converted with the unit the probe resolved)."
        ),
    }

    observations = []
    validations = []
    for r in doc.get("runs") or []:
        if r.get("requestedBytes") and not r.get("capHonored"):
            continue
        label = r["label"]
        version = r["version"]
        mem_bytes = int(r["memBytesObserved"])
        cache = int(r["maximumBytesConfigured"])
        obs_slug = f"{tag}-{label}-ram"
        observations.append(
            {
                "slug": obs_slug,
                "system": "mongodb",
                "parameter": "host.ram_bytes",
                "value": mem_bytes,
                "unit": "bytes",
                "workload": (
                    f"idle, default cache split, Docker --memory={r.get('requestedBytes')}"
                    if r.get("requestedBytes")
                    else "idle, default cache split, uncapped Docker control"
                ),
                "machine_class": f"{machine}, {label}",
                "system_version": version,
                "observed_on": when,
                "source": source["slug"],
                "notes": (
                    f"hostInfo.system.memSizeMB={r['memSizeMB']} interpreted as "
                    f"{r['memSizeUnit']} → {mem_bytes} bytes. "
                    f"maximum bytes configured={cache}. "
                    f"formula expected={r['expectedCacheBytes']}, "
                    f"relError={r['relError']}."
                ),
            }
        )
        observations.append(
            {
                "slug": f"{tag}-{label}-cache",
                "system": "mongodb",
                "parameter": "cache.size_bytes",
                "value": cache,
                "unit": "bytes",
                "workload": observations[-1]["workload"],
                "machine_class": f"{machine}, {label}",
                "system_version": version,
                "observed_on": when,
                "source": source["slug"],
                "notes": (
                    "serverStatus().wiredTiger.cache['maximum bytes configured'] "
                    "(default split; no wiredTigerCacheSizeGB override)."
                ),
            }
        )
        validations.append(
            {
                "model": "mongodb.host-ram",
                "case": f"{tag}-{label}",
                "observation": obs_slug,
                "inputs": {"cache_size": cache},
                "at_term": "os_reserve",
                "actual": mem_bytes,
                "notes": (
                    f"Independent default-split check at {label}: measured cache "
                    f"{cache} bytes → model predicts host RAM; actual is memSize "
                    f"from hostInfo ({r['memSizeUnit']}). Probe relError on the "
                    f"forward formula was {r['relError']}."
                ),
            }
        )

    if not validations:
        raise SystemExit("no importable runs")
    return [source], observations, validations


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("analysis_json", type=Path)
    p.add_argument("--machine-class", default="COOPER Docker Desktop")
    p.add_argument("--observed-on")
    p.add_argument("--publisher", default="xycalc measurement (COOPER)")
    p.add_argument("--tag")
    p.add_argument("--publish", action="store_true")
    args = p.parse_args(argv)

    doc = json.loads(args.analysis_json.read_text(encoding="utf-8"))
    sources, observations, validations = build_rows(doc, args)

    root = ROOT / ("data" if args.publish else "local")
    stem = args.tag or f"cooper-hostram-{date.today().isoformat()}"
    for sub, key, rows in (
        ("sources", "sources", sources),
        ("observations", "observations", observations),
        ("validation", "validation", validations),
    ):
        target = root / sub / f"{stem}.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            yaml.safe_dump({key: rows}, sort_keys=False), encoding="utf-8"
        )
        print(f"wrote {target.relative_to(ROOT)}")
    print("\nnow: xycalc build && xycalc audit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
