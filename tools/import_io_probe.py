"""Turn io_crossover_probe JSON into measured nvme-ssd coefficients.

    python tools/bench/io_crossover_smoke.sh 2> smoke.log | tee probe.json
    # or: bash tools/bench/io_crossover_probe.sh PROBE_ARM=local ...

    python tools/import_io_probe.py probe.json --machine-class "reef NVMe"

Writes to local/coefficients/ by default. Pass --publish only when the machine
class and transport are confirmed local (see issue #17 guard checklist).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load_json(path: Path) -> dict:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise SystemExit(f"empty probe file: {path}")
    if "===JSON===" in text:
        text = text.split("===JSON===", 1)[1].strip()
    return json.loads(text)


def _pick_arm(doc: dict, arm: str | None) -> dict:
    if "arms" in doc:
        arms = doc["arms"]
        if arm:
            for row in arms:
                if row.get("arm") == arm:
                    return row
            raise SystemExit(f"no arm {arm!r} in probe output")
        for prefer in ("local", "local-unthrottled", "arm_b"):
            for row in arms:
                if row.get("arm") == prefer:
                    return row
        return arms[-1]
    if "arm" in doc:
        return doc
    raise SystemExit("probe JSON must be an ArmResult or contain an 'arms' list")


def _plateau(arm: dict, kind: str) -> dict | None:
    for row in arm.get("plateaus") or []:
        if row.get("kind") == kind:
            return row
    return None


def build_coefficients(arm: dict, args) -> list[dict]:
    if arm.get("transport") and arm["transport"].lower() in ("iscsi", "fc", "sas"):
        raise SystemExit(
            f"transport {arm['transport']!r} does not look local — "
            "refusing to publish as nvme-ssd"
        )

    iops = _plateau(arm, "iops")
    tp = _plateau(arm, "throughput")
    if not iops and not tp:
        raise SystemExit(
            "no IOPS or throughput plateau in probe output — "
            "refuse to guess coefficients from a failed sweep"
        )

    applies = args.machine_class or arm.get("model") or arm.get("device") or "local probe"
    observed = args.observed_on or date.today().isoformat()
    source = args.source or f"io-probe-{observed}"

    out: list[dict] = []
    if iops:
        val = float(iops["value"])
        out.append(
            {
                "slug": "nvme-ssd.max-random-read-iops",
                "parameter": "io.iops",
                "system": "nvme-ssd",
                "applies_to": f"{applies}, {observed}",
                "value_lo": val * 0.9,
                "value_mode": val,
                "value_hi": val * 1.1,
                "confidence": "measured",
                "source": source,
                "notes": (
                    f"Random read IOPS plateau from fio sweep "
                    f"({iops.get('bs_kib_lo')}–{iops.get('bs_kib_hi')} KiB). "
                    f"Device {arm.get('device')!r}, transport {arm.get('transport')!r}."
                ),
            }
        )

    if tp:
        val_kib = float(tp["value"])
        mibps = val_kib / 1024.0
        out.append(
            {
                "slug": "nvme-ssd.max-throughput-mibps",
                "parameter": "io.throughput_mibps",
                "system": "nvme-ssd",
                "applies_to": f"{applies}, {observed}",
                "value_lo": mibps * 0.9,
                "value_mode": mibps,
                "value_hi": mibps * 1.1,
                "confidence": "measured",
                "source": source,
                "notes": (
                    f"Throughput plateau from fio sweep "
                    f"({tp.get('bs_kib_lo')}–{tp.get('bs_kib_hi')} KiB). "
                    f"Device {arm.get('device')!r}."
                ),
            }
        )

    cross = arm.get("crossover")
    if cross and cross.get("bs_kib"):
        out.append(
            {
                "slug": "nvme-ssd.io-size-crossover-kib",
                "parameter": "io.io_size_crossover_kib",
                "system": "nvme-ssd",
                "applies_to": f"{applies}, {observed}",
                "value": float(cross["bs_kib"]),
                "confidence": "measured",
                "source": source,
                "notes": (
                    f"Crossover method {cross.get('method')!r}. "
                    f"Predicted {cross.get('predicted_kib')!r}."
                ),
            }
        )

    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("probe_json", type=Path)
    p.add_argument("--arm", help="Arm name when JSON contains multiple arms")
    p.add_argument("--machine-class", help="Human label for applies_to")
    p.add_argument("--observed-on", help="ISO date; defaults to today")
    p.add_argument("--source", help="source slug for coefficient rows")
    p.add_argument(
        "--tag",
        help="filename stem under coefficients/; defaults to machine-class + date",
    )
    p.add_argument(
        "--publish",
        action="store_true",
        help="write to data/coefficients/ instead of local/coefficients/",
    )
    args = p.parse_args(argv)

    doc = _load_json(args.probe_json)
    arm = _pick_arm(doc, args.arm)
    coeffs = build_coefficients(arm, args)

    root = ROOT / ("data" if args.publish else "local")
    tag = args.tag or f"nvme-ssd-{date.today().isoformat()}"
    target = root / "coefficients" / f"{tag}.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump({"coefficients": coeffs}, sort_keys=False),
        encoding="utf-8",
    )
    print(f"wrote {len(coeffs)} coefficient row(s) to {target}")
    print("run: python -m xycalc.build")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
