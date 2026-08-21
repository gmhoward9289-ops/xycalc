"""Drive the Celery probe: load the data, enqueue at a target rate, sample.

The thread probe asked "how does MongoDB behave when the device is slow". This
asks "how does a Celery fleet behave when MongoDB behaves like that", which is
a different question with at least three answers the thread probe cannot give:

  BACKLOG      Threads are self-limiting -- a thread waiting on a query cannot
               issue another. A queue is not. If tasks arrive faster than they
               complete, the backlog grows without bound, and the time to drain
               it under continued throttle can dwarf the arrival window itself.
               (This harness never lifts blkio mid-run, so drain is not
               "after the stall ends.")

  REDELIVERY   With task_acks_late (harness default), the Redis broker
               redelivers any task unacknowledged within visibility_timeout.
               A storage stall makes tasks slow, which is exactly when they
               cross that threshold -- so the broker adds load, in duplicate,
               at the worst possible moment. Positive feedback, same shape as
               the eviction loop in investigation 001. Early ack makes this
               structurally impossible; see the vacuous-zero guard below.

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

import celery
import pymongo
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
    import sys
    from pathlib import Path

    _bench = Path(__file__).resolve().parents[1]
    if str(_bench) not in sys.path:
        sys.path.insert(0, str(_bench))
    from mongo_tickets import execution_tickets

    s = mongo.admin.command("serverStatus")
    t = execution_tickets(s)
    return {
        "readTotal": t["readTotal"],
        "readOut": t["readOut"],
        "queuedMicros": t["queuedMicros"],
        "pagesRead": s["wiredTiger"]["cache"]["pages read into cache"],
    }


def load() -> dict:
    db = mongo.ticketprobe
    # Idempotent: a five-value PROBE_RATES sweep used to reload 1.5M documents
    # five times, once per invocation of this script. estimated_document_count
    # reads collection metadata rather than scanning, so this check is cheap
    # even at DOCS-scale. A count that doesn't match DOCS means either a fresh
    # dataset or a differently-sized one left over -- drop and reload either way.
    existing = db.docs.estimated_document_count() if "docs" in db.list_collection_names() else 0
    if existing == DOCS:
        print(f"reusing existing {DOCS:,} documents (idempotent load)", file=sys.stderr)
    else:
        print(f"loading {DOCS:,} documents...", file=sys.stderr)
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
        "retries": g("probe:retries"),
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
            c = counters()
            queue = r.llen(QUEUE)
            outstanding = enqueued - c["completed"]
            samples.append(
                {
                    "t": round(now - t0, 1),
                    "queue": queue,
                    "enqueuedSoFar": enqueued,
                    "outstanding": outstanding,
                    "understatement": outstanding - queue,
                    **tickets(),
                    **c,
                }
            )
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
    achieved = enqueued / elapsed if elapsed else 0.0
    # Issue #14: backlog samples only (queue > 0) so startup transient is out.
    backed = [s for s in samples if s["queue"] > 0]
    under_vals = [s["understatement"] for s in backed]
    understatement_max = max(under_vals) if under_vals else 0
    understatement_mean = (
        round(sum(under_vals) / len(under_vals), 2) if under_vals else 0.0
    )
    rate_ok = achieved >= 0.9 * rate
    if not rate_ok:
        print(
            f"WARNING: achievedRate {achieved:.1f}/s is below 90% of target "
            f"{rate}/s — backlog/prefetch comparison is vacuous for this rate.",
            file=sys.stderr,
        )
    return {
        "targetRatePerSecond": rate,
        "seconds": round(elapsed, 1),
        "enqueued": enqueued,
        "achievedRate": round(achieved, 1),
        "achievedRateOk": rate_ok,
        "completedDuringLoad": at_end["completed"],
        "completedTotal": final["completed"],
        "throughputPerSecond": round(at_end["completed"] / elapsed, 1),
        "queueDepthAtEnd": depth_at_end,
        "queueDepthMax": max((s["queue"] for s in samples), default=0),
        # Executions above enqueued are the broker's doing, not the app's.
        "executionsTotal": final["executions"],
        "duplicateExecutions": final["duplicates"],
        "retriesTotal": final["retries"],
        "duplicateRatePct": round(
            100 * final["duplicates"] / max(final["executions"], 1), 2
        ),
        "drainSeconds": drained_in,
        "drainTimedOut": drained_in is None,
        "ticketsMax": max((s["readTotal"] for s in samples), default=0),
        "ticketsOutMax": max((s["readOut"] for s in samples), default=0),
        "queuedMicrosDelta": after["queuedMicros"] - before["queuedMicros"],
        "pagesReadIntoCache": after["pagesRead"] - before["pagesRead"],
        "sampleCount": len(samples),
        # Issue #14 — depth vs true outstanding; samples retained (not discarded).
        "understatementMax": understatement_max,
        "understatementMean": understatement_mean,
        "sampleSeries": samples,
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

    acks_late = bool(app.conf.task_acks_late)
    total_duplicates = sum(x["duplicateExecutions"] for x in results)
    # Under ack-before-execute, broker redelivery is structurally impossible at
    # any arrival rate or visibility timeout -- a clean zero here is guaranteed
    # by configuration, not by load, and does not mean redelivery was tested
    # and found absent. Say so loudly instead of reporting a quiet zero, which
    # is exactly the mistake the smoke run's write-up made (#20).
    zero_dupes_is_vacuous = (not acks_late) and total_duplicates == 0
    if zero_dupes_is_vacuous:
        print(
            "\nWARNING: acksLate is OFF and duplicateExecutions was ZERO across "
            "the whole run. That zero is GUARANTEED by configuration, not "
            "produced by load -- with ack-before-execute, the broker cannot "
            "redeliver a task no matter how slow it runs. This run says "
            "nothing about redelivery. Set PROBE_ACKS_LATE=1 to make "
            "duplication possible.",
            file=sys.stderr,
        )

    print("===JSON===")
    print(
        json.dumps(
            {
                "mongoVersion": mongo.admin.command("buildInfo")["version"],
                # Resolved, not just requested: the Dockerfile used to install
                # celery[redis]>=5.3 unpinned against floating image tags, so
                # no coefficient derived from this JSON had an honest
                # applies_to. Record what actually ran.
                "celeryVersion": celery.__version__,
                "pymongoVersion": pymongo.version,
                "redisClientVersion": redis.__version__,
                "redisServerVersion": r.info().get("redis_version"),
                "prefetch": app.conf.worker_prefetch_multiplier,
                "acksLate": acks_late,
                "acksLateVacuousZeroDuplicates": zero_dupes_is_vacuous,
                "visibilityTimeout": app.conf.broker_transport_options.get(
                    "visibility_timeout"
                ),
                "retryPolicy": os.environ.get("PROBE_RETRY_POLICY", "none"),
                "retryDeadline": float(os.environ.get("PROBE_RETRY_DEADLINE", "120")),
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
