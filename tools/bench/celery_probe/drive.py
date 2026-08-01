"""Drive the Celery probe: load the data, enqueue at a target rate, sample.

The thread probe asked "how does MongoDB behave when the device is slow". This
asks "how does a Celery fleet behave when MongoDB behaves like that", which is
a different question with at least three answers the thread probe cannot give:

  BACKLOG      Threads are self-limiting -- a thread waiting on a query cannot
               issue another. A queue is not. If tasks arrive faster than they
               complete, the backlog grows without bound, and the time to drain
               it after the stall ends can dwarf the stall itself.

  REDELIVERY   The Redis broker redelivers any task unacknowledged within
               visibility_timeout. A storage stall makes tasks slow, which is
               exactly when they cross that threshold -- so the broker adds
               load, in duplicate, at the worst possible moment. Positive
               feedback, same shape as the eviction loop in investigation 001.

  PREFETCH     A worker reserves prefetch_multiplier x concurrency tasks. Those
               are off the queue but not running, so queue depth understates the
               backlog and the fleet is slower to shed load than it looks.
"""

from __future__ import annotations

import json
import os
import random
import string
import sys
import time

import redis
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(__file__))
from tasks import app  # noqa: E402

MONGO_URL = os.environ.get("PROBE_MONGO", "mongodb://mongo:27017")
REDIS_URL = os.environ.get("PROBE_REDIS", "redis://redis:6379/0")
DOCS = int(os.environ.get("PROBE_DOCS", "1500000"))
RATES = [int(x) for x in os.environ.get("PROBE_RATES", "25,50,100,200,400").split(",")]
SECONDS = float(os.environ.get("PROBE_SECONDS", "30"))
DRAIN_TIMEOUT = float(os.environ.get("PROBE_DRAIN_TIMEOUT", "120"))
MIN_OVERSUB = float(os.environ.get("PROBE_MIN_OVERSUB", "2.0"))
QUEUE = os.environ.get("PROBE_QUEUE", "celery")

mongo = MongoClient(MONGO_URL, serverSelectionTimeoutMS=60000)
r = redis.Redis.from_url(REDIS_URL)


def cache_max() -> int:
    c = mongo.admin.command("serverStatus")["wiredTiger"]["cache"]
    return c["maximum bytes configured"]


def tickets() -> dict:
    s = mongo.admin.command("serverStatus")
    c = s["wiredTiger"]["concurrentTransactions"]
    return {
        "readTotal": c["read"]["totalTickets"],
        "readOut": c["read"]["out"],
        "queuedMicros": int(c["read"].get("totalTimeQueuedMicros", 0)),
        "pagesRead": s["wiredTiger"]["cache"]["pages read into cache"],
    }


def load() -> dict:
    print(f"loading {DOCS:,} documents...", file=sys.stderr)
    db = mongo.ticketprobe
    db.docs.drop()
    alphabet = string.ascii_lowercase + string.digits
    batch = []
    for i in range(DOCS):
        batch.append({"_id": i, "pad": "".join(random.choices(alphabet, k=700))})
        if len(batch) == 2000:
            db.docs.insert_many(batch, ordered=False)
            batch = []
    if batch:
        db.docs.insert_many(batch, ordered=False)
    st = db.command("collstats", "docs")
    oversub = st["size"] / cache_max()
    print(
        f"  loaded. dataSize={st['size'] / 1e6:.0f} MB  cache={cache_max() / 1e6:.0f} MB"
        f"  oversubscription={oversub:.1f}x",
        file=sys.stderr,
    )
    if oversub < MIN_OVERSUB:
        # Same guard as the thread probe, for the same reason: a working set
        # that fits makes the throttled device irrelevant and produces a clean
        # table describing a healthy system.
        raise SystemExit(
            f"REFUSING TO RUN: data is only {oversub:.1f}x the cache. Reads would "
            f"hit cache, the throttle would do nothing, and the result would "
            f"look fine and mean nothing. Raise PROBE_DOCS."
        )
    return {"dataSizeBytes": st["size"], "oversubscription": round(oversub, 2)}


def counters() -> dict:
    def g(k):
        v = r.get(k)
        return int(v) if v else 0

    return {
        "executions": g("probe:executions"),
        "completed": g("probe:completed"),
        "duplicates": g("probe:duplicates"),
    }


