"""Does MongoDB 7.0's throughputProbing climb off its floor when the bottleneck
is the DEVICE rather than concurrency?

Investigation 003's open question. An idle 7.0.39 instance rests at
totalTickets = 4, the documented minimum. The algorithm raises concurrency and
checks whether throughput improves; when the limit is a throttled disk, raising
concurrency does not improve throughput, it only deepens the device queue. So an
algorithm doing exactly what it was designed to do might sit at the floor
precisely when the floor hurts most.

Two outcomes, both worth knowing:
    climbs -> the pool adapts and 40 ops/s is a transient
    stays  -> 7.0+ is materially worse than 6.x in this failure

Written in Python rather than mongosh on purpose. mongosh auto-awaits its
driver calls, so N "concurrent" operations there run one after another and
measure nothing at all -- the first attempt at this harness had exactly that
bug. Real OS threads against a real connection pool are the only way to fill a
ticket pool.
"""

from __future__ import annotations

import json
import os
import random
import statistics
import string
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from pymongo import MongoClient
from pymongo import errors as pymongo_errors

# The only failures a sampler should ever swallow: transient connectivity
# problems where the *next* sample will very likely succeed once the network
# or a replica election settles. AutoReconnect and NetworkTimeout are both
# ConnectionFailure subclasses; ServerSelectionTimeoutError is included
# separately because this client sets serverSelectionTimeoutMS explicitly and
# a busy/reconnecting node can legitimately blow through it mid-run.
# Anything else -- a KeyError from a renamed serverStatus field, a TypeError
# from a reshaped document -- is a real bug and must crash the run instead of
# vanishing into an empty series. See issue #21.
TRANSIENT_ERRORS = (
    pymongo_errors.AutoReconnect,
    pymongo_errors.NetworkTimeout,
    pymongo_errors.ConnectionFailure,
    pymongo_errors.ServerSelectionTimeoutError,
)

URI = os.environ.get("PROBE_URI", "mongodb://127.0.0.1:27017")
LEVELS = [int(x) for x in os.environ.get("PROBE_LEVELS", "1,2,4,8,16,32,64").split(",")]
SECONDS = float(os.environ.get("PROBE_SECONDS", "25"))
DOCS = int(os.environ.get("PROBE_DOCS", "1500000"))
# The experiment is vacuous unless the data comfortably exceeds the cache.
MIN_OVERSUBSCRIPTION = float(os.environ.get("PROBE_MIN_OVERSUB", "2.0"))
# levels = concurrency ladder (investigation 003). timeseries = issue #12 / T4
# single-concurrency soak with 1s latency buckets + checkpoint series.
PROBE_MODE = os.environ.get("PROBE_MODE", "levels").strip().lower()
_sample_override = os.environ.get("PROBE_SAMPLE_S", "").strip()
SAMPLE_S = (
    float(_sample_override)
    if _sample_override
    else (1.0 if PROBE_MODE == "timeseries" else 0.5)
)
# Issue #3 — cooldown after the last climb level (0 disables).
COOLDOWN_SECONDS = float(os.environ.get("PROBE_COOLDOWN_SECONDS", "0"))
COOLDOWN_HEARTBEAT_HZ = float(os.environ.get("PROBE_COOLDOWN_HEARTBEAT_HZ", "0"))
CONVERGENCE_WINDOW_S = float(os.environ.get("PROBE_CONVERGENCE_WINDOW_S", "90"))
CONVERGENCE_TOL = float(os.environ.get("PROBE_CONVERGENCE_TOL", "0.05"))
TICKET_FLOOR = 4
FLOOR_HOLD_S = 60.0
# Issue #12 / T4 — refuse a "flat" conclusion without enough checkpoints.
MIN_CHECKPOINTS = int(os.environ.get("PROBE_MIN_CHECKPOINTS", "4"))
# Aggregate cross-check vs investigation 003's published c=8 row (optional).
REF_OPS_PER_S = float(os.environ.get("PROBE_REF_OPS_PER_S", "114.0"))
REF_MEAN_LAT_MS = float(os.environ.get("PROBE_REF_MEAN_LAT_MS", "70.1"))
REF_TOLERANCE = float(os.environ.get("PROBE_REF_TOLERANCE", "0.35"))

client = MongoClient(URI, maxPoolSize=max(LEVELS) + 8, serverSelectionTimeoutMS=30000)
db = client.ticketprobe
admin = client.admin


