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
import time

import redis
from celery import Celery
from pymongo import MongoClient
from pymongo.errors import PyMongoError

REDIS_URL = os.environ.get("PROBE_REDIS", "redis://redis:6379/0")
MONGO_URL = os.environ.get("PROBE_MONGO", "mongodb://mongo:27017")
DOCS = int(os.environ.get("PROBE_DOCS", "1500000"))

# visibility_timeout is the whole point of this harness. With the Redis broker,
# a task not acknowledged within this window is redelivered to another worker.
# Under a storage stall tasks take longer than usual -- which is precisely when
# they cross that threshold, and precisely when duplicating them is worst.
VISIBILITY_TIMEOUT = int(os.environ.get("PROBE_VISIBILITY_TIMEOUT", "30"))

# Defaults to ON. Under Celery's own default -- ack before execute -- broker
# redelivery is structurally impossible at any arrival rate or visibility
# timeout, so a "zero duplicates" run proves nothing about redelivery; it is
# guaranteed by configuration. Set PROBE_ACKS_LATE=0 to go back to that
# (vacuous) mode deliberately.
ACKS_LATE = os.environ.get("PROBE_ACKS_LATE", "1") == "1"

# --- retry policy -----------------------------------------------------------
# The task previously had no retry logic at all -- it just blocked -- so
# "Celery retries" was not something this harness could exercise. This adds a
# real retry path for transient PyMongo errors (the kind a stalled device
# actually produces: server selection / wait-queue timeouts), with three
# explicit policies:
#
#   immediate           countdown=0 -- retry with no delay
#   exponential          countdown = base * 2**attempt, capped at RETRY_MAX_DELAY
#   jitter               same curve, but a random countdown in [0, that value]
#
# Celery's own default_retry_delay is 180s, so "no backoff" has to set
# countdown=0 explicitly -- otherwise "immediate" vs "exponential" is really
# just "180s" vs "exponential", not a real comparison of policies.
#
# The retry limit is a wall-clock deadline, not Celery's retry-count-based
# max_retries: a task keeps retrying until RETRY_DEADLINE seconds have passed
# since its *first* attempt, regardless of how many retries that took. That is
# the "server-enforced deadline" the harness needs -- it bounds how long a
# single task can occupy a worker slot, which a bare retry count does not.
RETRY_POLICY = os.environ.get("PROBE_RETRY_POLICY", "none")  # none|immediate|exponential|jitter
RETRY_BASE_DELAY = float(os.environ.get("PROBE_RETRY_BASE_DELAY", "1"))
RETRY_MAX_DELAY = float(os.environ.get("PROBE_RETRY_MAX_DELAY", "60"))
RETRY_DEADLINE = float(os.environ.get("PROBE_RETRY_DEADLINE", "120"))

app = Celery("probe", broker=REDIS_URL, backend=REDIS_URL)
app.conf.update(
    worker_prefetch_multiplier=int(os.environ.get("PROBE_PREFETCH", "4")),
    task_acks_late=ACKS_LATE,
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


def _retry_countdown(attempt: int) -> float | None:
    """Return the countdown for this policy, or None if the policy is 'none'
    (no retry logic to exercise -- keeps the old blocking behaviour available
    for direct comparison)."""
    if RETRY_POLICY == "immediate":
        return 0
    if RETRY_POLICY == "exponential":
        return min(RETRY_BASE_DELAY * (2**attempt), RETRY_MAX_DELAY)
    if RETRY_POLICY == "jitter":
        ceiling = min(RETRY_BASE_DELAY * (2**attempt), RETRY_MAX_DELAY)
        return random.uniform(0, ceiling)
    return None


# max_retries=None: Celery's own count-based cutoff is disabled on purpose.
# The deadline below is wall-clock, not attempt-counted, and is what actually
# bounds a task's retries.
@app.task(bind=True, name="probe.lookup", max_retries=None)
def lookup(self):
    mongo, r = _clients()
    r.incr("probe:executions")
    # >1 means this task id ran more than once. Nobody retried it; the broker
    # redelivered it. Kept separate from application-driven retries below --
    # this counter must stay about the broker, not about us.
    if r.incr(f"probe:exec:{self.request.id}") > 1:
        r.incr("probe:duplicates")

    first_attempt_key = f"probe:first_attempt:{self.request.id}"
    first_attempt = r.get(first_attempt_key)
    if first_attempt is None:
        first_attempt = time.time()
        r.set(first_attempt_key, first_attempt, ex=int(RETRY_DEADLINE) + 300)
    else:
        first_attempt = float(first_attempt)

    try:
        mongo.ticketprobe.docs.find_one({"_id": random.randrange(DOCS)})
    except PyMongoError as exc:
        r.incr("probe:retries")
        countdown = _retry_countdown(self.request.retries)
        deadline_hit = time.time() - first_attempt >= RETRY_DEADLINE
        if countdown is None or deadline_hit:
            # RETRY_POLICY is "none", or the server-enforced deadline expired:
            # stop retrying and let the failure surface.
            r.incr("probe:retryDeadlineHits" if deadline_hit else "probe:retryPolicyNone")
            raise
        raise self.retry(exc=exc, countdown=countdown)

    r.incr("probe:completed")
