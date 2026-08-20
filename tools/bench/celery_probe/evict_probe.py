"""Redis broker maxmemory probe (issue #15 / roadmap T7).

Phase 1 builds a Celery backlog with no consumer until the broker is at
maxmemory. Phase 2 spawns a worker while the broker is still at/over the
ceiling and measures whether tasks are lost, duplicated, or the fleet stalls.

Ground truth for executions lives in a separate bookkeeping Redis that is
never subject to maxmemory — counters on the broker itself would silently
undercount when their keys get evicted.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

import celery
import redis

sys.path.insert(0, os.path.dirname(__file__))
from tasks import app  # noqa: E402

BROKER_URL = os.environ.get("PROBE_REDIS", "redis://redis:6379/0")
BOOKKEEPING_URL = os.environ.get("PROBE_BOOKKEEPING", "redis://bookkeeping:6379/0")
PAYLOAD_BYTES = int(os.environ.get("PROBE_PAYLOAD_BYTES", "2048"))
ENQUEUE_ATTEMPTS = int(os.environ.get("PROBE_ENQUEUE_ATTEMPTS", "20000"))
DRAIN_TIMEOUT = float(os.environ.get("PROBE_DRAIN_TIMEOUT", "120"))
MAXMEMORY_POLICY = os.environ.get("PROBE_MAXMEMORY_POLICY", "noeviction")
QUEUE = os.environ.get("PROBE_QUEUE", "celery")
MIN_MEMORY_RATIO = float(os.environ.get("PROBE_MIN_MEMORY_RATIO", "0.95"))
CEILING_TOLERANCE = float(os.environ.get("PROBE_CEILING_TOLERANCE", "0.01"))
CEILING_SAMPLES = int(os.environ.get("PROBE_CEILING_SAMPLES", "3"))
CONCURRENCY = os.environ.get("PROBE_CONCURRENCY", "8")
IGNORE_RESULT = os.environ.get("PROBE_IGNORE_RESULT", "1") == "1"

broker = redis.Redis.from_url(BROKER_URL)
bookkeeping = redis.Redis.from_url(BOOKKEEPING_URL)
PAD = "x" * PAYLOAD_BYTES


def memory_snapshot() -> dict:
    mem = broker.info("memory")
    stats = broker.info("stats")
    used = int(mem.get("used_memory", 0))
    ceiling = int(mem.get("maxmemory", 0))
    ratio = round(used / ceiling, 4) if ceiling else None
    return {
        "usedMemory": used,
        "maxmemory": ceiling,
        "usedMemoryRatio": ratio,
        "maxmemoryPolicy": mem.get("maxmemory_policy", ""),
        "evictedKeys": int(stats.get("evicted_keys", 0)),
    }


def reset() -> None:
    for client in (broker, bookkeeping):
        for key in client.scan_iter("probe:*"):
            client.delete(key)
    broker.delete(QUEUE)


def bookkeeping_counts() -> dict:
    def g(key: str) -> int:
        val = bookkeeping.get(key)
        return int(val) if val else 0

    try:
        return {
            "executions": g("probe:executions"),
            "distinctExecuted": bookkeeping.scard("probe:executed_ids"),
            "duplicates": g("probe:duplicates"),
        }
    except redis.RedisError as exc:
        return {
            "executions": 0,
            "distinctExecuted": 0,
            "duplicates": 0,
            "error": str(exc),
        }


def safe_broker_llen() -> int | None:
    try:
        return broker.llen(QUEUE)
    except redis.RedisError:
        return None


def safe_memory_snapshot() -> dict:
    try:
        return memory_snapshot()
    except redis.RedisError as exc:
        return {"error": str(exc)}


def at_ceiling(snapshot: dict) -> bool:
    ratio = snapshot.get("usedMemoryRatio")
    if ratio is None:
        return False
    return ratio >= (1.0 - CEILING_TOLERANCE)


def phase1() -> dict:
    reset()
    evicted_start = memory_snapshot()["evictedKeys"]
    attempts = 0
    enqueued_ok = 0
    enqueue_errors: list[dict] = []
    ceiling_hits = 0
    peak_ratio = 0.0
    t0 = time.time()
    next_sample = t0

    while attempts < ENQUEUE_ATTEMPTS:
        attempts += 1
        try:
            app.send_task("probe.noop", kwargs={"pad": PAD}, queue=QUEUE)
            enqueued_ok += 1
        except Exception as exc:  # noqa: BLE001 — record broker rejection verbatim
            enqueue_errors.append(
                {
                    "attempt": attempts,
                    "type": exc.__class__.__name__,
                    "message": str(exc),
                }
            )

        now = time.time()
        if now >= next_sample:
            snap = memory_snapshot()
            ratio = snap.get("usedMemoryRatio")
            if ratio is not None:
                peak_ratio = max(peak_ratio, ratio)
            if MAXMEMORY_POLICY == "noeviction" and at_ceiling(snap):
                ceiling_hits += 1
                if ceiling_hits >= CEILING_SAMPLES:
                    break
            elif MAXMEMORY_POLICY != "noeviction" and snap["evictedKeys"] - evicted_start > 0:
                ceiling_hits += 1
                if ceiling_hits >= CEILING_SAMPLES and enqueued_ok >= 500:
                    break
            else:
                ceiling_hits = 0
            next_sample += 0.5

    end_snap = memory_snapshot()
    elapsed = round(time.time() - t0, 2)
    ratio = end_snap.get("usedMemoryRatio")
    evicted_delta = end_snap["evictedKeys"] - evicted_start
    reached_maxmemory = ratio is not None and ratio >= MIN_MEMORY_RATIO
    reached_pressure = (
        evicted_delta > 0 and enqueued_ok >= 500
        if MAXMEMORY_POLICY != "noeviction"
        else reached_maxmemory
    )

    return {
        "attempts": attempts,
        "enqueuedOk": enqueued_ok,
        "enqueueErrors": enqueue_errors,
        "enqueueErrorCount": len(enqueue_errors),
        "seconds": elapsed,
        "endMemory": end_snap,
        "peakMemoryRatio": round(peak_ratio, 4),
        "reachedMaxmemory": reached_maxmemory,
        "reachedPressure": reached_pressure,
        "evictedKeysDelta": evicted_delta,
        "queueDepthAtEnd": broker.llen(QUEUE),
    }


def phase2(enqueued_ok: int) -> dict:
    before = bookkeeping_counts()
    queue_start = broker.llen(QUEUE)
    evicted_start = memory_snapshot()["evictedKeys"]
    t0 = time.time()
    first_consumption = None
    worker_proc = subprocess.Popen(
        [
            "celery",
            "-A",
            "tasks",
            "worker",
            "--loglevel=warning",
            f"--concurrency={CONCURRENCY}",
            "--without-gossip",
            "--without-mingle",
            "--without-heartbeat",
        ],
        env=os.environ.copy(),
    )

    samples: list[dict] = []
    drained_in = None
    worker_alive_at_end = worker_proc.poll() is None

    try:
        while time.time() - t0 < DRAIN_TIMEOUT:
            now = time.time()
            counts = bookkeeping_counts()
            queue_depth = safe_broker_llen()
            snap = safe_memory_snapshot()

            if first_consumption is None and counts.get("executions", 0) > before.get(
                "executions", 0
            ):
                first_consumption = round(now - t0, 2)

            samples.append(
                {
                    "t": round(now - t0, 1),
                    "queue": queue_depth,
                    **counts,
                    **snap,
                    "workerAlive": worker_proc.poll() is None,
                }
            )

            if (
                queue_depth == 0
                and counts.get("distinctExecuted", 0) >= enqueued_ok
            ):
                drained_in = round(now - t0, 1)
                break

            if worker_proc.poll() is not None and first_consumption is None:
                break

            time.sleep(0.5)
    finally:
        if worker_proc.poll() is None:
            worker_proc.send_signal(signal.SIGTERM)
            try:
                worker_proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                worker_proc.kill()
                worker_proc.wait(timeout=10)

    final = bookkeeping_counts()
    end_snap = safe_memory_snapshot()
    worker_starts = first_consumption is not None
    worker_exit_code = worker_proc.poll()
    if "evictedKeys" in end_snap:
        evicted_delta = end_snap["evictedKeys"] - evicted_start
    else:
        evicted_delta = 0

    return {
        "workerProcessStarted": True,
        "workerExitCode": worker_exit_code,
        "workerStartsConsuming": worker_starts,
        "timeToFirstConsumptionSeconds": first_consumption,
        "drainSeconds": drained_in,
        "drainTimedOut": drained_in is None,
        "distinctExecuted": final.get("distinctExecuted", 0),
        "executionsTotal": final.get("executions", 0),
        "duplicateExecutions": final.get("duplicates", 0),
        "tasksLost": max(enqueued_ok - final.get("distinctExecuted", 0), 0),
        "taskLossRate": round(
            max(enqueued_ok - final.get("distinctExecuted", 0), 0) / max(enqueued_ok, 1),
            4,
        ),
        "duplicateRate": round(
            final.get("duplicates", 0) / max(final.get("executions", 1), 1),
            4,
        ),
        "evictedKeysDelta": evicted_delta,
        "endMemory": end_snap,
        "sampleCount": len(samples),
        "sampleSeries": samples,
    }


def guard_verdict(policy: str, phase1_result: dict, phase2_result: dict) -> dict:
    """Return guard pass/fail with reasons. A failed guard means the arm
    measured nothing useful and must not be reported as a finding."""
    reasons: list[str] = []
    passed = True

    if policy == "noeviction":
        if not phase1_result["reachedMaxmemory"]:
            passed = False
            ratio = phase1_result["endMemory"].get("usedMemoryRatio")
            reasons.append(
                f"Phase 1 never reached maxmemory (ratio={ratio}, need >={MIN_MEMORY_RATIO})"
            )
    elif policy in ("allkeys-lru", "volatile-lru"):
        if not phase1_result.get("reachedPressure"):
            passed = False
            reasons.append(
                "Phase 1 never reached eviction pressure "
                f"(evictedKeysDelta={phase1_result.get('evictedKeysDelta')}, "
                f"enqueuedOk={phase1_result.get('enqueuedOk')})"
            )

    evicted_total = phase1_result["evictedKeysDelta"] + phase2_result["evictedKeysDelta"]

    if policy == "noeviction":
        if evicted_total != 0:
            passed = False
            reasons.append(
                f"noeviction arm saw evicted_keys move by {evicted_total} — policy misconfigured"
            )
    elif policy == "allkeys-lru":
        if evicted_total <= 0:
            passed = False
            reasons.append("allkeys-lru arm never evicted a key — proves nothing about eviction")
    elif policy == "volatile-lru":
        if IGNORE_RESULT:
            reasons.append(
                "volatile-lru with task_ignore_result=True and no TTL keys — "
                "degenerates to noeviction; not a policy measurement"
            )
        elif evicted_total <= 0:
            passed = False
            reasons.append(
                "volatile-lru with results enabled but evicted_keys stayed at zero"
            )

    return {"passed": passed, "reasons": reasons}


def main() -> int:
    print(
        f"policy={MAXMEMORY_POLICY} payload={PAYLOAD_BYTES}B "
        f"maxmemory={memory_snapshot()['maxmemory']} "
        f"ignoreResult={IGNORE_RESULT}",
        file=sys.stderr,
    )

    p1 = phase1()
    print(
        f"phase1: attempts={p1['attempts']} enqueued={p1['enqueuedOk']} "
        f"errors={p1['enqueueErrorCount']} "
        f"memRatio={p1['endMemory'].get('usedMemoryRatio')} "
        f"q={p1['queueDepthAtEnd']}",
        file=sys.stderr,
    )

    if not p1["reachedPressure"] and not p1["reachedMaxmemory"]:
        print(
            f"\nREFUSING TO REPORT: Phase 1 never reached broker pressure "
            f"(policy={MAXMEMORY_POLICY}, memRatio={p1['endMemory'].get('usedMemoryRatio')}, "
            f"evictedKeysDelta={p1.get('evictedKeysDelta')}). "
            f"Tune PROBE_MAXMEMORY or PROBE_PAYLOAD_BYTES.",
            file=sys.stderr,
        )

    p2 = phase2(p1["enqueuedOk"])
    drain = "TIMEOUT" if p2["drainTimedOut"] else p2["drainSeconds"]
    print(
        f"phase2: executed={p2['distinctExecuted']}/{p1['enqueuedOk']} "
        f"lost={p2['tasksLost']} dupes={p2['duplicateExecutions']} "
        f"firstConsume={p2['timeToFirstConsumptionSeconds']}s drain={drain}",
        file=sys.stderr,
    )

    guards = guard_verdict(MAXMEMORY_POLICY, p1, p2)
    if not guards["passed"]:
        print("\nGUARD FAILED — arm is not reportable:", file=sys.stderr)
        for reason in guards["reasons"]:
            print(f"  - {reason}", file=sys.stderr)

    broker_info = broker.info()
    payload = {
        "probe": "redis-broker-maxmemory",
        "celeryVersion": celery.__version__,
        "redisPyVersion": redis.__version__,
        "redisServerVersion": broker_info.get("redis_version"),
        "maxmemoryPolicy": MAXMEMORY_POLICY,
        "payloadBytes": PAYLOAD_BYTES,
        "ignoreResult": IGNORE_RESULT,
        "resultExpires": app.conf.result_expires,
        "enqueueAttemptsLimit": ENQUEUE_ATTEMPTS,
        "drainTimeoutSeconds": DRAIN_TIMEOUT,
        "concurrency": int(CONCURRENCY),
        "phase1": p1,
        "phase2": p2,
        "guards": guards,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }

    print("===JSON===")
    print(json.dumps(payload, indent=1, default=str))
    if not p1["reachedMaxmemory"] and not p1.get("reachedPressure"):
        return 1
    if not guards["passed"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