def tickets() -> dict:
    # Verified paths: 7.0.39 → wiredTiger.concurrentTransactions;
    # 8.0.29 / 8.2.12 → queues.execution (issue #7). Helper covers both.
    try:
        import sys
        from pathlib import Path as _P

        _here = _P(__file__).resolve().parent
        if str(_here) not in sys.path:
            sys.path.insert(0, str(_here))
        from mongo_tickets import execution_tickets
    except ImportError:
        def execution_tickets(server_status: dict) -> dict:
            queues = server_status.get("queues") or {}
            execution = queues.get("execution")
            if execution is not None:
                read = execution["read"]
                write = execution["write"]
                pri = read.get("normalPriority") or {}
                return {
                    "path": "queues.execution",
                    "readTotal": int(read["totalTickets"]),
                    "readOut": int(read["out"]),
                    "writeTotal": int(write["totalTickets"]),
                    "queueLength": int(pri.get("queueLength") or 0),
                    "queuedMicros": int(pri.get("totalTimeQueuedMicros") or 0),
                }
            wt = server_status.get("wiredTiger") or {}
            c = wt.get("concurrentTransactions") or {}
            read = c.get("read") or {}
            write = c.get("write") or {}
            return {
                "path": "wiredTiger.concurrentTransactions",
                "readTotal": int(read.get("totalTickets") or 0),
                "readOut": int(read.get("out") or 0),
                "writeTotal": int(write.get("totalTickets") or 0),
                "queueLength": int(read.get("queueLength") or 0),
                "queuedMicros": int(read.get("totalTimeQueuedMicros") or 0),
            }

    s = admin.command("serverStatus")
    t = execution_tickets(s)
    g = s["globalLock"]
    return {
        "readTotal": t["readTotal"],
        "readOut": t["readOut"],
        "writeTotal": t["writeTotal"],
        "queueLength": t["queueLength"],
        "queuedMicros": t["queuedMicros"],
        "currentQueueReaders": g["currentQueue"]["readers"],
        "activeReaders": g["activeClients"]["readers"],
    }


# Verified present on MongoDB 7.0.39 by reading a live serverStatus, not by
# reading documentation. The corpus has already been wrong twice about where a
# field lives (queues.execution assumed then absent on 7.0; concurrentTransactions
# assumed then absent on 8.x — issue #7).
CKPT_RUNNING = "transaction checkpoint currently running"
CKPT_GENERATION = "transaction checkpoint generation"
CKPT_RECENT_MS = "transaction checkpoint most recent time (msecs)"


def checkpoint() -> dict:
    tx = admin.command("serverStatus")["wiredTiger"]["transaction"]
    return {
        "ckptRunning": int(tx[CKPT_RUNNING]),
        "ckptGeneration": int(tx[CKPT_GENERATION]),
        "ckptRecentMs": int(tx[CKPT_RECENT_MS]),
    }


def cache_state() -> dict:
    c = admin.command("serverStatus")["wiredTiger"]["cache"]
    return {
        "inCache": c["bytes currently in the cache"],
        "maxCache": c["maximum bytes configured"],
        "readIntoCache": c["pages read into cache"],
        "evictedByApp": c["pages evicted by application threads"],
        # Confirmed present on 7.0.39; used by T4 to prove checkpoint I/O
        # actually reached the throttled device rather than a no-op clean flush.
        "bytesWrittenFromCache": c["bytes written from cache"],
    }


def load() -> None:
    """Write past the cache. A working set that fits makes the throttle
    irrelevant and the whole experiment vacuous."""
    print(f"loading {DOCS:,} documents...", file=sys.stderr)
    db.docs.drop()
    alphabet = string.ascii_lowercase + string.digits
    batch, n = [], 0
    for i in range(DOCS):
        batch.append(
            {
                "_id": i,
                "pad": "".join(random.choices(alphabet, k=700)),
                "k": "".join(random.choices(alphabet, k=24)),
            }
        )
        if len(batch) == 2000:
            db.docs.insert_many(batch, ordered=False)
            batch, n = [], n + 2000
    if batch:
        db.docs.insert_many(batch, ordered=False)
    st = db.command("collstats", "docs")
    cache_max = cache_state()["maxCache"]
    oversub = st["size"] / cache_max
    print(
        f"  loaded. dataSize={st['size'] / 1e6:.0f} MB  "
        f"storageSize={st['storageSize'] / 1e6:.0f} MB  "
        f"cache={cache_max / 1e6:.0f} MB  oversubscription={oversub:.1f}x",
        file=sys.stderr,
    )
    if oversub < MIN_OVERSUBSCRIPTION:
        # The first smoke run of this harness loaded 20,000 documents into a
        # 250 MB cache, reported pagesReadIntoCache = 0, and produced a clean
        # table of numbers that measured nothing at all. A throttled device is
        # irrelevant to a working set that fits. Refuse rather than publish a
        # confident result about the wrong thing.
        raise SystemExit(
            f"REFUSING TO RUN: data is only {oversub:.1f}x the cache "
            f"({st['size'] / 1e6:.0f} MB vs {cache_max / 1e6:.0f} MB). Random "
            f"reads would hit cache, no I/O would reach the throttled device, "
            f"and the result would look fine and mean nothing. Raise "
            f"PROBE_DOCS, or set PROBE_MIN_OVERSUB to override deliberately."
        )


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return round(s[min(int(q * len(s)), len(s) - 1)], 2)


