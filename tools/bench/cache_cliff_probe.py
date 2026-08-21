"""T1 / issue #9 — does WiredTiger read throughput fall off a cliff past cache?

Forked from ticket_probe.py. Differences from the parent:

- One oversubscription ratio per process (PROBE_TARGET_RATIO), not a
  concurrency ladder.
- Concurrency fixed at 1 (or PROBE_CONCURRENCY) so ticket-pool queueing
  cannot masquerade as a cache knee.
- Dataset sized from a measured pilot batch against live maxCache, then
  checked within PROBE_RATIO_TOLERANCE of the target.
- Modes: --load (size+insert), --probe (timed lookups), --all (both).
  The shell samples cgroup device bytes around --probe only.

Uniform random point lookups. Absolute ops/s are throttle artifacts;
only the curve shape across ratios is the finding.
"""

from __future__ import annotations

import argparse
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

TRANSIENT_ERRORS = (
    pymongo_errors.AutoReconnect,
    pymongo_errors.NetworkTimeout,
    pymongo_errors.ConnectionFailure,
    pymongo_errors.ServerSelectionTimeoutError,
)

URI = os.environ.get("PROBE_URI", "mongodb://127.0.0.1:27017")
SECONDS = float(os.environ.get("PROBE_SECONDS", "25"))
CONCURRENCY = int(os.environ.get("PROBE_CONCURRENCY", "1"))
TARGET_RATIO = float(os.environ.get("PROBE_TARGET_RATIO", "2.0"))
RATIO_TOLERANCE = float(os.environ.get("PROBE_RATIO_TOLERANCE", "0.10"))
PILOT_DOCS = int(os.environ.get("PROBE_PILOT_DOCS", "2000"))
# Soft ceiling so a mis-set cache cannot ask for tens of millions of docs.
# 100× of a 0.25 GB cache is ~37M docs at this schema; allow headroom.
MAX_DOCS = int(os.environ.get("PROBE_MAX_DOCS", "50000000"))
SAMPLE_S = 0.5
# Queued-ticket budget at concurrency 1 should stay near zero. Anything
# larger than a second of queue time across a 25s window means we are
# measuring ticket contention, not cache shape.
MAX_QUEUED_MICROS = int(os.environ.get("PROBE_MAX_QUEUED_MICROS", "1_000_000"))

client = MongoClient(URI, maxPoolSize=CONCURRENCY + 8, serverSelectionTimeoutMS=30000)
db = client.cachecliff
admin = client.admin

CKPT_RUNNING = "transaction checkpoint currently running"
CKPT_GENERATION = "transaction checkpoint generation"
CKPT_RECENT_MS = "transaction checkpoint most recent time (msecs)"


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


def _doc(i: int) -> dict:
    alphabet = string.ascii_lowercase + string.digits
    return {
        "_id": i,
        "pad": "".join(random.choices(alphabet, k=700)),
        "k": "".join(random.choices(alphabet, k=24)),
    }


def _insert_range(start: int, count: int) -> None:
    batch: list[dict] = []
    n = 0
    for i in range(start, start + count):
        batch.append(_doc(i))
        if len(batch) == 2000:
            db.docs.insert_many(batch, ordered=False)
            batch, n = [], n + 2000
            if n % 100_000 == 0:
                print(f"  inserted {start + n:,}...", file=sys.stderr)
    if batch:
        db.docs.insert_many(batch, ordered=False)


