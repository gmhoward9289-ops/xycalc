"""Issue #11 / T3 — sustained write rate vs eviction_dirty_trigger.

Sweeps target write rate as multiples of configured device write throughput.
Samples dirty% / overall occupancy / app-thread eviction every ~1–2s.

Guards:
  - achieved rate within ~10% of target (else flag level)
  - insert arm caps total inserted bytes ≤ 50% of cache
  - refuse levels where dirty% never moves (vacuous)
"""

from __future__ import annotations

import json
import os
import random
import string
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from pymongo import MongoClient

URI = os.environ.get("PROBE_URI", "mongodb://127.0.0.1:27017")
ARM = os.environ.get("PROBE_ARM", "insert").strip().lower()  # insert|update
SECONDS = float(os.environ.get("PROBE_SECONDS", "180"))
RATES = [float(x) for x in os.environ.get("PROBE_RATES", "0.25,0.5,1,2,4,8").split(",")]
WRITE_BPS = int(os.environ.get("PROBE_WRITE_BPS", "4194304"))
CACHE_GB = float(os.environ.get("PROBE_CACHE_GB", "0.25"))
WORKERS = int(os.environ.get("PROBE_WORKERS", "4"))
DOC_BYTES = int(os.environ.get("PROBE_DOC_BYTES", "1024"))
SAMPLE_S = float(os.environ.get("PROBE_SAMPLE_S", "1.5"))
# Cap inserted bytes per level to ≤50% of WT cache (insert-arm guard).
MAX_INSERT_FRAC = float(os.environ.get("PROBE_MAX_INSERT_CACHE_FRAC", "0.5"))

client = MongoClient(URI, maxPoolSize=WORKERS + 8, serverSelectionTimeoutMS=30000)
db = client.evictionprobe
admin = client.admin


def cache_metrics() -> dict:
    c = admin.command("serverStatus")["wiredTiger"]["cache"]
    max_b = c["maximum bytes configured"]
    dirty = c["tracked dirty bytes in the cache"]
    cur = c["bytes currently in the cache"]
    return {
        "maxCache": max_b,
        "dirtyBytes": dirty,
        "dirtyPct": round(100.0 * dirty / max_b, 2) if max_b else 0.0,
        "inCache": cur,
        "occupancyPct": round(100.0 * cur / max_b, 2) if max_b else 0.0,
        "evictedByApp": c["pages evicted by application threads"],
        "evictionUnable": c.get("eviction server unable to reach eviction goal", 0),
        "bytesWrittenFromCache": c["bytes written from cache"],
    }


def _pad() -> str:
    alphabet = string.ascii_lowercase + string.digits
    n = max(DOC_BYTES - 64, 16)
    return "".join(random.choices(alphabet, k=n))


def prep_update_arm(n_docs: int) -> None:
    print(f"preloading {n_docs:,} docs for update arm...", file=sys.stderr)
    db.docs.drop()
    batch = []
    for i in range(n_docs):
        batch.append({"_id": i, "pad": _pad(), "n": 0})
        if len(batch) == 1000:
            db.docs.insert_many(batch, ordered=False)
            batch = []
    if batch:
        db.docs.insert_many(batch, ordered=False)