def _per_second_buckets(
    latencies: list[tuple[float, float]], samples: list[dict], t0: float
) -> list[dict]:
    """1-second p50/p95/p99 joined to checkpoint-running flag (issue #12 / T4)."""
    by_sec: dict[int, list[float]] = {}
    for ts, ms in latencies:
        by_sec.setdefault(int(ts - t0), []).append(ms)

    ckpt_by_sec: dict[int, int] = {}
    written_by_sec: dict[int, int] = {}
    gen_by_sec: dict[int, int] = {}
    for s in samples:
        sec = int(s["t"] - t0)
        ckpt_by_sec[sec] = max(ckpt_by_sec.get(sec, 0), int(s.get("ckptRunning", 0)))
        if "bytesWrittenFromCache" in s:
            written_by_sec[sec] = int(s["bytesWrittenFromCache"])
        if "ckptGeneration" in s:
            gen_by_sec[sec] = int(s["ckptGeneration"])

    secs = sorted(set(by_sec) | set(ckpt_by_sec))
    rows = []
    prev_written = None
    for sec in secs:
        vals = by_sec.get(sec, [])
        written = written_by_sec.get(sec)
        written_delta = None
        if written is not None and prev_written is not None:
            written_delta = written - prev_written
        if written is not None:
            prev_written = written
        rows.append(
            {
                "t": sec,
                "ops": len(vals),
                "p50Ms": _pct(vals, 0.50),
                "p95Ms": _pct(vals, 0.95),
                "p99Ms": _pct(vals, 0.99),
                "ckptRunning": ckpt_by_sec.get(sec, 0),
                "ckptGeneration": gen_by_sec.get(sec),
                "bytesWrittenFromCacheDelta": written_delta,
            }
        )
    return rows


def timeseries_guards(
    buckets: list[dict],
    *,
    checkpoints_observed: int,
    sampler_errors: int,
    min_checkpoints: int = MIN_CHECKPOINTS,
) -> dict:
    """Loud refuse-to-conclude checks for issue #12 / T4.

    A flat per-second p99 series is exactly what a broken sampler prints, so
    these must fail the run rather than let 'no sawtooth' stand on empty data.
    """
    flags: list[str] = []
    running_vals = {int(b.get("ckptRunning") or 0) for b in buckets}
    toggled = 0 in running_vals and 1 in running_vals
    written_during = [
        b["bytesWrittenFromCacheDelta"]
        for b in buckets
        if b.get("ckptRunning") and b.get("bytesWrittenFromCacheDelta") is not None
    ]
    wrote_during_ckpt = any(d and d > 0 for d in written_during)

    if sampler_errors > 0:
        flags.append(f"sampler_errors={sampler_errors}")
    if checkpoints_observed < min_checkpoints:
        flags.append(
            f"checkpoints_observed={checkpoints_observed} < min={min_checkpoints}"
        )
    if not toggled:
        flags.append("ckptRunning never toggled 0<->1")
    if not wrote_during_ckpt:
        flags.append(
            "no bytesWrittenFromCache growth during checkpoint-active seconds "
            "(clean cache / no dirty flush — cannot conclude on checkpoint cost)"
        )

    ok = not flags
    return {
        "ok": ok,
        "refuseToConclude": not ok,
        "flags": flags,
        "ckptRunningToggled": toggled,
        "wroteDuringCheckpoint": wrote_during_ckpt,
        "checkpointsObserved": checkpoints_observed,
        "samplerErrors": sampler_errors,
        "minCheckpoints": min_checkpoints,
    }