def load() -> dict:
    """Pilot-measure bytes/doc, then load to TARGET_RATIO of live maxCache."""
    print(
        f"sizing for {TARGET_RATIO}x oversubscription "
        f"(tolerance ±{RATIO_TOLERANCE * 100:.0f}%)...",
        file=sys.stderr,
    )
    db.docs.drop()
    _insert_range(0, PILOT_DOCS)
    st = db.command("collstats", "docs")
    max_cache = cache_state()["maxCache"]
    if max_cache <= 0:
        raise SystemExit("REFUSING: maxCache reported as 0")
    bytes_per_doc = st["size"] / PILOT_DOCS
    target_bytes = TARGET_RATIO * max_cache
    target_docs = int(target_bytes / bytes_per_doc)
    if target_docs < PILOT_DOCS:
        # Already oversized pilot relative to a tiny target — rare with
        # 0.25 GB cache, but refuse rather than undershoot silently.
        raise SystemExit(
            f"REFUSING: pilot alone is {st['size'] / max_cache:.2f}x cache; "
            f"target {TARGET_RATIO}x needs fewer than {PILOT_DOCS} docs. "
            f"Lower PROBE_PILOT_DOCS or raise cache."
        )
    if target_docs > MAX_DOCS:
        raise SystemExit(
            f"REFUSING: target {target_docs:,} docs exceeds PROBE_MAX_DOCS="
            f"{MAX_DOCS:,}. Raise PROBE_MAX_DOCS deliberately if intended."
        )
    remaining = target_docs - PILOT_DOCS
    print(
        f"  pilot {PILOT_DOCS:,} docs -> {bytes_per_doc:.0f} B/doc; "
        f"maxCache={max_cache / 1e6:.0f} MB; loading to {target_docs:,} docs "
        f"({remaining:,} more)...",
        file=sys.stderr,
    )
    if remaining > 0:
        _insert_range(PILOT_DOCS, remaining)

    st = db.command("collstats", "docs")
    oversub = st["size"] / max_cache
    print(
        f"  loaded. dataSize={st['size'] / 1e6:.0f} MB  "
        f"storageSize={st['storageSize'] / 1e6:.0f} MB  "
        f"cache={max_cache / 1e6:.0f} MB  oversubscription={oversub:.2f}x "
        f"(target {TARGET_RATIO}x)",
        file=sys.stderr,
    )
    lo, hi = TARGET_RATIO * (1 - RATIO_TOLERANCE), TARGET_RATIO * (1 + RATIO_TOLERANCE)
    if not (lo <= oversub <= hi):
        raise SystemExit(
            f"REFUSING TO RUN: landed at {oversub:.2f}x cache, outside "
            f"tolerance [{lo:.2f}, {hi:.2f}] for target {TARGET_RATIO}x. "
            f"Adjust sizing or PROBE_RATIO_TOLERANCE."
        )
    return {
        "docs": st["count"],
        "dataSizeBytes": st["size"],
        "storageSizeBytes": st["storageSize"],
        "bytesPerDoc": round(bytes_per_doc, 1),
        "maxCacheBytes": max_cache,
        "cacheOversubscription": round(oversub, 3),
        "targetRatio": TARGET_RATIO,
    }


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return round(s[min(int(q * len(s)), len(s) - 1)], 2)


