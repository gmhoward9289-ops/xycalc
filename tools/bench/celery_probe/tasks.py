"""The Celery app and the one task the probe runs.

The task does exactly what the thread probe's worker did — a random point
lookup against a deliberately I/O-starved MongoDB — so the two experiments
differ in the load generator and nothing else. Anything the Celery run shows
that the thread run did not is attributable to Celery.

Every execution increments two counters in Redis: a global one, and one keyed
by task id. The second is what makes broker redelivery visible. If a task
executes twice, its per-id counter reaches 2, and that is not a retry — the
application never asked for one. It is the broker deciding the task was lost
because nobody acknowledged it in time, and handing it to a second worker while
the first is still running.
"""

from __future__ import annotations

import os
import random

import redis
from celery import Celery
from pymongo import MongoClient

REDIS_URL = os.environ.get("PROBE_REDIS", "redis://redis:6379/0")
MONGO_URL = os.environ.get("PROBE_MONGO", "mongodb://mongo:27017")
DOCS = int(os.environ.get("PROBE_DOCS", "1500000"))

# visibility_timeout is the whole point of this harness. With the Redis broker,
# a task not acknowledged within this window is redelivered to another worker.
# Under a storage stall tasks take longer than usual -- which is precisely when
# they cross this threshold, and precisely when duplicating them is worst.
VISIBILITY_TIMEOUT = int(os.environ.get("PROBE_VISIBILITY_TIMEOUT", "30"))

app = Celery("probe", broker=REDIS_URL, backend=REDIS_URL)
app.conf.update(
    worker_prefetch_multiplier=int(os.environ.get("PROBE_PREFETCH", "4")),
    task_acks_late=os.environ.get("PROBE_ACKS_LATE", "0") == "1",
    broker_transport_options={"visibility_timeout": VISIBILITY_TIMEOUT},
    result_expires=300,
    task_ignore_result=True,
    worker_send_task_events=False,
)

_mongo: MongoClient | None = None
_redis: redis.Redis | None = None


def _clients():
    # Built lazily and cached per worker process. Prefork gives each child its
    # own pool, so total connections to MongoDB is workers x pool size -- one of
    # the things this experiment is here to measure.
    global _mongo, _redis
    if _mongo is None:
        _mongo = MongoClient(MONGO_URL, maxPoolSize=8, serverSelectionTimeoutMS=30000)
    if _redis is None:
        _redis = redis.Redis.from_url(REDIS_URL)
    return _mongo, _redis


@app.task(bind=True, name="probe.lookup")
def lookup(self):
    mongo, r = _clients()
    r.incr("probe:executions")
    # >1 means this task id ran more than once. Nobody retried it; the broker
    # redelivered it.
    if r.incr(f"probe:exec:{self.request.id}") > 1:
        r.incr("probe:duplicates")
    mongo.ticketprobe.docs.find_one({"_id": random.randrange(DOCS)})
    r.incr("probe:completed")