def _checkpoint_split(latencies: list[tuple[float, float]], samples: list[dict]) -> dict:
    """Split latencies into seconds a checkpoint was running and seconds it was not.

    Investigation 003 reported throughput "flat" from a 25-second mean. If a
    periodic checkpoint stalls the device inside those windows, the mean hides
    exactly the kind of tail investigation 002 was about -- this corpus making,
    at small scale, the error it documented AWS's minute averages for making.
    """
    busy = {
        int(s["t"]) for s in samples if s.get("ckptRunning")
    }
    # A checkpoint shorter than the sample interval can still be caught by the
    # generation counter advancing between two samples.
    prev = None
    for s in sorted(samples, key=lambda x: x["t"]):
        gen = s.get("ckptGeneration")
        if prev is not None and gen is not None and gen != prev:
            busy.add(int(s["t"]))
        prev = gen

    during = [ms for ts, ms in latencies if int(ts) in busy]
    outside = [ms for ts, ms in latencies if int(ts) not in busy]
    return {
        "ckptSecondsObserved": len(busy),
        "ckptOpsDuring": len(during),
        "ckptOpsOutside": len(outside),
        "ckptP50During": _pct(during, 0.50),
        "ckptP50Outside": _pct(outside, 0.50),
        "ckptP99During": _pct(during, 0.99),
        "ckptP99Outside": _pct(outside, 0.99),
    }


def _series_elapsed(samples: list[dict], t0: float) -> list[dict]:
    """Rebase absolute sample timestamps to seconds since level start."""
    out = []
    for s in samples:
        row = {
            "t": round(s["t"] - t0, 2),
            "readTotal": s["readTotal"],
            "readOut": s["readOut"],
            "queueLength": s["queueLength"],
        }
        if "ckptRunning" in s:
            row["ckptRunning"] = s["ckptRunning"]
        out.append(row)
    return out


def convergence_verdict(
    samples: list[dict],
    *,
    window_s: float = CONVERGENCE_WINDOW_S,
    tol: float = CONVERGENCE_TOL,
    offered: int | None = None,
) -> dict:
    """Compare mean readTotal in the last window_s vs the window before that.

    Issue #3 plan §4b. Also flags demand-capping (§4d / guard): if the late
    window mean sits within a few tickets of `offered`, CONVERGED may just mean
    "ran out of client threads," not "found the algorithm's ceiling."
    """
    if len(samples) < 4:
        return {
            "verdict": "INSUFFICIENT_SAMPLES",
            "lateMean": None,
            "priorMean": None,
            "relDelta": None,
            "demandCapped": None,
        }
    t_end = samples[-1]["t"]
    late = [s["readTotal"] for s in samples if s["t"] >= t_end - window_s]
    prior = [
        s["readTotal"]
        for s in samples
        if t_end - 2 * window_s <= s["t"] < t_end - window_s
    ]
    if not late or not prior:
        return {
            "verdict": "INSUFFICIENT_SAMPLES",
            "lateMean": None,
            "priorMean": None,
            "relDelta": None,
            "demandCapped": None,
        }
    late_mean = statistics.mean(late)
    prior_mean = statistics.mean(prior)
    base = max(abs(prior_mean), 1.0)
    rel = abs(late_mean - prior_mean) / base
    verdict = "CONVERGED" if rel < tol else "STILL_MOVING"
    demand_capped = None
    if offered is not None and verdict == "CONVERGED":
        # Within a few tickets of offered concurrency → demand-capped signature.
        demand_capped = abs(late_mean - offered) <= max(3.0, 0.05 * offered)
        if demand_capped:
            verdict = "CONVERGED_DEMAND_CAPPED"
    return {
        "verdict": verdict,
        "lateMean": round(late_mean, 2),
        "priorMean": round(prior_mean, 2),
        "relDelta": round(rel, 4),
        "windowSeconds": window_s,
        "tolerance": tol,
        "demandCapped": demand_capped,
    }