def run_level(rate_mult: float) -> dict:
    """rate_mult is a multiple of WRITE_BPS converted to approx docs/s."""
    # Rough doc size on wire ≈ DOC_BYTES; target docs/s = (WRITE_BPS * mult) / DOC_BYTES
    target_docs_s = max((WRITE_BPS * rate_mult) / max(DOC_BYTES, 1), 0.1)
    max_docs = int((CACHE_GB * 1e9 * MAX_INSERT_FRAC) / max(DOC_BYTES, 1))
    if ARM == "insert":
        db.docs.drop()
        next_id = 0
    else:
        # Working set sized under cache so onset is dirty-trigger, not fill.
        n = min(max_docs, 50000)
        if db.docs.estimated_document_count() != n:
            prep_update_arm(n)
        next_id = 0

    samples: list[dict] = []
    stop = threading.Event()
    before = cache_metrics()
    ops = 0
    lock = threading.Lock()
    t0 = time.time()

    def sampler() -> None:
        while not stop.is_set():
            samples.append({"t": round(time.time() - t0, 1), **cache_metrics()})
            stop.wait(SAMPLE_S)

    def worker(wid: int) -> int:
        nonlocal next_id, ops
        local = 0
        interval = WORKERS / target_docs_s
        next_t = time.time()
        deadline = t0 + SECONDS
        while time.time() < deadline:
            now = time.time()
            if now < next_t:
                time.sleep(min(next_t - now, 0.05))
                continue
            if ARM == "insert":
                with lock:
                    i = next_id
                    next_id += 1
                    if next_id > max_docs:
                        break
                db.docs.insert_one({"_id": i, "pad": _pad(), "n": 0})
            else:
                i = random.randrange(db.docs.estimated_document_count())
                db.docs.update_one({"_id": i}, {"$set": {"pad": _pad(), "n": random.randrange(1e9)}})
            local += 1
            next_t += interval
        with lock:
            ops += local
        return local

    sam = threading.Thread(target=sampler, daemon=True)
    sam.start()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.submit(worker, w).result() for w in range(WORKERS))
    stop.set()
    sam.join(timeout=2)
    elapsed = max(time.time() - t0, 0.1)
    after = cache_metrics()
    achieved = ops / elapsed
    rate_ok = abs(achieved - target_docs_s) / target_docs_s <= 0.10
    dirty_peak = max((s["dirtyPct"] for s in samples), default=0.0)
    occ_peak = max((s["occupancyPct"] for s in samples), default=0.0)
    evict_delta = after["evictedByApp"] - before["evictedByApp"]
    onset_dirty = None
    for s in samples:
        if s["evictedByApp"] > before["evictedByApp"]:
            onset_dirty = s["dirtyPct"]
            break

    flags = []
    if not rate_ok:
        flags.append(f"achieved {achieved:.1f}/s vs target {target_docs_s:.1f}/s (>10%)")
    if dirty_peak < 1.0 and rate_mult >= 1.0:
        flags.append(f"dirty% never rose (peak {dirty_peak}) at rate_mult={rate_mult}")

    # Attribution hint: dirty trigger vs overall occupancy trigger.
    attribution = "unclear"
    if evict_delta > 0 and onset_dirty is not None:
        if onset_dirty >= 15 and occ_peak < 80:
            attribution = "consistent_with_dirty_trigger"
        elif occ_peak >= 80:
            attribution = "consistent_with_overall_occupancy_trigger"
        else:
            attribution = "mixed_or_early"

    return {
        "arm": ARM,
        "rateMultipleOfWriteBps": rate_mult,
        "targetDocsPerSecond": round(target_docs_s, 1),
        "achievedDocsPerSecond": round(achieved, 1),
        "achievedRateOk": rate_ok,
        "seconds": round(elapsed, 1),
        "ops": ops,
        "dirtyPctPeak": dirty_peak,
        "occupancyPctPeak": occ_peak,
        "evictedByAppDelta": evict_delta,
        "onsetDirtyPct": onset_dirty,
        "attribution": attribution,
        "bytesWrittenFromCacheDelta": after["bytesWrittenFromCache"]
        - before["bytesWrittenFromCache"],
        "flags": flags,
        "sampleCount": len(samples),
        "series": samples,
    }


def main() -> int:
    version = admin.command("buildInfo")["version"]
    print(f"eviction_probe MongoDB {version} arm={ARM}", file=sys.stderr)
    results = []
    for mult in RATES:
        print(f"  level {mult}x write_bps ...", file=sys.stderr)
        r = run_level(mult)
        results.append(r)
        print(
            f"    achieved {r['achievedDocsPerSecond']}/s  "
            f"dirtyPeak {r['dirtyPctPeak']}%  "
            f"occPeak {r['occupancyPctPeak']}%  "
            f"evictΔ {r['evictedByAppDelta']}  "
            f"{r['attribution']}",
            file=sys.stderr,
        )
    print("===JSON===")
    print(
        json.dumps(
            {
                "version": version,
                "arm": ARM,
                "writeBps": WRITE_BPS,
                "cacheGb": CACHE_GB,
                "secondsPerLevel": SECONDS,
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
