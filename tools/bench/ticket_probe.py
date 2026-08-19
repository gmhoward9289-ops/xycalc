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
SAMPLE_S = 0.5

client = MongoClient(URI, maxPoolSize=max(LEVELS) + 8, serverSelectionTimeoutMS=30000)
db = client.ticketprobe
admin = client.admin


def tickets() -> dict:
    s = admin.command("serverStatus")
    c = s["wiredTiger"]["concurrentTransactions"]
    g = s["globalLock"]
    return {
        "readTotal": c["read"]["totalTickets"],
        "readOut": c["read"]["out"],
        "writeTotal": c["write"]["totalTickets"],
        "queueLength": int(c["read"].get("queueLength", 0)),
        "queuedMicros": int(c["read"].get("totalTimeQueuedMicros", 0)),
        "currentQueueReaders": g["currentQueue"]["readers"],
        "activeReaders": g["activeClients"]["readers"],
    }


# Verified present on MongoDB 7.0.39 by reading a live serverStatus, not by
# reading documentation. The corpus has already been wrong once about where a
# field lives (queues.execution), and issue #21 exists because a wrong name
# here used to vanish silently.
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
                samples.append({"t": time.time(), **tickets(), **checkpoint()})
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
        # Little's law. Once the pool binds this should track opsPerSecond;
        # where the two diverge is where the model is wrong.
        "predictedCeiling": round(max_tickets / (mean_ms / 1000), 1) if mean_ms else None,
        "samples": len(samples),
        "samplerErrors": len(errors),
        "samplerFirstError": errors[0] if errors else None,
        "checkpointsObserved": after_ckpt_gen - before_ckpt_gen,
        **ckpt,
    }


def main() -> int:
    version = admin.command("buildInfo")["version"]
    load()
    print(f"probing MongoDB {version}", file=sys.stderr)
    results = []
    for level in LEVELS:
        r = run_level(level)
        results.append(r)
        print(
            f"  c={r['concurrency']:>3}  {r['opsPerSecond']:>8} ops/s  "
            f"lat {r['meanLatencyMs']:>8} ms  "
            f"tickets {r['ticketsStart']}->{r['ticketsEnd']} (max {r['ticketsMax']})  "
            f"out {r['outMax']}  queued {r['queuedMicrosDelta'] / 1e6:.2f}s",
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
                "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            },
            indent=1,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