def run_level(level: int) -> dict:
    samples: list[dict] = []
    errors: list[str] = []
    stop = threading.Event()
    before, cache_before = tickets(), cache_state()
    before_ckpt_gen = checkpoint()["ckptGeneration"]

    def sampler() -> None:
        # totalTickets is the whole point and it MOVES on 7.0. A reading taken
        # only at the end would miss the algorithm's response entirely.
        while not stop.is_set():
            try:
                row = {"t": time.time(), **tickets(), **checkpoint()}
                # Cheap extra field for T4 checkpoint-I/O attribution.
                row["bytesWrittenFromCache"] = cache_state()["bytesWrittenFromCache"]
                samples.append(row)
            except TRANSIENT_ERRORS as e:
                # Counted, never swallowed. This used to be `except: pass`,
                # which meant a renamed field discarded every sample and the run
                # reported a clean empty series -- indistinguishable from
                # "measured, and there was nothing there". See issue #21.
                #
                # Only genuinely transient connectivity errors are caught
                # here. A KeyError/TypeError from a wrong or renamed field
                # (exactly the mistake issue #21 was filed over) is not
                # caught -- it propagates and crashes the run loudly, which
                # is the correct behavior for a bug rather than a blip.
                errors.append(f"{type(e).__name__}: {e}")
            stop.wait(SAMPLE_S)

    latencies: list[float] = []
    lock = threading.Lock()
    deadline = time.time() + SECONDS

    def worker() -> int:
        ops, local = 0, []
        while time.time() < deadline:
            # Random _id defeats read-ahead and forces the cache miss this
            # experiment depends on.
            i = random.randrange(DOCS)
            t0 = time.perf_counter()
            db.docs.find_one({"_id": i})
            # Wall-clock second alongside the latency, so operations can be
            # grouped into the second they completed in and compared against
            # what the checkpointer was doing during that second.
            local.append((time.time(), (time.perf_counter() - t0) * 1000))
            ops += 1
        with lock:
            latencies.extend(local)
        return ops

    t_start = time.time()
    sam = threading.Thread(target=sampler, daemon=True)
    sam.start()
    with ThreadPoolExecutor(max_workers=level) as pool:
        ops = sum(f.result() for f in [pool.submit(worker) for _ in range(level)])
    stop.set()
    sam.join(timeout=2)

    elapsed = time.time() - t_start
    after, cache_after = tickets(), cache_state()

    if errors and not samples:
        # Every sample failed. That is not a degraded measurement, it is no
        # measurement, and continuing would emit a full table of zeros that
        # reads exactly like a finding.
        raise SystemExit(
            f"SAMPLER FAILED ON EVERY ATTEMPT at concurrency {level} "
            f"({len(errors)} errors, first: {errors[0]}). No ticket or "
            f"checkpoint data was captured, so this run measured nothing."
        )

    after_ckpt_gen = checkpoint()["ckptGeneration"]
    lat_values = [ms for _, ms in latencies]
    mean_ms = statistics.mean(lat_values) if lat_values else 0.0
    p95_ms = (
        statistics.quantiles(lat_values, n=20)[18]
        if len(lat_values) > 20
        else mean_ms
    )
    ckpt = _checkpoint_split(latencies, samples)
    max_tickets = max((s["readTotal"] for s in samples), default=after["readTotal"])
    series = _series_elapsed(samples, t_start)
    conv = convergence_verdict(series, offered=level)

    return {
        "concurrency": level,
        "seconds": round(elapsed, 1),
        "ops": ops,
        "opsPerSecond": round(ops / elapsed, 1),
        "meanLatencyMs": round(mean_ms, 2),
        "p95LatencyMs": round(p95_ms, 2),
        "ticketsStart": before["readTotal"],
        "ticketsEnd": after["readTotal"],
        "ticketsMax": max_tickets,
        "outMax": max((s["readOut"] for s in samples), default=0),
        "queueLengthMax": max((s["queueLength"] for s in samples), default=0),
        "queuedMicrosDelta": after["queuedMicros"] - before["queuedMicros"],
        "pagesReadIntoCache": cache_after["readIntoCache"] - cache_before["readIntoCache"],
        "evictedByAppThreads": cache_after["evictedByApp"] - cache_before["evictedByApp"],
        "bytesWrittenFromCacheDelta": (
            cache_after["bytesWrittenFromCache"] - cache_before["bytesWrittenFromCache"]
        ),
        # Little's law. Once the pool binds this should track opsPerSecond;
        # where the two diverge is where the model is wrong.
        "predictedCeiling": round(max_tickets / (mean_ms / 1000), 1) if mean_ms else None,
        "sampleCount": len(samples),
        "series": series,
        "convergence": conv,
        "samplerErrors": len(errors),
        "samplerFirstError": errors[0] if errors else None,
        "checkpointsObserved": after_ckpt_gen - before_ckpt_gen,
        **ckpt,
    }


