#!/usr/bin/env python3
"""I/O size vs throughput crossover probe — issue #17.

Random read sweep at increasing block sizes. Finds where a device stops being
IOPS-bound and becomes throughput-bound, and records the flat plateaus below
and above the knee.

Arm A (Docker blkio emulation) and Arm B (local unthrottled) share this driver.
See docs/plans/issue-17-io-crossover-nvme-baseline.md.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class Point:
    bs_kib: float
    iops: float
    bw_kib_s: float
    lat_us: float
    iodepth_mean: float
    direct: bool
    ok: bool
    reject_reason: str | None = None


@dataclass
class Plateau:
    kind: str  # "iops" | "throughput"
    value: float
    unit: str
    bs_kib_lo: float
    bs_kib_hi: float


@dataclass
class Crossover:
    bs_kib: float
    method: str
    predicted_kib: float | None = None


@dataclass
class ArmResult:
    arm: str
    device: str
    transport: str | None
    model: str | None
    throttle_iops: int | None
    throttle_bps: int | None
    points: list[Point]
    plateaus: list[Plateau]
    crossover: Crossover | None
    guards: dict[str, Any]


def run_fio(
    test_file: str,
    bs_kib: float,
    runtime: float,
    iodepth: int,
) -> dict[str, Any]:
    bs = f"{int(bs_kib)}k" if bs_kib == int(bs_kib) else f"{bs_kib}k"
    engine = "windowsaio" if platform.system() == "Windows" else "libaio"
    filename = Path(test_file).as_posix() if platform.system() == "Windows" else str(Path(test_file))
    cmd = [
        "fio",
        "--name=probe",
        f"--filename={filename}",
        "--rw=randread",
        "--direct=1",
        f"--ioengine={engine}",
        f"--iodepth={iodepth}",
        f"--bs={bs}",
        f"--runtime={runtime}",
        "--time_based",
        "--group_reporting",
        "--output-format=json",
    ]
    if platform.system() == "Windows":
        cmd.append("--thread")
        # windowsaio + sparse createnew files need an explicit size= or fio
        # errors with Invalid argument in filesetup.c.
        try:
            nbytes = Path(test_file).stat().st_size
            if nbytes > 0:
                cmd.append(f"--size={nbytes}")
        except OSError:
            pass
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "fio failed")
    payload = proc.stdout.strip()
    if not payload:
        raise RuntimeError(proc.stderr.strip() or "fio produced no JSON on stdout")
    if not payload.startswith("{"):
        start = payload.find("{")
        if start < 0:
            raise RuntimeError(payload[:500] or "fio stdout had no JSON object")
        payload = payload[start:]
    return json.loads(payload)


def parse_fio(raw: dict[str, Any], bs_kib: float, iodepth: int) -> Point:
    job = raw["jobs"][0]
    read = job["read"]
    opts = job.get("job options", {})
    direct = str(opts.get("direct", "0")) == "1"
    iops = float(read["iops"])
    bw = float(read["bw"])  # KiB/s
    lat_us = float(read["lat_ns"]["mean"]) / 1000.0
    slat = read.get("slat_ns") or {}
    clat = read.get("clat_ns") or {}
    lat_total = float((slat.get("mean") or 0) + (clat.get("mean") or 0)) / 1000.0
    if lat_total > 0:
        lat_us = lat_total
    qd = float(read.get("iodepth_level", {}).get("mean", iodepth))
    if qd == 0:
        qd = float(iodepth)
    ok = True
    reason = None
    if not direct:
        ok = False
        reason = "O_DIRECT not engaged"
    elif iodepth >= 4 and qd < max(2.0, iodepth * 0.25):
        ok = False
        reason = f"queue depth collapsed ({qd:.1f} vs {iodepth} configured)"
    elif iops <= 0 or bw <= 0:
        ok = False
        reason = "zero throughput"
    return Point(bs_kib, iops, bw, lat_us, qd, direct, ok, reason)


def device_info(dev: str) -> tuple[str | None, str | None]:
    transport = model = None
    base = Path(dev).name
    try:
        tran = subprocess.run(
            ["lsblk", "-no", "TRAN", dev],
            capture_output=True,
            text=True,
            check=False,
        )
        if tran.stdout.strip():
            transport = tran.stdout.strip()
    except OSError:
        pass
    model_path = Path(f"/sys/block/{base}/device/model")
    if model_path.is_file():
        model = model_path.read_text(encoding="utf-8", errors="replace").strip()
    return transport, model


def predicted_crossover_kib(iops: int | None, bps: int | None) -> float | None:
    if not iops or not bps:
        return None
    mibps = bps / (1024 * 1024)
    return mibps * 1024 / iops


def find_plateaus(points: list[Point]) -> list[Plateau]:
    good = [p for p in points if p.ok]
    if len(good) < 3:
        return []
    out: list[Plateau] = []
    small = sorted(good, key=lambda p: p.bs_kib)[: max(3, len(good) // 3)]
    iops_vals = [p.iops for p in small]
    if iops_vals and statistics.pstdev(iops_vals) / max(statistics.mean(iops_vals), 1) < 0.15:
        out.append(
            Plateau(
                "iops",
                statistics.mean(iops_vals),
                "iops",
                min(p.bs_kib for p in small),
                max(p.bs_kib for p in small),
            )
        )
    large = sorted(good, key=lambda p: p.bs_kib)[-max(3, len(good) // 3) :]
    bw_vals = [p.bw_kib_s for p in large]
    if bw_vals and statistics.pstdev(bw_vals) / max(statistics.mean(bw_vals), 1) < 0.12:
        out.append(
            Plateau(
                "throughput",
                statistics.mean(bw_vals),
                "KiB/s",
                min(p.bs_kib for p in large),
                max(p.bs_kib for p in large),
            )
        )
    return out


def find_crossover(points: list[Point], predicted: float | None) -> Crossover | None:
    good = sorted([p for p in points if p.ok], key=lambda p: p.bs_kib)
    if len(good) < 4:
        return None
    iops_plateau = statistics.mean(p.iops for p in good[:3])
    bw_plateau = statistics.mean(p.bw_kib_s for p in good[-3:])
    if iops_plateau <= 0 or bw_plateau <= 0:
        return None
    threshold_iops = iops_plateau * 0.85
    for p in good:
        if p.iops < threshold_iops and p.bw_kib_s >= bw_plateau * 0.85:
            return Crossover(p.bs_kib, "iops_drop_with_full_bw", predicted)
    return Crossover(good[-1].bs_kib, "no_knee_in_sweep", predicted)


def validate_throttle_plateau(
    points: list[Point],
    throttle_iops: int | None,
    throttle_bps: int | None,
    tolerance: float = 0.15,
) -> list[str]:
    warns: list[str] = []
    if not throttle_iops and not throttle_bps:
        return warns
    good = [p for p in points if p.ok]
    if not good:
        return ["no valid points"]
    small = sorted(good, key=lambda p: p.bs_kib)[:3]
    large = sorted(good, key=lambda p: p.bs_kib)[-3:]
    if throttle_iops:
        mean_iops = statistics.mean(p.iops for p in small)
        if mean_iops > throttle_iops * (1 + tolerance):
            warns.append(
                f"IOPS plateau {mean_iops:.0f} exceeds throttle {throttle_iops} — limit may not bind"
            )
    if throttle_bps:
        mean_bw = statistics.mean(p.bw_kib_s for p in large)
        cap_kib = throttle_bps / 1024
        if mean_bw > cap_kib * (1 + tolerance):
            warns.append(
                f"throughput plateau {mean_bw:.0f} KiB/s exceeds throttle {cap_kib:.0f} KiB/s"
            )
    return warns


def run_arm(
    *,
    arm: str,
    test_file: str,
    device: str,
    sizes_kib: list[float],
    runtime: float,
    iodepth: int,
    throttle_iops: int | None = None,
    throttle_bps: int | None = None,
) -> ArmResult:
    transport, model = device_info(device)
    points: list[Point] = []
    for bs in sizes_kib:
        raw = run_fio(test_file, bs, runtime, iodepth)
        points.append(parse_fio(raw, bs, iodepth))
        time.sleep(0.5)
    predicted = predicted_crossover_kib(throttle_iops, throttle_bps)
    plateaus = find_plateaus(points)
    crossover = find_crossover(points, predicted)
    guards = {
        "throttle_warnings": validate_throttle_plateau(
            points, throttle_iops, throttle_bps
        ),
        "predicted_crossover_kib": predicted,
    }
    return ArmResult(
        arm=arm,
        device=device,
        transport=transport,
        model=model,
        throttle_iops=throttle_iops,
        throttle_bps=throttle_bps,
        points=points,
        plateaus=plateaus,
        crossover=crossover,
        guards=guards,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--test-file", required=True)
    ap.add_argument("--device", required=True)
    ap.add_argument("--arm", default="local")
    ap.add_argument("--sizes-kib", default="4,8,16,32,64,128,256,512,1024")
    ap.add_argument("--runtime", type=float, default=10.0)
    ap.add_argument("--iodepth", type=int, default=32)
    ap.add_argument("--throttle-iops", type=int, default=None)
    ap.add_argument("--throttle-bps", type=int, default=None)
    args = ap.parse_args()

    sizes = [float(x.strip()) for x in args.sizes_kib.split(",") if x.strip()]
    result = run_arm(
        arm=args.arm,
        test_file=args.test_file,
        device=args.device,
        sizes_kib=sizes,
        runtime=args.runtime,
        iodepth=args.iodepth,
        throttle_iops=args.throttle_iops,
        throttle_bps=args.throttle_bps,
    )

    print("===JSON===", file=sys.stderr)
    print(json.dumps(asdict(result), indent=2), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
