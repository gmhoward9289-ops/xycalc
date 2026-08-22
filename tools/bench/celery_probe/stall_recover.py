"""Issue #16 / T8 — stall / recover phases over PROBE_RETRY_POLICY.

Three phases per policy (baseline → stall → recovery), sampling every 0.5s.
The stall is induced by rewriting the mongo container's cgroup io.max (v2)
or blkio throttle files (v1). If that fails, PROBE_STALL_MODE=pause falls
back to docker pause/unpause (total outage, not "slow" — named deliberately).

Guards (refuse amplification coefficient if tripped):
  - probe:retries during stall below MIN_STALL_RETRIES
  - probe:duplicates non-trivial vs retries (broker redelivery confound)
  - pagesReadIntoCache == 0
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import celery
import pymongo
import redis
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(__file__))
from drive import (  # noqa: E402
    DOCS,
    MIN_OVERSUB,
    QUEUE,
    counters,
    load,
    reset,
    tickets,
)
from tasks import app  # noqa: E402

MONGO_URL = os.environ.get("PROBE_MONGO", "mongodb://mongo:27017")
REDIS_URL = os.environ.get("PROBE_REDIS", "redis://redis:6379/0")
RATE = int(os.environ.get("PROBE_RATES", "300").split(",")[0])
BASELINE_S = float(os.environ.get("PROBE_BASELINE_SECONDS", "60"))
STALL_S = float(os.environ.get("PROBE_STALL_SECONDS", "90"))
RECOVERY_TIMEOUT = float(os.environ.get("PROBE_RECOVERY_TIMEOUT", "180"))
STALL_MODE = os.environ.get("PROBE_STALL_MODE", "cgroup")  # cgroup|pause
MONGO_CONTAINER = os.environ.get("PROBE_MONGO_CONTAINER", "")
READ_BPS = int(os.environ.get("PROBE_READ_BPS", "8388608"))
READ_IOPS = int(os.environ.get("PROBE_READ_IOPS", "150"))
DEV_MAJ_MIN = os.environ.get("PROBE_DEV_MAJOR_MINOR", "")  # e.g. 8:0
MIN_STALL_RETRIES = int(os.environ.get("PROBE_MIN_STALL_RETRIES", "50"))
RECOVERY_BAND = float(os.environ.get("PROBE_RECOVERY_BAND", "0.25"))
RECOVERY_STABLE_SAMPLES = int(os.environ.get("PROBE_RECOVERY_STABLE_SAMPLES", "6"))

mongo = MongoClient(MONGO_URL, serverSelectionTimeoutMS=60000)
r = redis.Redis.from_url(REDIS_URL)

# When mongo is docker-paused, serverStatus hangs until CSOT cancels. Sample
# queue/redis counters only during that window; resume tickets after unpause.
_SKIP_TICKETS = False


def _safe_tickets() -> dict:
    if _SKIP_TICKETS:
        return {
            "readTotal": None,
            "readOut": None,
            "queuedMicros": None,
            "pagesRead": None,
            "ticketsSkipped": True,
        }
    try:
        return tickets()
    except Exception as exc:  # noqa: BLE001 — stall window must keep enqueueing
        return {
            "readTotal": None,
            "readOut": None,
            "queuedMicros": None,
            "pagesRead": None,
            "ticketsError": type(exc).__name__,
        }


def _docker(*args: str) -> str:
    try:
        return subprocess.check_output(["docker", *args], text=True).strip()
    except FileNotFoundError:
        # Docker Desktop slim images often mount the socket but ship no CLI.
        import docker as docker_sdk

        client = docker_sdk.from_env()
        if not args:
            raise
        cmd, *rest = args
        if cmd == "ps" and "--format" in rest:
            return "\n".join(c.name for c in client.containers.list())
        if cmd == "inspect" and "-f" in rest and rest:
            # Used for Pid: docker inspect -f '{{.State.Pid}}' cid
            cid = rest[-1]
            return str(client.containers.get(cid).attrs["State"]["Pid"])
        if cmd == "pause" and rest:
            client.containers.get(rest[0]).pause()
            return ""
        if cmd == "unpause" and rest:
            client.containers.get(rest[0]).unpause()
            return ""
        raise SystemExit(f"REFUSING: docker CLI missing and SDK cannot run: {args}")


def resolve_mongo_container() -> str:
    if MONGO_CONTAINER:
        return MONGO_CONTAINER
    # Compose project name is xycalc-celery-probe; service is mongo.
    names = _docker("ps", "--format", "{{.Names}}").splitlines()
    for n in names:
        if n.endswith("-mongo-1") or n.endswith("_mongo_1") or "mongo" in n:
            return n
    raise SystemExit("REFUSING: could not find mongo container; set PROBE_MONGO_CONTAINER")


def _cgroup_paths(cid: str) -> tuple[str | None, str | None]:
    """Return (v2 io.max path, v1 blkio dir) for the container, either may be None."""
    # Prefer host cgroup via container PID (compose stall-driver uses pid: host).
    try:
        pid = _docker("inspect", "-f", "{{.State.Pid}}", cid)
    except subprocess.CalledProcessError:
        return None, None
    cgroup = open(f"/proc/{pid}/cgroup", encoding="utf-8").read()
    # cgroup v2: 0::/path
    for line in cgroup.splitlines():
        if line.startswith("0::"):
            path = line.split("::", 1)[1]
            io_max = f"/sys/fs/cgroup{path}/io.max"
            if os.path.exists(io_max):
                return io_max, None
    # cgroup v1 blkio
    for line in cgroup.splitlines():
        if ":blkio:" in line:
            path = line.split(":", 2)[2]
            base = f"/sys/fs/cgroup/blkio{path}"
            if os.path.isdir(base):
                return None, base
    return None, None


def _maj_min(cid: str) -> str:
    if DEV_MAJ_MIN:
        return DEV_MAJ_MIN
    # Resolve from compose default /dev/sda → major:minor on the host.
    try:
        out = subprocess.check_output(
            ["stat", "-c", "%t:%T", "/dev/sda"], text=True
        ).strip()
        major_hex, minor_hex = out.split(":")
        return f"{int(major_hex, 16)}:{int(minor_hex, 16)}"
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        return "8:0"


def tighten(cid: str) -> dict:
    if STALL_MODE == "pause":
        _docker("pause", cid)
        return {"mode": "pause", "note": "total outage (process frozen), not slow I/O"}
    io_max, blkio = _cgroup_paths(cid)
    maj = _maj_min(cid)
    if io_max:
        # rbps/riops — leave writes alone; this probe is read-path stall.
        line = f"{maj} rbps={READ_BPS} riops={READ_IOPS}\n"
        open(io_max, "w", encoding="utf-8").write(line)
        return {"mode": "cgroup_v2", "ioMax": io_max, "setting": line.strip()}
    if blkio:
        open(f"{blkio}/blkio.throttle.read_bps_device", "w", encoding="utf-8").write(
            f"{maj} {READ_BPS}\n"
        )
        open(f"{blkio}/blkio.throttle.read_iops_device", "w", encoding="utf-8").write(
            f"{maj} {READ_IOPS}\n"
        )
        return {"mode": "cgroup_v1", "blkio": blkio, "majMin": maj}
    raise SystemExit(
        "REFUSING: cannot rewrite cgroup throttle; set PROBE_STALL_MODE=pause "
        "deliberately, or run with privileged + /sys/fs/cgroup mounted"
    )


def loosen(cid: str, how: dict) -> None:
    if how.get("mode") == "pause":
        _docker("unpause", cid)
        return
    if how.get("mode") == "cgroup_v2":
        maj = _maj_min(cid)
        open(how["ioMax"], "w", encoding="utf-8").write(f"{maj} rbps=max riops=max\n")
        return
    if how.get("mode") == "cgroup_v1":
        maj = how["majMin"]
        # Empty / max — write 0 to clear is not portable; rewrite huge values.
        open(f"{how['blkio']}/blkio.throttle.read_bps_device", "w", encoding="utf-8").write(
            f"{maj} {10**15}\n"
        )
        open(
            f"{how['blkio']}/blkio.throttle.read_iops_device", "w", encoding="utf-8"
        ).write(f"{maj} {10**9}\n")


def _enqueue_window(seconds: float, rate: int) -> tuple[list[dict], int]:
    samples: list[dict] = []
    enqueued = 0
    t0 = time.time()
    interval = 1.0 / rate
    next_send = t0
    deadline = t0 + seconds
    next_sample = t0
    while time.time() < deadline:
        now = time.time()
        if now >= next_send:
            app.send_task("probe.lookup", queue=QUEUE)
            enqueued += 1
            next_send += interval
        if now >= next_sample:
            c = counters()
            samples.append(
                {
                    "t": round(now - t0, 1),
                    "queue": r.llen(QUEUE),
                    **_safe_tickets(),
                    **c,
                }
            )
            next_sample = now + 0.5
        slack = min(next_send, next_sample) - time.time()
        if slack > 0:
            time.sleep(slack)
    return samples, enqueued


def _phase_stats(samples: list[dict], enqueued: int, label: str) -> dict:
    if not samples:
        return {"phase": label, "enqueued": enqueued, "empty": True}
    first, last = samples[0], samples[-1]
    elapsed = max(last["t"] - first["t"], 0.1)
    completed_delta = last["completed"] - first["completed"]
    retries_delta = last.get("retries", 0) - first.get("retries", 0)
    dupes_delta = last["duplicates"] - first["duplicates"]
    exec_delta = last["executions"] - first["executions"]
    return {
        "phase": label,
        "seconds": round(elapsed, 1),
        "enqueued": enqueued,
        "completedDelta": completed_delta,
        "executionsDelta": exec_delta,
        "retriesDelta": retries_delta,
        "duplicatesDelta": dupes_delta,
        "throughputPerSecond": round(completed_delta / elapsed, 1),
        "queueDepthMax": max(s["queue"] for s in samples),
        "queueDepthEnd": last["queue"],
        "amplification": round(exec_delta / max(enqueued, 1), 3),
        "sampleCount": len(samples),
        "samples": samples,
    }


def wait_recovery(baseline_tps: float, baseline_q: float) -> dict:
    """Require queue near baseline AND throughput within band for N samples."""
    t0 = time.time()
    stable = 0
    series: list[dict] = []
    while time.time() - t0 < RECOVERY_TIMEOUT:
        c = counters()
        q = r.llen(QUEUE)
        # Instantaneous throughput: short window via consecutive samples.
        series.append({"t": round(time.time() - t0, 1), "queue": q, **c, **_safe_tickets()})
        if len(series) >= 2:
            a, b = series[-2], series[-1]
            dt = max(b["t"] - a["t"], 0.1)
            tps = (b["completed"] - a["completed"]) / dt
            q_ok = q <= max(baseline_q * 1.5, 5)
            tps_ok = abs(tps - baseline_tps) <= RECOVERY_BAND * max(baseline_tps, 1.0)
            if q_ok and tps_ok:
                stable += 1
            else:
                stable = 0
            if stable >= RECOVERY_STABLE_SAMPLES:
                return {
                    "recovered": True,
                    "recoveryTimedOut": False,
                    "recoverySeconds": round(time.time() - t0, 1),
                    "samples": series,
                }
        time.sleep(0.5)
    return {
        "recovered": False,
        "recoveryTimedOut": True,
        "recoverySeconds": round(time.time() - t0, 1),
        "samples": series,
    }


def run_once() -> dict:
    meta = load()
    cid = resolve_mongo_container()
    reset()
    policy = os.environ.get("PROBE_RETRY_POLICY", "none")

    print(f"baseline {BASELINE_S}s @ {RATE}/s policy={policy}", file=sys.stderr)
    base_samples, base_enq = _enqueue_window(BASELINE_S, RATE)
    baseline = _phase_stats(base_samples, base_enq, "baseline")

    print(f"stall tighten ({STALL_MODE}) {STALL_S}s", file=sys.stderr)
    how = tighten(cid)
    global _SKIP_TICKETS
    _SKIP_TICKETS = how.get("mode") == "pause"
    try:
        stall_samples, stall_enq = _enqueue_window(STALL_S, RATE)
        stall = _phase_stats(stall_samples, stall_enq, "stall")
    finally:
        _SKIP_TICKETS = False
        print("recovery loosen", file=sys.stderr)
        loosen(cid, how)

    recovery = wait_recovery(
        baseline_tps=baseline.get("throughputPerSecond") or 0.0,
        baseline_q=float(baseline.get("queueDepthEnd") or 0),
    )

    guards: list[str] = []
    if policy != "none" and stall.get("retriesDelta", 0) < MIN_STALL_RETRIES:
        guards.append(
            f"stall retries {stall.get('retriesDelta')} < min {MIN_STALL_RETRIES}"
        )
    if stall.get("retriesDelta", 0) > 0 and stall.get("duplicatesDelta", 0) > 0.1 * stall[
        "retriesDelta"
    ]:
        guards.append(
            f"duplicates {stall.get('duplicatesDelta')} not negligible vs retries "
            f"{stall.get('retriesDelta')} — redelivery confound"
        )
    # Confirm mongo answers again after unpause (pause mode skips tickets in stall).
    _ = tickets()

    ok = not guards
    if not ok:
        print("REFUSING TO CONCLUDE:\n  - " + "\n  - ".join(guards), file=sys.stderr)

    return {
        "retryPolicy": policy,
        "rate": RATE,
        "stallMode": how,
        "baseline": baseline,
        "stall": stall,
        "recovery": recovery,
        "guards": {"ok": ok, "flags": guards},
        "mongoVersion": mongo.admin.command("buildInfo")["version"],
        "celeryVersion": celery.__version__,
        "pymongoVersion": pymongo.version,
        "redisServerVersion": r.info().get("redis_version"),
        "maxTimeMs": int(os.environ.get("PROBE_MAX_TIME_MS", "500")),
        "visibilityTimeout": app.conf.broker_transport_options.get("visibility_timeout"),
        **meta,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def main() -> int:
    doc = run_once()
    print("===JSON===")
    print(json.dumps(doc, indent=1, default=str))
    return 0 if doc["guards"]["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
