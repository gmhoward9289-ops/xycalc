# Celery probe

Runs a Celery fleet against a deliberately I/O-starved MongoDB, to ask what a
*queue* does to the failure investigation 003 characterised with raw threads.

```bash
cd tools/bench/celery_probe && ./run.sh
```

The task is identical to the thread probe's worker — a random point lookup by
`_id` — so the two experiments differ in the load generator and nothing else.
Anything this run shows that the thread run did not is attributable to Celery.

## Why a queue is not sixty-four threads

Threads are self-limiting. A thread waiting on a query cannot issue another, so
offered concurrency is capped by the thread count and the system finds an
equilibrium: throughput flat, latency rising. That is what investigation 003
measured.

A queue has no such property, and three things follow.

**Backlog.** If tasks arrive faster than they complete, the queue grows without
bound. The smoke run above shows it: at 200 tasks/s against a fleet managing
158/s, depth reached 478 in twelve seconds. Nothing recovers on its own.

**Drain time.** Arrivals stop; the blkio throttle does not. `drainSeconds`
measures how long the fleet takes to clear the backlog **while MongoDB is still
throttled** — not recovery after a stall ends. The compose file never lifts the
device cap mid-run, so this is "drain under a sustained bad disk," still the
number that turns a short overload into a long outage on that disk.

**Redelivery.** With `task_acks_late` (this harness defaults it on), the Redis
broker redelivers any task unacknowledged within `visibility_timeout`. A storage
stall makes tasks slow — which is exactly when they cross that threshold. The
broker then adds load, in duplicate, at the worst possible moment: positive
feedback, the same shape as the eviction loop in investigation 001.
`duplicateExecutions` counts it directly, by incrementing a per-task-id counter
on every execution. A count above one is not a retry; the application never
asked for one. With ack-before-execute, redelivery is structurally impossible —
a clean zero then says nothing about load.

## Knobs

All environment variables, because the experiment is about what Celery's own
configuration does to a database under stress.

| Variable | Default | What it changes |
|---|---|---|
| `PROBE_CONCURRENCY` | 8 | worker processes. Prefork gives each its own MongoDB pool, so connections are workers × pool size |
| `PROBE_PREFETCH` | 4 | tasks reserved per slot. Reserved tasks are off the queue but not running, so **queue depth understates the backlog** |
| `PROBE_ACKS_LATE` | 1 | ack after execution (required for any redelivery arm). Set `0` only as a control — that mode cannot produce duplicates |
| `PROBE_VISIBILITY_TIMEOUT` | 30 | seconds before the broker redelivers. Only meaningful with `PROBE_ACKS_LATE=1` |
| `PROBE_RATES` | 25,50,100,200,400 | arrival rates to sweep, tasks/second |
| `PROBE_SECONDS` | 30 | load duration per rate |
| `PROBE_DOCS` | 1500000 | dataset size. **Do not lower it to save time** — see below |

## The guards, and why they exist

Earlier harnesses in this repo produced clean, plausible tables that measured
nothing at all. The failure modes are guarded here:

- **The working set must exceed the cache.** The driver refuses to run below
  2× oversubscription. A dataset that fits means reads never reach the
  throttled device.
- **Reads must actually reach the device.** If `pagesReadIntoCache` is zero
  across the whole run, the output says so loudly. The mongo container's
  `mem_limit` bounds its *page cache*, not just its heap — without that the
  host serves the reads from its own RAM and the block-IO throttle never
  engages.
- **Zero duplicates with early ack is vacuous.** If `acksLate` is false and
  `duplicateExecutions` is zero, the driver prints a WARNING and sets
  `acksLateVacuousZeroDuplicates` in the JSON. That zero is guaranteed by
  configuration, not by load — the broker already forgot the task before it
  ran.

## Host requirements

`compose.yml` throttles `/dev/sda`, which is correct on swamplink and wrong
elsewhere; `run.sh` refuses to start rather than run unthrottled. Everything is
scoped to this compose project and its own network, and the block-IO and memory
limits apply to the mongo container's cgroup only — the host may be serving
other things.

## Redis broker maxmemory probe (issue #15)

Separate driver — same Docker image, different question. When the Celery
broker's Redis hits `maxmemory`, do you lose queued tasks or stall the fleet?

```bash
cd tools/bench/celery_probe && ./run_evict.sh
```

Phase 1 enqueues `probe.noop` tasks with **no worker running** until the
broker is at `maxmemory`. Phase 2 spawns a worker while the broker is still
at/over the ceiling. Execution ground truth lives in a separate `bookkeeping`
Redis that is never capped — counters on the broker itself would undercount
when their keys get evicted.

| Variable | Default | What it changes |
|---|---|---|
| `PROBE_MAXMEMORY` | 16mb | broker memory ceiling (compose `redis` command) |
| `PROBE_MAXMEMORY_POLICY` | noeviction | `noeviction`, `allkeys-lru`, or `volatile-lru` |
| `PROBE_PAYLOAD_BYTES` | 2048 | pad bytes per task message |
| `PROBE_ENQUEUE_ATTEMPTS` | 20000 | stop after this many send attempts |
| `PROBE_DRAIN_TIMEOUT` | 120 | seconds to wait for queue drain in phase 2 |
| `PROBE_IGNORE_RESULT` | 1 | `0` enables result-backend TTL keys for volatile-lru |
| `PROBE_RESULT_EXPIRES` | 60 | result TTL when ignore is off (evict driver only) |

Guards refuse to report an arm if Phase 1 never reaches 95% of `maxmemory`,
if `noeviction` evicts keys, or if an LRU policy never evicts when it should.
Run `./run_evict.sh` on a Linux host with Docker (swamplink). No block device
check — this probe does not use MongoDB.

Three-arm sweep and corpus import:

```bash
./sweep_evict.sh
python ../../import_evict_probe.py \
  /root/celery-evict-sweep/noeviction.log \
  /root/celery-evict-sweep/allkeys-lru.log \
  /root/celery-evict-sweep/volatile-lru.log \
  --date $(date +%F) --host swamplink --publish
cd ../.. && python -m xycalc.build
```

See `docs/investigations/005-redis-broker-eviction/FINDINGS.md`.

## Prefetch backlog sweep (issue #14 / T6)

```bash
docker compose up -d --build redis bookkeeping mongo
PROBE_RATES=400 PROBE_SECONDS=30 ./sweep_prefetch.sh
```

Recreates the worker per `PROBE_PREFETCH` value (default `1,2,4,8,16`).
`drive.py` keeps `sampleSeries` with `outstanding` / `understatement`, and
skips reloading when `docs` already has `PROBE_DOCS` documents.

## Stall / recover retry policies (issue #16 / T8)

```bash
./run_stall_recover.sh
```

Phases: baseline → live cgroup tighten (or `PROBE_STALL_MODE=pause`) → recover.
Sweeps `PROBE_RETRY_POLICY` ∈ `none,immediate,exponential,jitter` with
`max_time_ms` on the lookup. Raise `PROBE_VISIBILITY_TIMEOUT` (script default
600) so broker redelivery does not mix into `probe:retries`.
