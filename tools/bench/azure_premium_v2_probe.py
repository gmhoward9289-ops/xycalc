#!/usr/bin/env python3
"""Azure Premium SSD v2 delivery probe — validates azure.premium-v2-throughput-ceiling.

Runs against a Premium SSD v2 data disk attached to an Azure VM. It records two
different quantities, and keeps them apart because they answer different
questions:

  1. The CONTROL-PLANE ceiling. What throughput Azure actually let you set for
     the disk's provisioned IOPS (read back from `az disk show`, passed in via
     --settable-mbps). This is what azure.premium-v2-throughput-ceiling
     predicts — 0.25 MB/s per provisioned IOPS, floored at 125, capped at 2,000
     — so it is the quantity a validation case for that model must use.

  2. The DATA-PLANE delivery. What the disk actually sustained under fio
     (--direct=1): large-block sequential throughput in MB/s and small-block
     random-read IOPS. This is NOT what the ceiling model predicts — a disk can
     be configured for 750 MB/s and deliver less under a real workload — so it
     is recorded as an observation, not folded into the ceiling validation.

The split is the whole point. Conflating "what I was allowed to set" with "what
I got" is the same class of mistake the corpus keeps dataSize and storageSize
apart to avoid.

Guards (see tools/bench/README.md — "Before you believe a result"):
  - --direct=1 on every job; a run whose O_DIRECT did not engage is rejected,
    because the host page cache would otherwise report RAM speed, not disk.
  - queue depth must not collapse, or the number is a latency reading not a
    throughput one.
  - the measured throughput is checked against the settable ceiling: delivery
    materially ABOVE the ceiling means the wrong device was measured (a local
    NVMe temp disk, not the Premium SSD v2), and the run is flagged.

Azure quotes disk throughput in decimal MB/s (10^6 B/s); fio reports KiB/s.
This script converts with 1000, not 1024, so the recorded number is in the same
unit the model and the vendor use.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

MB = 1_000_000  # decimal, per Azure's own "MB/s = 10^6 Bytes per second"


@dataclass
class FioRun:
    kind: str  # "throughput" | "iops"
    rw: str
    bs_kib: float
    iodepth: int
    iops: float
    bw_kib_s: float
    bw_mbps: float  # decimal MB/s
    lat_us: float
    iodepth_mean: float
    direct: bool
    ok: bool
    reject_reason: str | None = None


@dataclass
class ProbeResult:
    device: str
    transport: str | None
    model: str | None
    # Control-plane config, read from `az disk show` and passed in:
    provisioned_iops: int | None
    settable_mbps: float | None  # the ceiling az accepted for this IOPS
    disk_size_gib: float | None
    runs: list[FioRun]
    guards: dict[str, Any]


def run_fio(
    test_file: str, rw: str, bs_kib: float, runtime: float, iodepth: int
) -> dict[str, Any]:
    bs = f"{int(bs_kib)}k" if bs_kib == int(bs_kib) else f"{bs_kib}k"
    engine = "windowsaio" if platform.system() == "Windows" else "libaio"
    cmd = [
        "fio",
        "--name=azprobe",
        f"--filename={Path(test_file)}",
        f"--rw={rw}",
        "--direct=1",
        f"--ioengine={engine}",
        f"--iodepth={iodepth}",
        f"--bs={bs}",
        f"--runtime={runtime}",
        "--time_based",
        "--group_reporting",
        "--output-format=json",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "fio failed")
    payload = proc.stdout.strip()
    start = payload.find("{")
    if start < 0:
        raise RuntimeError(payload[:500] or "fio produced no JSON")
    return json.loads(payload[start:])


def parse_fio(raw: dict[str, Any], kind: str, rw: str, bs_kib: float, iodepth: int) -> FioRun:
    job = raw["jobs"][0]
    # Sequential is a write job here (fresh disk reads can be sparse-fast);
    # random IOPS is a read job. Pick whichever side did the work.
    side = job["write"] if job["write"]["io_bytes"] >= job["read"]["io_bytes"] else job["read"]
    opts = job.get("job options", {})
    direct = str(opts.get("direct", "0")) == "1"
    iops = float(side["iops"])
    bw_kib = float(side["bw"])  # KiB/s
    clat = side.get("clat_ns") or {}
    slat = side.get("slat_ns") or {}
    lat_us = float((slat.get("mean") or 0) + (clat.get("mean") or 0)) / 1000.0
    qd = float(job.get("iodepth_level", {}).get("mean", iodepth) or iodepth)

    ok, reason = True, None
    if not direct:
        ok, reason = False, "O_DIRECT not engaged — page cache would report RAM, not disk"
    elif iodepth >= 4 and qd < max(2.0, iodepth * 0.25):
        ok, reason = False, f"queue depth collapsed ({qd:.1f} vs {iodepth})"
    elif iops <= 0 or bw_kib <= 0:
        ok, reason = False, "zero throughput"
    return FioRun(
        kind=kind,
        rw=rw,
        bs_kib=bs_kib,
        iodepth=iodepth,
        iops=iops,
        bw_kib_s=bw_kib,
        bw_mbps=bw_kib * 1024 / MB,
        lat_us=lat_us,
        iodepth_mean=qd,
        direct=direct,
        ok=ok,
        reject_reason=reason,
    )


def device_info(dev: str) -> tuple[str | None, str | None]:
    transport = model = None
    try:
        tran = subprocess.run(
            ["lsblk", "-no", "TRAN", dev], capture_output=True, text=True, check=False
        )
        if tran.stdout.strip():
            transport = tran.stdout.strip().splitlines()[0]
    except OSError:
        pass
    model_path = Path(f"/sys/block/{Path(dev).name}/device/model")
    if model_path.is_file():
        model = model_path.read_text(encoding="utf-8", errors="replace").strip()
    return transport, model


def check_delivery_against_ceiling(
    runs: list[FioRun], settable_mbps: float | None
) -> list[str]:
    """The device-identity guard. Premium SSD v2 cannot deliver more than the
    throughput Azure let you set, so measured MB/s materially above the ceiling
    means fio hit the wrong device (typically the VM's local NVMe temp disk),
    not the managed disk under test."""
    warns: list[str] = []
    if not settable_mbps:
        warns.append(
            "no --settable-mbps given: cannot check delivery against the ceiling, "
            "and cannot build a ceiling validation case"
        )
        return warns
    tp = next((r for r in runs if r.kind == "throughput" and r.ok), None)
    if tp and tp.bw_mbps > settable_mbps * 1.10:
        warns.append(
            f"delivered {tp.bw_mbps:.0f} MB/s exceeds the {settable_mbps:.0f} MB/s "
            f"settable ceiling by >10% — wrong device? (a local NVMe temp disk, "
            f"not the Premium SSD v2). Confirm --device is the managed disk."
        )
    return warns


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--test-file", required=True, help="a path ON the mounted Premium SSD v2 disk")
    ap.add_argument("--device", required=True, help="the disk's block device, e.g. /dev/sdc")
    ap.add_argument("--provisioned-iops", type=int, default=None, help="from az disk show diskIOPSReadWrite")
    ap.add_argument("--settable-mbps", type=float, default=None, help="from az disk show diskMBpsReadWrite (set to the max Azure allows for these IOPS)")
    ap.add_argument("--disk-size-gib", type=float, default=None)
    ap.add_argument("--seq-bs-kib", type=float, default=256.0, help="sequential block size for throughput")
    ap.add_argument("--rand-bs-kib", type=float, default=4.0, help="random block size for IOPS")
    ap.add_argument("--runtime", type=float, default=30.0)
    ap.add_argument("--iodepth", type=int, default=32)
    args = ap.parse_args()

    transport, model = device_info(args.device)
    runs: list[FioRun] = []
    runs.append(
        parse_fio(
            run_fio(args.test_file, "write", args.seq_bs_kib, args.runtime, args.iodepth),
            "throughput", "write", args.seq_bs_kib, args.iodepth,
        )
    )
    time.sleep(1.0)
    runs.append(
        parse_fio(
            run_fio(args.test_file, "randread", args.rand_bs_kib, args.runtime, args.iodepth),
            "iops", "randread", args.rand_bs_kib, args.iodepth,
        )
    )

    result = ProbeResult(
        device=args.device,
        transport=transport,
        model=model,
        provisioned_iops=args.provisioned_iops,
        settable_mbps=args.settable_mbps,
        disk_size_gib=args.disk_size_gib,
        runs=runs,
        guards={
            "delivery_vs_ceiling_warnings": check_delivery_against_ceiling(
                runs, args.settable_mbps
            ),
            "rejected_runs": [r.kind for r in runs if not r.ok],
        },
    )

    print("===JSON===")
    print(json.dumps(asdict(result), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