def run_probe(docs: int) -> dict:
    samples: list[dict] = []
    errors: list[str] = []
    stop = threading.Event()
    before, cache_before = tickets(), cache_state()
    before_ckpt_gen = checkpoint()["ckptGeneration"]

    def sampler() -> None:
        while not stop.is_set():
            try:
                samples.append({"t": time.time(), **tickets(), **checkpoint()})
            except TRANSIENT_ERRORS as e:
                errors.append(f"{type(e).__name__}: {e}")
            stop.wait(SAMPLE_S)

    latencies: list[tuple[float, float]] = []
    lock = threading.Lock()
    deadline = time.time() + SECONDS

    def worker() -> int:
        ops, local = 0, []
        while time.time() < deadline:
            i = random.randrange(docs)
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
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        ops = sum(
            f.result() for f in [pool.submit(worker) for _ in range(CONCURRENCY)]
        )
    stop.set()
    sam.join(timeout=2)

    elapsed = time.time() - t_start
    after, cache_after = tickets(), cache_state()

    if errors and not samples:
        raise SystemExit(
            f"SAMPLER FAILED ON EVERY ATTEMPT ({len(errors)} errors, "
            f"first: {errors[0]}). No ticket data captured."
        )

    lat_values = [ms for _, ms in latencies]
    mean_ms = statistics.mean(lat_values) if lat_values else 0.0
    p95_ms = (
        statistics.quantiles(lat_values, n=20)[18]
        if len(lat_values) > 20
        else mean_ms
    )
    queued_delta = after["queuedMicros"] - before["queuedMicros"]
    pages_delta = cache_after["readIntoCache"] - cache_before["readIntoCache"]

    if queued_delta > MAX_QUEUED_MICROS:
        raise SystemExit(
            f"REFUSING RESULT: queuedMicrosDelta={queued_delta} exceeds "
            f"PROBE_MAX_QUEUED_MICROS={MAX_QUEUED_MICROS}. Ticket queueing "
            f"is contaminating the cache-cliff measurement. Lower "
            f"PROBE_CONCURRENCY (default 1) or investigate."
        )

    if pages_delta == 0 and TARGET_RATIO > 1.0:
        print(
            "\nWARNING: pagesReadIntoCache delta was ZERO above 1.0x "
            "oversubscription. No read reached WT from disk (or the counter "
            "did not move). Device-byte guard in the shell must also fail "
            "this leg — do not trust the throughput numbers.",
            file=sys.stderr,
        )

    return {
        "concurrency": CONCURRENCY,
        "seconds": round(elapsed, 1),
        "ops": ops,
        "opsPerSecond": round(ops / elapsed, 1) if elapsed else 0.0,
        "meanLatencyMs": round(mean_ms, 2),
        "p95LatencyMs": round(p95_ms, 2),
        "p50LatencyMs": _pct(lat_values, 0.50),
        "ticketsStart": before["readTotal"],
        "ticketsEnd": after["readTotal"],
        "outMax": max((s["readOut"] for s in samples), default=0),
        "queueLengthMax": max((s["queueLength"] for s in samples), default=0),
        "queuedMicrosDelta": queued_delta,
        "pagesReadIntoCache": pages_delta,
        "pagesReadIntoCachePerOp": round(pages_delta / ops, 4) if ops else None,
        "evictedByAppThreads": cache_after["evictedByApp"] - cache_before["evictedByApp"],
        "samples": len(samples),
        "samplerErrors": len(errors),
        "samplerFirstError": errors[0] if errors else None,
        "checkpointsObserved": checkpoint()["ckptGeneration"] - before_ckpt_gen,
    }


def _existing_docs() -> int:
    try:
        return int(db.command("collstats", "docs")["count"])
    except Exception:
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("load", "probe", "all"),
        default=os.environ.get("PROBE_MODE", "all"),
        help="load=size+insert; probe=timed lookups; all=both (default)",
    )
    args = parser.parse_args()

    version = admin.command("buildInfo")["version"]
    sizing: dict | None = None
    result: dict | None = None

    if args.mode in ("load", "all"):
        sizing = load()
        docs = sizing["docs"]
    else:
        docs = _existing_docs()
        if docs <= 0:
            raise SystemExit("REFUSING: --mode probe with empty collection; run --mode load first")
        st = db.command("collstats", "docs")
        max_cache = cache_state()["maxCache"]
        sizing = {
            "docs": docs,
            "dataSizeBytes": st["size"],
            "storageSizeBytes": st["storageSize"],
            "maxCacheBytes": max_cache,
            "cacheOversubscription": round(st["size"] / max_cache, 3),
            "targetRatio": TARGET_RATIO,
        }

    if args.mode in ("probe", "all"):
        print(
            f"probing MongoDB {version} at {sizing['cacheOversubscription']}x "
            f"(concurrency={CONCURRENCY}, {SECONDS}s)...",
            file=sys.stderr,
        )
        result = run_probe(docs)
        print(
            f"  {result['opsPerSecond']:>8} ops/s  "
            f"lat {result['meanLatencyMs']:>8} ms  "
            f"pages/op {result['pagesReadIntoCachePerOp']}  "
            f"queued {result['queuedMicrosDelta'] / 1e6:.3f}s",
            file=sys.stderr,
        )

    print("===JSON===")
    print(
        json.dumps(
            {
                "version": version,
                "mode": args.mode,
                "targetRatio": TARGET_RATIO,
                "concurrency": CONCURRENCY,
                "seconds": SECONDS,
                "sizing": sizing,
                "result": result,
                "cache": cache_state(),
                "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            },
            indent=1,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
