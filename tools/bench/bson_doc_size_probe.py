"""Arm 3 — BSON ~1 MiB vs ~15 MiB docs on real gp3 (same oversub).

Loads two collections sized to the same WiredTiger-cache oversubscription,
then random point-reads. Emits ops/s, mean latency, and pages-read-per-op.
"""
from __future__ import annotations

import json
import os
import random
import string
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from pymongo import MongoClient

URI = os.environ.get("PROBE_URI", "mongodb://127.0.0.1:27017")
CACHE_GB = float(os.environ.get("PROBE_CACHE_GB", "0.5"))
MIN_OVERSUB = float(os.environ.get("PROBE_MIN_OVERSUB", "2.0"))
TARGET_OVERSUB = float(os.environ.get("PROBE_TARGET_OVERSUB", "2.0"))
SECONDS = float(os.environ.get("PROBE_SECONDS", "45"))
WORKERS = int(os.environ.get("PROBE_WORKERS", "8"))
# ~1 MiB and ~15 MiB payload pads (BSON overhead on top)
SIZES = [
    int(x)
    for x in os.environ.get(
        "PROBE_DOC_BYTES_LIST", f"{1024 * 1024},{15 * 1024 * 1024}"
    ).split(",")
]

client = MongoClient(URI, maxPoolSize=WORKERS + 8, serverSelectionTimeoutMS=60000)
admin = client.admin
db = client.bsondocs


def cache_max() -> int:
    return int(admin.command("serverStatus")["wiredTiger"]["cache"]["maximum bytes configured"])


def pages_read() -> int:
    return int(admin.command("serverStatus")["wiredTiger"]["cache"]["pages read into cache"])


def _pad(n: int) -> str:
    alphabet = string.ascii_lowercase + string.digits
    # leave room for _id / field names
    body = max(n - 64, 16)
    return "".join(random.choices(alphabet, k=body))


def load_size(doc_bytes: int) -> dict:
    coll = db[f"d{doc_bytes}"]
    coll.drop()
    need = int(cache_max() * TARGET_OVERSUB)
    n_docs = max(int(need / max(doc_bytes, 1)) + 2, 8)
    print(
        f"loading doc_bytes={doc_bytes} n_docs={n_docs} target_bytes≈{need}",
        file=sys.stderr,
        flush=True,
    )
    batch = []
    for i in range(n_docs):
        batch.append({"_id": i, "pad": _pad(doc_bytes)})
        if len(batch) >= 8:
            coll.insert_many(batch, ordered=False)
            batch = []
    if batch:
        coll.insert_many(batch, ordered=False)
    st = db.command("collstats", coll.name)
    over = st["size"] / cache_max()
    print(
        f"  dataSize={st['size']/1e6:.1f}MB storage={st['storageSize']/1e6:.1f}MB "
        f"avgObj={st.get('avgObjSize')} oversub={over:.2f}x",
        file=sys.stderr,
        flush=True,
    )
    if over < MIN_OVERSUB:
        raise SystemExit(f"REFUSE: oversub {over:.2f}x < {MIN_OVERSUB}")
    return {
        "doc_bytes_target": doc_bytes,
        "n_docs": n_docs,
        "dataSize": st["size"],
        "storageSize": st["storageSize"],
        "avgObjSize": st.get("avgObjSize"),
        "oversubscription": round(over, 3),
        "cache_max": cache_max(),
    }


def drive(doc_bytes: int, n_docs: int) -> dict:
    coll = db[f"d{doc_bytes}"]
    latencies: list[float] = []
    ok = 0
    err = 0
    t0 = time.perf_counter()
    pr0 = pages_read()
    stop = t0 + SECONDS

    def one(_):
        nonlocal ok, err
        while time.perf_counter() < stop:
            i = random.randrange(n_docs)
            t = time.perf_counter()
            try:
                coll.find_one({"_id": i})
                latencies.append((time.perf_counter() - t) * 1000.0)
                ok += 1
            except Exception:
                err += 1

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        list(ex.map(one, range(WORKERS)))
    elapsed = time.perf_counter() - t0
    pr1 = pages_read()
    pages = pr1 - pr0
    ops = ok
    mean_lat = sum(latencies) / len(latencies) if latencies else 0.0
    return {
        "seconds": round(elapsed, 2),
        "ops": ops,
        "errors": err,
        "ops_per_s": round(ops / elapsed, 2) if elapsed else 0.0,
        "mean_latency_ms": round(mean_lat, 3),
        "pages_read": pages,
        "pages_read_per_op": round(pages / ops, 4) if ops else None,
        "workers": WORKERS,
    }


def main() -> None:
    out = {
        "arm": "bson-doc-size-gp3",
        "cache_gb_env": CACHE_GB,
        "cache_max_bytes": cache_max(),
        "target_oversub": TARGET_OVERSUB,
        "min_oversub": MIN_OVERSUB,
        "seconds": SECONDS,
        "sizes": [],
    }
    print(f"cache_max={cache_max()} target_oversub={TARGET_OVERSUB}", file=sys.stderr)
    for sz in SIZES:
        meta = load_size(sz)
        # brief settle so first reads are cold-ish vs insert cache warm
        time.sleep(3)
        drive_stats = drive(sz, meta["n_docs"])
        row = {**meta, "drive": drive_stats}
        out["sizes"].append(row)
        print(json.dumps(row), file=sys.stderr, flush=True)
    path = os.environ.get("PROBE_OUT", "/opt/xycalc/results/bson-size-summary.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(json.dumps(out, indent=2))
    print(f"wrote {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
