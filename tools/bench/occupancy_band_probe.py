"""007 — occupancy band: eviction_target 80% vs 90% under the same oversub.

Same skeleton as cache_cliff_probe (pilot-sized load, concurrency=1 by
default, ticket-queue refusal). Differences:

- Before the timed probe, applies
  wiredTigerEngineRuntimeConfig with eviction_target=N (PROBE_EVICTION_TARGET).
- Sampler records cache occupancy % and dirty % every 0.5s.
- End snapshot includes tcmalloc heap vs allocated (fragmentation gap).
- Reports mean/p50 occupancy during the window so the shell can refuse a
  leg that never sat near its configured target.

Absolute ops/s are throttle artifacts; the finding is the *delta* between
the 80 and 90 legs on the same host/throttle/ratio.
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
MAX_DOCS = int(os.environ.get("PROBE_MAX_DOCS", "50000000"))
EVICTION_TARGET = int(os.environ.get("PROBE_EVICTION_TARGET", "80"))
EVICTION_TRIGGER = int(os.environ.get("PROBE_EVICTION_TRIGGER", "95"))
# Occupancy should land near the configured target under sustained pressure.
# Allow ±8 points so a 80-target window at 73–88% still counts; refuse wider.
OCC_BAND = float(os.environ.get("PROBE_OCC_BAND", "8"))
SAMPLE_S = 0.5
MAX_QUEUED_MICROS = int(os.environ.get("PROBE_MAX_QUEUED_MICROS", "1_000_000"))

client = MongoClient(URI, maxPoolSize=CONCURRENCY + 8, serverSelectionTimeoutMS=30000)
db = client.occupancyband
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
    max_c = c["maximum bytes configured"] or 1
    in_c = c["bytes currently in the cache"]
    dirty = c["tracked dirty bytes in the cache"]
    return {
        "inCache": in_c,
        "maxCache": max_c,
        "dirtyBytes": dirty,
        "occupancyPct": round(100.0 * in_c / max_c, 2),
        "dirtyPct": round(100.0 * dirty / max_c, 2),
        "readIntoCache": c["pages read into cache"],
        "evictedByApp": c["pages evicted by application threads"],
        "evictedByWorkers": c.get("eviction worker thread evicting pages", 0),
        "unableToReachGoal": c.get("eviction server unable to reach eviction goal", 0),
    }


def tcmalloc_state() -> dict:
    s = admin.command("serverStatus")
    t = s.get("tcmalloc") or {}
    g = t.get("generic") or {}
    heap = g.get("heap_size")
    allocated = g.get("current_allocated_bytes") or g.get("total_allocated_bytes")
    out = {
        "heapSize": heap,
        "allocatedBytes": allocated,
    }
    if heap is not None and allocated is not None:
        out["fragmentationBytes"] = heap - allocated
        out["fragmentationPctOfHeap"] = round(
            100.0 * (heap - allocated) / heap, 2
        ) if heap else None
    return out


def apply_eviction_target(target: int, trigger: int) -> str:
    if not (1 <= target < trigger <= 100):
        raise SystemExit(
            f"REFUSING: need 1 <= eviction_target ({target}) < "
            f"eviction_trigger ({trigger}) <= 100"
        )
    cfg = f"eviction_target={target},eviction_trigger={trigger}"
    admin.command({"setParameter": 1, "wiredTigerEngineRuntimeConfig": cfg})
    return cfg


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
        raise SystemExit(
            f"REFUSING: pilot alone is {st['size'] / max_cache:.2f}x cache; "
            f"target {TARGET_RATIO}x needs fewer than {PILOT_DOCS} docs."
        )
    if target_docs > MAX_DOCS:
        raise SystemExit(
            f"REFUSING: target {target_docs:,} docs exceeds PROBE_MAX_DOCS="
            f"{MAX_DOCS:,}."
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
    lo, hi = TARGET_RATIO * (1 - RATIO_TOLERANCE), TARGET_RATIO * (1 + RATIO_TOLERANCE)
    if not (lo <= oversub <= hi):
        raise SystemExit(
            f"REFUSING TO RUN: landed at {oversub:.2f}x cache, outside "
            f"tolerance [{lo:.2f}, {hi:.2f}] for target {TARGET_RATIO}x."
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
    tcmalloc_before = tcmalloc_state()

    def sampler() -> None:
        while not stop.is_set():
            try:
                samples.append(
                    {"t": time.time(), **tickets(), **checkpoint(), **cache_state()}
                )
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
    tcmalloc_after = tcmalloc_state()

    if errors and not samples:
        raise SystemExit(
            f"SAMPLER FAILED ON EVERY ATTEMPT ({len(errors)} errors, "
            f"first: {errors[0]})."
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
    occ = [s["occupancyPct"] for s in samples if "occupancyPct" in s]
    dirty = [s["dirtyPct"] for s in samples if "dirtyPct" in s]
    mean_occ = round(statistics.mean(occ), 2) if occ else None
    mean_dirty = round(statistics.mean(dirty), 2) if dirty else None

    if queued_delta > MAX_QUEUED_MICROS:
        raise SystemExit(
            f"REFUSING RESULT: queuedMicrosDelta={queued_delta} exceeds "
            f"PROBE_MAX_QUEUED_MICROS={MAX_QUEUED_MICROS}."
        )

    band_lo, band_hi = EVICTION_TARGET - OCC_BAND, EVICTION_TARGET + OCC_BAND
    occupancy_in_band = (
        mean_occ is not None and band_lo <= mean_occ <= band_hi
    )

    return {
        "concurrency": CONCURRENCY,
        "seconds": round(elapsed, 1),
        "ops": ops,
        "opsPerSecond": round(ops / elapsed, 1) if elapsed else 0.0,
        "meanLatencyMs": round(mean_ms, 2),
        "p95LatencyMs": round(p95_ms, 2),
        "p50LatencyMs": _pct(lat_values, 0.50),
        "evictionTargetConfigured": EVICTION_TARGET,
        "evictionTriggerConfigured": EVICTION_TRIGGER,
        "occupancyPctMean": mean_occ,
        "occupancyPctP50": _pct(occ, 0.50) if occ else None,
        "occupancyPctMin": round(min(occ), 2) if occ else None,
        "occupancyPctMax": round(max(occ), 2) if occ else None,
        "dirtyPctMean": mean_dirty,
        "occupancyInConfiguredBand": occupancy_in_band,
        "occupancyBand": [band_lo, band_hi],
        "ticketsStart": before["readTotal"],
        "ticketsEnd": after["readTotal"],
        "outMax": max((s["readOut"] for s in samples), default=0),
        "queueLengthMax": max((s["queueLength"] for s in samples), default=0),
        "queuedMicrosDelta": queued_delta,
        "pagesReadIntoCache": pages_delta,
        "pagesReadIntoCachePerOp": round(pages_delta / ops, 4) if ops else None,
        "evictedByAppThreads": cache_after["evictedByApp"] - cache_before["evictedByApp"],
        "evictedByWorkersDelta": cache_after["evictedByWorkers"]
        - cache_before["evictedByWorkers"],
        "unableToReachGoalDelta": cache_after["unableToReachGoal"]
        - cache_before["unableToReachGoal"],
        "cacheEnd": cache_after,
        "tcmallocBefore": tcmalloc_before,
        "tcmallocAfter": tcmalloc_after,
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
        choices=("load", "probe", "all", "configure"),
        default=os.environ.get("PROBE_MODE", "all"),
    )
    args = parser.parse_args()

    version = admin.command("buildInfo")["version"]
    sizing: dict | None = None
    result: dict | None = None
    wt_config: str | None = None

    if args.mode in ("configure", "probe", "all"):
        wt_config = apply_eviction_target(EVICTION_TARGET, EVICTION_TRIGGER)
        print(f"applied wiredTigerEngineRuntimeConfig={wt_config}", file=sys.stderr)
        # Let workers settle briefly after the knob change.
        time.sleep(2)

    if args.mode in ("load", "all"):
        sizing = load()
        docs = sizing["docs"]
    else:
        docs = _existing_docs()
        if docs <= 0 and args.mode == "probe":
            raise SystemExit("REFUSING: --mode probe but collection is empty")

    if args.mode in ("probe", "all"):
        print(
            f"probing {SECONDS}s at concurrency={CONCURRENCY}, "
            f"eviction_target={EVICTION_TARGET}%...",
            file=sys.stderr,
        )
        result = run_probe(docs)
        print(
            f"  {result['opsPerSecond']} ops/s  mean={result['meanLatencyMs']}ms  "
            f"occ_mean={result['occupancyPctMean']}%  "
            f"dirty_mean={result['dirtyPctMean']}%  "
            f"in_band={result['occupancyInConfiguredBand']}",
            file=sys.stderr,
        )

    print("===JSON===")
    print(
        json.dumps(
            {
                "version": version,
                "wtConfig": wt_config,
                "evictionTarget": EVICTION_TARGET,
                "sizing": sizing,
                "result": result,
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