def run_timeseries(level: int) -> dict:
    """Issue #12 / T4 — one concurrency, multi-minute soak, 1s latency buckets.

    Prefer PROBE_LEVELS=8 and PROBE_SECONDS>=480 (or 6× measured checkpoint
    interval). Emits perSecond rows + checkpoint-conditioned p99 ratio.
    """
    samples: list[dict] = []
    errors: list[str] = []
    stop = threading.Event()
    before, cache_before = tickets(), cache_state()
    before_ckpt_gen = checkpoint()["ckptGeneration"]

    def sampler() -> None:
        while not stop.is_set():
            try:
                row = {"t": time.time(), **tickets(), **checkpoint()}
                row["bytesWrittenFromCache"] = cache_state()["bytesWrittenFromCache"]
                samples.append(row)
            except TRANSIENT_ERRORS as e:
                errors.append(f"{type(e).__name__}: {e}")
            stop.wait(SAMPLE_S)

    latencies: list[tuple[float, float]] = []
    lock = threading.Lock()
    deadline = time.time() + SECONDS

    def worker() -> int:
        ops, local = 0, []
        while time.time() < deadline:
            i = random.randrange(DOCS)
            t0 = time.perf_counter()
            db.docs.find_one({"_id": i})
            local.append((time.time(), (time.perf_counter() - t0) * 1000))
            ops += 1
        with lock:
            latencies.extend(local)
        return ops

    t_start = time.time()
    sam = threading.Thread(target=sampler, daemon=True)
    sam.start()
    with ThreadPoolExecutor(max_workers=level) as pool:
        ops = sum(f.result() for f in [pool.submit(worker) for _ in range(level)])
    stop.set()
    sam.join(timeout=2)

    elapsed = time.time() - t_start
    after, cache_after = tickets(), cache_state()

    if errors and not samples:
        raise SystemExit(
            f"SAMPLER FAILED ON EVERY ATTEMPT at concurrency {level} "
            f"({len(errors)} errors, first: {errors[0]}). No ticket or "
            f"checkpoint data was captured, so this run measured nothing."
        )
    if errors:
        raise SystemExit(
            f"SAMPLER ERRORS during timeseries soak at c={level}: "
            f"{len(errors)} (first: {errors[0]}). Issue #12 refuses a "
            f"'flat' conclusion when the instrument itself dropped samples."
        )

    after_ckpt_gen = checkpoint()["ckptGeneration"]
    ckpts = after_ckpt_gen - before_ckpt_gen
    lat_values = [ms for _, ms in latencies]
    mean_ms = statistics.mean(lat_values) if lat_values else 0.0
    p95_ms = (
        statistics.quantiles(lat_values, n=20)[18]
        if len(lat_values) > 20
        else mean_ms
    )
    buckets = _per_second_buckets(latencies, samples, t_start)
    ckpt = _checkpoint_split(latencies, samples)
    guards = timeseries_guards(
        buckets, checkpoints_observed=ckpts, sampler_errors=len(errors)
    )

    # Aggregate cross-check vs 003's published c=8 row (same throttle/cache).
    ops_s = ops / elapsed if elapsed else 0.0
    ref_ok = True
    ref_notes: list[str] = []
    if level == 8 and SECONDS >= 60:
        if abs(ops_s - REF_OPS_PER_S) / REF_OPS_PER_S > REF_TOLERANCE:
            ref_ok = False
            ref_notes.append(
                f"ops/s {ops_s:.1f} vs ref {REF_OPS_PER_S} (tol {REF_TOLERANCE})"
            )
        if abs(mean_ms - REF_MEAN_LAT_MS) / REF_MEAN_LAT_MS > REF_TOLERANCE:
            ref_ok = False
            ref_notes.append(
                f"meanLatencyMs {mean_ms:.1f} vs ref {REF_MEAN_LAT_MS} "
                f"(tol {REF_TOLERANCE})"
            )
    if not ref_ok:
        guards["ok"] = False
        guards["refuseToConclude"] = True
        guards["flags"] = list(guards["flags"]) + [
            "aggregate_cross_check_failed: " + "; ".join(ref_notes)
        ]

    ratio = None
    if ckpt["ckptP99Outside"] and ckpt["ckptP99Outside"] > 0:
        ratio = round(ckpt["ckptP99During"] / ckpt["ckptP99Outside"], 3)

    if guards["refuseToConclude"]:
        print(
            "\nREFUSING TO CONCLUDE (issue #12 / T4 guards):\n  - "
            + "\n  - ".join(guards["flags"]),
            file=sys.stderr,
        )

    return {
        "mode": "timeseries",
        "concurrency": level,
        "seconds": round(elapsed, 1),
        "ops": ops,
        "opsPerSecond": round(ops_s, 1),
        "meanLatencyMs": round(mean_ms, 2),
        "p95LatencyMs": round(p95_ms, 2),
        "ticketsStart": before["readTotal"],
        "ticketsEnd": after["readTotal"],
        "ticketsMax": max((s["readTotal"] for s in samples), default=after["readTotal"]),
        "outMax": max((s["readOut"] for s in samples), default=0),
        "queuedMicrosDelta": after["queuedMicros"] - before["queuedMicros"],
        "pagesReadIntoCache": cache_after["readIntoCache"] - cache_before["readIntoCache"],
        "bytesWrittenFromCacheDelta": (
            cache_after["bytesWrittenFromCache"] - cache_before["bytesWrittenFromCache"]
        ),
        "sampleCount": len(samples),
        "samplerErrors": len(errors),
        "checkpointsObserved": ckpts,
        "perSecond": buckets,
        "ckptP99RatioDuringOverOutside": ratio,
        "guards": guards,
        "refCrossCheck": {
            "ok": ref_ok,
            "refOpsPerSecond": REF_OPS_PER_S,
            "refMeanLatencyMs": REF_MEAN_LAT_MS,
            "notes": ref_notes,
        },
        **ckpt,
    }


