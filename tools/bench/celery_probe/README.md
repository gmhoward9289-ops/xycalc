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

**Drain time.** The stall ends; the backlog does not. `drainSeconds` measures
how long the fleet takes to work off what accumulated, and it is the number
that turns a two-minute storage blip into a twenty-minute outage.

**Redelivery.** The Redis broker redelivers any task unacknowledged within
`visibility_timeout`. A storage stall makes tasks slow — which is exactly when
they cross that threshold. The broker then adds load, in duplicate, at the
worst possible moment: positive feedback, the same shape as the eviction loop
in investigation 001. `duplicateExecutions` counts it directly, by incrementing
a per-task-id counter on every execution. A count above one is not a retry; the
application never asked for one.

## Knobs

All environment variables, because the experiment is about what Celery's own
configuration does to a database under stress.

| Variable | Default | What it changes |
|---|---|---|
| `PROBE_CONCURRENCY` | 8 | worker processes. Prefork gives each its own MongoDB pool, so connections are workers × pool size |
| `PROBE_PREFETCH` | 4 | tasks reserved per slot. Reserved tasks are off the queue but not running, so **queue depth understates the backlog** |
| `PROBE_ACKS_LATE` | 0 | ack after execution instead of before. Changes what a redelivery costs |
| `PROBE_VISIBILITY_TIMEOUT` | 30 | seconds before the broker redelivers. Lower it to provoke duplication deliberately |
| `PROBE_RATES` | 25,50,100,200,400 | arrival rates to sweep, tasks/second |
| `PROBE_SECONDS` | 30 | load duration per rate |
| `PROBE_DOCS` | 1500000 | dataset size. **Do not lower it to save time** — see below |

## The guards, and why they exist

Two earlier harnesses in this repo produced clean, plausible tables that
measured nothing at all. Both failure modes are guarded here:

- **The working set must exceed the cache.** The driver refuses to run below
  2× oversubscription. A dataset that fits means reads never reach the
  throttled device.
- **Reads must actually reach the device.** If `pagesReadIntoCache` is zero
  across the whole run, the output says so loudly. The mongo container's
  `mem_limit` bounds its *page cache*, not just its heap — without that the
  host serves the reads from its own RAM and the block-IO throttle never
  engages.

## Host requirements

`compose.yml` throttles `/dev/sda`, which is correct on swamplink and wrong
elsewhere; `run.sh` refuses to start rather than run unthrottled. Everything is
scoped to this compose project and its own network, and the block-IO and memory
limits apply to the mongo container's cgroup only — the host may be serving
other things.