def reset() -> None:
    for k in r.scan_iter("probe:*"):
        r.delete(k)
    r.delete(QUEUE)


def run_rate(rate: int) -> dict:
    reset()
    before, t0 = tickets(), time.time()
    samples: list[dict] = []
    enqueued = 0
    interval = 1.0 / rate
    next_send = t0
    deadline = t0 + SECONDS
    next_sample = t0

    while time.time() < deadline:
        now = time.time()
        if now >= next_send:
            app.send_task("probe.lookup", queue=QUEUE)
            enqueued += 1
            next_send += interval
        if now >= next_sample:
            samples.append({"queue": r.llen(QUEUE), **tickets(), **counters()})
            next_sample = now + 0.5
        slack = min(next_send, next_sample) - time.time()
        if slack > 0:
            time.sleep(slack)

    load_end = time.time()
    at_end = counters()
    depth_at_end = r.llen(QUEUE)

    # How long to work off the backlog once arrivals stop. This is the number
    # that turns a two-minute stall into a twenty-minute outage, and it has no
    # analogue in the thread experiment.
    drain_start = time.time()
    drained_in = None
    while time.time() - drain_start < DRAIN_TIMEOUT:
        if r.llen(QUEUE) == 0 and counters()["completed"] >= enqueued:
            drained_in = round(time.time() - drain_start, 1)
            break
        time.sleep(0.5)

    final, after = counters(), tickets()
    elapsed = load_end - t0
    return {
        "targetRatePerSecond": rate,
        "seconds": round(elapsed, 1),
        "enqueued": enqueued,
        "completedDuringLoad": at_end["completed"],
        "completedTotal": final["completed"],
        "throughputPerSecond": round(at_end["completed"] / elapsed, 1),
        "queueDepthAtEnd": depth_at_end,
        "queueDepthMax": max((s["queue"] for s in samples), default=0),
        # Executions above enqueued are the broker's doing, not the app's.
        "executionsTotal": final["executions"],
        "duplicateExecutions": final["duplicates"],
        "duplicateRatePct": round(
            100 * final["duplicates"] / max(final["executions"], 1), 2
        ),
        "drainSeconds": drained_in,
        "drainTimedOut": drained_in is None,
        "ticketsMax": max((s["readTotal"] for s in samples), default=0),
        "ticketsOutMax": max((s["readOut"] for s in samples), default=0),
        "queuedMicrosDelta": after["queuedMicros"] - before["queuedMicros"],
        "pagesReadIntoCache": after["pagesRead"] - before["pagesRead"],
        "samples": len(samples),
    }


def main() -> int:
    meta = load()
    results = []
    print(
        f"{'rate':>6} {'done/s':>8} {'qmax':>7} {'qend':>7} {'dupes':>7} "
        f"{'dup%':>6} {'drain s':>8} {'tickets':>8}",
        file=sys.stderr,
    )
    for rate in RATES:
        res = run_rate(rate)
        results.append(res)
        drain = "TIMEOUT" if res["drainTimedOut"] else res["drainSeconds"]
        print(
            f"{res['targetRatePerSecond']:>6} {res['throughputPerSecond']:>8} "
            f"{res['queueDepthMax']:>7} {res['queueDepthAtEnd']:>7} "
            f"{res['duplicateExecutions']:>7} {res['duplicateRatePct']:>6} "
            f"{str(drain):>8} {res['ticketsMax']:>8}",
            file=sys.stderr,
        )

    if sum(x["pagesReadIntoCache"] for x in results) == 0:
        print(
            "\nWARNING: pagesReadIntoCache was ZERO across the whole run. No read "
            "reached the throttled device, so these numbers describe a healthy "
            "database, not a stalled one. Lower the mongo container's --memory "
            "or raise PROBE_DOCS.",
            file=sys.stderr,
        )

    print("===JSON===")
    print(
        json.dumps(
            {
                "mongoVersion": mongo.admin.command("buildInfo")["version"],
                "prefetch": app.conf.worker_prefetch_multiplier,
                "acksLate": app.conf.task_acks_late,
                "visibilityTimeout": app.conf.broker_transport_options.get(
                    "visibility_timeout"
                ),
                "secondsPerRate": SECONDS,
                "docs": DOCS,
                **meta,
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