def run_cooldown() -> dict:
    """Issue #3 §4c — sample after load stops on a dedicated single-connection client.

    Closes the load-generating pool first so idle pooled-socket heartbeats cannot
    masquerade as "zero load." Optional trickle heartbeat (PROBE_COOLDOWN_HEARTBEAT_HZ)
    gives the controller a throughput signal when nonzero.
    """
    global client, db, admin
    client.close()
    cool = MongoClient(
        URI,
        maxPoolSize=1,
        minPoolSize=0,
        maxIdleTimeMS=60_000,
        serverSelectionTimeoutMS=30000,
    )
    cool_db = cool.ticketprobe
    cool_admin = cool.admin

    def cool_tickets() -> dict:
        from mongo_tickets import execution_tickets

        s = cool_admin.command("serverStatus")
        t = execution_tickets(s)
        g = s["globalLock"]
        return {
            "readTotal": t["readTotal"],
            "readOut": t["readOut"],
            "queueLength": t["queueLength"],
            "currentQueueReaders": g["currentQueue"]["readers"],
            "activeReaders": g["activeClients"]["readers"],
        }

    samples: list[dict] = []
    errors: list[str] = []
    stop = threading.Event()
    t0 = time.time()
    floor_since: float | None = None
    heartbeat_ops = 0

    def sampler() -> None:
        nonlocal floor_since
        while not stop.is_set():
            try:
                row = cool_tickets()
                now = time.time()
                samples.append(
                    {
                        "t": round(now - t0, 2),
                        "sinceLoadStoppedSeconds": round(now - t0, 2),
                        **row,
                    }
                )
                if row["readTotal"] <= TICKET_FLOOR:
                    if floor_since is None:
                        floor_since = now
                    elif now - floor_since >= FLOOR_HOLD_S:
                        stop.set()
                        return
                else:
                    floor_since = None
            except TRANSIENT_ERRORS as e:
                errors.append(f"{type(e).__name__}: {e}")
            stop.wait(SAMPLE_S)

    def heartbeat() -> None:
        nonlocal heartbeat_ops
        if COOLDOWN_HEARTBEAT_HZ <= 0:
            return
        interval = 1.0 / COOLDOWN_HEARTBEAT_HZ
        while not stop.is_set():
            try:
                i = random.randrange(DOCS)
                cool_db.docs.find_one({"_id": i})
                heartbeat_ops += 1
            except TRANSIENT_ERRORS as e:
                errors.append(f"heartbeat {type(e).__name__}: {e}")
            stop.wait(interval)

    sam = threading.Thread(target=sampler, daemon=True)
    hb = threading.Thread(target=heartbeat, daemon=True)
    sam.start()
    hb.start()
    # Wall-clock cap; sampler may stop early on floor hold.
    deadline = t0 + COOLDOWN_SECONDS
    while not stop.is_set() and time.time() < deadline:
        time.sleep(0.25)
    stop.set()
    sam.join(timeout=2)
    hb.join(timeout=2)

    reached_floor = False
    time_to_floor = None
    if samples:
        end_total = samples[-1]["readTotal"]
        if end_total <= TICKET_FLOOR and floor_since is not None:
            reached_floor = True
            time_to_floor = round(floor_since - t0 + FLOOR_HOLD_S, 1)

    # Re-bind module globals so any post-cooldown cache_state() still works.
    client, db, admin = cool, cool_db, cool_admin

    nonzero_out = any(s["readOut"] > 0 for s in samples)
    return {
        "seconds": round(time.time() - t0, 1),
        "configuredSeconds": COOLDOWN_SECONDS,
        "heartbeatHz": COOLDOWN_HEARTBEAT_HZ,
        "heartbeatOps": heartbeat_ops,
        "ticketsStart": samples[0]["readTotal"] if samples else None,
        "ticketsEnd": samples[-1]["readTotal"] if samples else None,
        "reachedFloor": reached_floor,
        "timeToFloorSeconds": time_to_floor,
        "samplerReadOutEverNonzero": nonzero_out,
        "sampleCount": len(samples),
        "series": samples,
        "samplerErrors": len(errors),
        "samplerFirstError": errors[0] if errors else None,
        "note": (
            "sampler readOut was nonzero during HZ=0 cooldown — zero-load "
            "condition was not met"
            if (COOLDOWN_HEARTBEAT_HZ == 0 and nonzero_out)
            else None
        ),
    }


def main() -> int:
    version = admin.command("buildInfo")["version"]
    load()
    print(f"probing MongoDB {version} mode={PROBE_MODE}", file=sys.stderr)

    if PROBE_MODE == "timeseries":
        # Issue #12: single concurrency soak (default PROBE_LEVELS=8).
        if len(LEVELS) != 1:
            print(
                f"note: timeseries mode uses only the first PROBE_LEVELS entry "
                f"({LEVELS[0]}); ignoring {LEVELS[1:]}",
                file=sys.stderr,
            )
        level = LEVELS[0]
        if SECONDS < 480:
            print(
                f"WARNING: PROBE_SECONDS={SECONDS} is below the issue #12 floor "
                f"of 480s (8 min / ~6 checkpoint cycles). Raise it for a "
                f"conclusive run; continuing for smoke only.",
                file=sys.stderr,
            )
        result = run_timeseries(level)
        print(
            f"  c={result['concurrency']:>3}  {result['opsPerSecond']:>8} ops/s  "
            f"lat {result['meanLatencyMs']:>8} ms  "
            f"ckpts {result['checkpointsObserved']}  "
            f"p99 ratio {result.get('ckptP99RatioDuringOverOutside')}  "
            f"guards_ok={result['guards']['ok']}",
            file=sys.stderr,
        )
        st = db.command("collstats", "docs")
        print("===JSON===")
        print(
            json.dumps(
                {
                    "mode": "timeseries",
                    "version": version,
                    "cache": cache_state(),
                    "seconds": SECONDS,
                    "docs": DOCS,
                    "storageSizeBytes": st["storageSize"],
                    "dataSizeBytes": st["size"],
                    "cacheOversubscription": round(
                        st["size"] / cache_state()["maxCache"], 2
                    ),
                    "totalPagesReadIntoCache": result["pagesReadIntoCache"],
                    "result": result,
                    "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                },
                indent=1,
                default=str,
            )
        )
        return 0 if result["guards"]["ok"] else 2

    results = []
    for level in LEVELS:
        r = run_level(level)
        results.append(r)
        conv = r["convergence"]
        print(
            f"  c={r['concurrency']:>3}  {r['opsPerSecond']:>8} ops/s  "
            f"lat {r['meanLatencyMs']:>8} ms  "
            f"tickets {r['ticketsStart']}->{r['ticketsEnd']} (max {r['ticketsMax']})  "
            f"out {r['outMax']}  queued {r['queuedMicrosDelta'] / 1e6:.2f}s  "
            f"{conv['verdict']}"
            + (
                f" (Δ={conv['relDelta']:.1%})"
                if conv.get("relDelta") is not None
                else ""
            ),
            file=sys.stderr,
        )
    cooldown = None
    if COOLDOWN_SECONDS > 0:
        print(
            f"cooldown {COOLDOWN_SECONDS:.0f}s  "
            f"heartbeatHz={COOLDOWN_HEARTBEAT_HZ} ...",
            file=sys.stderr,
        )
        cooldown = run_cooldown()
        print(
            f"  cooldown tickets {cooldown['ticketsStart']}->"
            f"{cooldown['ticketsEnd']}  "
            f"reachedFloor={cooldown['reachedFloor']}  "
            f"readOutEverNonzero={cooldown['samplerReadOutEverNonzero']}",
            file=sys.stderr,
        )
    st = db.command("collstats", "docs")
    read_pages = sum(r["pagesReadIntoCache"] for r in results)
    if read_pages == 0:
        # The second smoke run of this harness produced a full, clean table
        # while pagesReadIntoCache stayed at zero: the host page cache served
        # everything, so the throttled device was never involved and the
        # numbers described a healthy database. Say so loudly rather than let
        # a plausible table stand in for a result.
        print(
            "\nWARNING: pagesReadIntoCache was ZERO for every level. No read "
            "reached the device, so the throttle did nothing and these numbers "
            "say nothing about slow storage. Lower PROBE_MEMORY (which bounds "
            "the container's page cache) or raise PROBE_DOCS.",
            file=sys.stderr,
        )
    print("===JSON===")
    print(
        json.dumps(
            {
                "mode": "levels",
                "version": version,
                "cache": cache_state(),
                "secondsPerLevel": SECONDS,
                "docs": DOCS,
                "storageSizeBytes": st["storageSize"],
                "dataSizeBytes": st["size"],
                "cacheOversubscription": round(st["size"] / cache_state()["maxCache"], 2),
                # Zero here means no read reached the device and the run proved
                # nothing, whatever the rest of the table says.
                "totalPagesReadIntoCache": sum(
                    r["pagesReadIntoCache"] for r in results
                ),
                "results": results,
                "cooldown": cooldown,
                "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            },
            indent=1,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
