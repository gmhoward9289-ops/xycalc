# Telemetry wanted — MongoDB / WiredTiger

Everything here is `obtainable`: one shell and one `mongosh` gets all of it. No
Coralogix, no Grafana, no CloudWatch. That is unusual and it makes MongoDB the
right system to have started with — the models can be validated by anyone with
a database, including a laptop with a restored dump.

## The sizing question

`db.stats()`. One call, three numbers, and the distinction the whole model
turns on.

| Series | Unit | Status | Why |
|---|---|---|---|
| `dataSize` | bytes | `obtainable` | Uncompressed collection data. **What actually occupies the cache.** Measuring this removes the compression coefficient entirely — the weakest term in the model. |
| `storageSize` | bytes | `obtainable` | Compressed on disk. What people mean by "a 500 GB database". |
| `indexSize` | bytes | `obtainable` | Index bytes. Whether this expands in cache is the model's weakest inference. |

**`dataSize / storageSize` is a measured compression ratio for the real
collection**, and it beats any published figure outright. Anyone who can run
this should, and should record it as an observation — the corpus coefficient
exists for people who have been handed a number and no access.

## Is the cache actually under pressure?

`db.serverStatus().wiredTiger.cache`, sampled. Point-in-time values answer
"how full"; rates answer "at what cost", and the cost is the part that matters.

| Series | Unit | Agg | Window | Status | Why |
|---|---|---|---|---|---|
| `bytes currently in the cache` | bytes | last | 1 min | `obtainable` | The occupancy. Against `maximum bytes configured`, gives utilisation. |
| `maximum bytes configured` | bytes | last | — | `obtainable` | The denominator. Constant unless someone changed it. |
| `tracked dirty bytes in the cache` | bytes | last | 1 min | `obtainable` | Against the 20% dirty trigger. **A write-heavy instance hits this long before it fills the cache**, and a dashboard showing only total occupancy will show a healthy 40% while writers are being throttled. |
| `pages read into cache` | count | rate | 1 min | `obtainable` | Misses. This is the series that becomes the storage question — every page read into cache came from disk. |
| `pages evicted by application threads` | count | rate | 1 min | `obtainable` | **The money metric.** Sustained non-zero means queries are doing eviction work: the cache is past `eviction_trigger` and the symptom is latency, not memory. If only one series is graphed, this one. |
| `eviction server unable to reach eviction goal` | count | rate | 5 min | `obtainable` | Background eviction losing. Precedes the previous metric. |
| occupancy % = `bytes currently` / `maximum bytes configured` | percent | last | 1 min | `obtainable` | Place yourself on the 80 → 90 → 95 ladder. Default `eviction_target` is 80; 90 is closer to app-thread conscription at 95. Investigation 007: raising the *configured* target 80→90 held the cache fuller (~78% → ~87–88% mean on 25s passes); ops/s delta was modest/noisy on a miss-bound throttle. |
| `bytes read into cache` | bytes | rate | 1 min | `obtainable` | Miss volume in bytes. Multiply out to get the read bandwidth the storage layer sees — the direct handoff to `ebs.md`. |

**One-shot snapshot (cache + tickets + tcmalloc)** — what investigation 007
captures at the end of each probe leg:

```javascript
const s = db.serverStatus();
const c = s.wiredTiger.cache;
const t = (s.tcmalloc && s.tcmalloc.generic) || {};
const max = c["maximum bytes configured"];
printjson({
  occupancyPct: 100 * c["bytes currently in the cache"] / max,
  dirtyPct: 100 * c["tracked dirty bytes in the cache"] / max,
  appEvict: c["pages evicted by application threads"],
  unable: c["eviction server unable to reach eviction goal"],
  // 7.0: wiredTiger.concurrentTransactions; 8.0+: queues.execution
  tickets: (s.queues && s.queues.execution)
    ? s.queues.execution.read.totalTickets
    : s.wiredTiger.concurrentTransactions.read.totalTickets,
  queuedMicros: (s.queues && s.queues.execution)
    ? s.queues.execution.read.normalPriority.totalTimeQueuedMicros
    : s.wiredTiger.concurrentTransactions.read.totalTimeQueuedMicros,
  tcmallocHeap: t.heap_size,
  tcmallocAllocated: t.current_allocated_bytes || t.total_allocated_bytes
});
```

**Sampling interval matters.** These are cumulative counters; a rate needs two
samples. At 60 s you will see sustained pressure and miss bursts entirely. At
10 s you will see checkpoint sawtooth. Record the interval in the observation's
`notes` — a rate without its window is not a number.

## Is the ticket pool the bottleneck?

Investigation 003's series. These decide whether a storage stall has become a
concurrency ceiling, which is the difference between "queries are slow" and
"queries never return".

**Where these live was checked rather than assumed**, and the path moved
twice. The section first said the 7.0+ location is
`serverStatus().queues.execution`. On MongoDB **7.0.39** that path **does not
exist** — the figures are under `wiredTiger.concurrentTransactions`, which
grew queue fields. Verified 2026-07-31. On MongoDB **8.0.29 and 8.2.12** the
opposite is true: `wiredTiger.concurrentTransactions` is **gone**, and tickets
live under `queues.execution` again (verified 2026-08-21, issue #7). Idle
`totalTickets` is **4** on all three versions checked.

| Version | Ticket path | Idle `totalTickets` | Checked |
|---|---|---|---|
| 7.0.39 | `wiredTiger.concurrentTransactions.{read,write}` | 4 | 2026-07-31 |
| 8.0.29 | `queues.execution.{read,write}` | 4 | 2026-08-21 |
| 8.2.12 | `queues.execution.{read,write}` | 4 | 2026-08-21 |

On 8.x, `queueLength` / `totalTimeQueuedMicros` / `addedToQueue` /
`removedFromQueue` sit under `{normalPriority,exempt}` on each of read/write,
not directly on the read/write object. Do not confuse `queues.ingress`
(default `totalTickets` 1_000_000) with execution tickets.

| Series | Unit | Agg | Status | Why |
|---|---|---|---|---|
| `…read.totalTickets` (path per table above) | count | last | `obtainable` | **The divisor in the model, and it moves on 7.0+.** Measured at **4** idle on 7.0.39 / 8.0.29 / 8.2.12 — the documented floor, not the 128 everyone assumes. |
| `…read.out` / `.available` | count | last | `obtainable` | `out` equal to `totalTickets` means the pool is exhausted and new operations are queueing. |
| `…read.queueLength` (7.0) or `…read.normalPriority.queueLength` (8.x) | count | last | `obtainable` | **New in 7.0.** The queue itself, reported directly rather than inferred. This is the number that "queries never return" looks like. |
| `…read.totalTimeQueuedMicros` (same nesting note) | µs | rate | `obtainable` | Cumulative time spent waiting for a ticket. Rising sharply while `out` is pinned is this failure, unambiguously. |
| `…addedToQueue` / `.removedFromQueue` | count | rate | `obtainable` | Arrival and drain rates for the queue. Added exceeding removed, sustained, is a queue that does not drain. |
| `globalLock.currentQueue.readers` / `.writers` | count | last | `obtainable` | Demand stacked behind the pool. |
| `globalLock.activeClients.readers` / `.writers` | count | last | `obtainable` | Concurrency actually in flight. Without contention the ticket limit never binds, so this says whether the precondition even holds. |

**The measurement that would settle the open question.** Record
`totalTickets` alongside `totalTimeQueuedMicros` through a real storage stall.
The resting value is already known to be 4; what nobody here has seen is
whether it climbs under load, and whether it climbs when the bottleneck is the
device rather than the concurrency. An algorithm that probes by raising
concurrency and measuring throughput will see no improvement when the disk is
the limit — and may therefore stay at the floor precisely when the floor hurts
most. That is a hypothesis, not a finding.

## Connections and per-operation memory

| Series | Unit | Status | Why |
|---|---|---|---|
| `connections.current` | count | `obtainable` | Per-connection memory is outside the cache but inside the RAM budget. Not yet modelled: no cited coefficient for per-connection bytes. |
| `tcmalloc.generic.heap_size` vs `total_allocated_bytes` | bytes | `obtainable` | The gap is allocator fragmentation — RAM held by mongod and used by nothing. Known to be significant on long-running instances and **currently absent from the model entirely**. |

Both are gaps in the corpus rather than gaps in the telemetry. Named here so
the omission is visible.

## GridFS and large binaries

| Series | Unit | Status | Why |
|---|---|---|---|
| `db.fs.files.stats().count` / `size` / `storageSize` | count / bytes | `obtainable` | Metadata collection footprint — usually small vs chunks. |
| `db.fs.chunks.stats().count` / `size` / `storageSize` / `avgObjSize` | count / bytes | `obtainable` | Chunk payload + BSON overhead in aggregate. Compare `avgObjSize` to the documented 255 KiB default (261,120 B data field) to see custom chunk sizes or compression effects. |
| `files.length` vs `files.chunkSize` | bytes | `obtainable` | Per-file: expected chunk count is `ceil(length / chunkSize)`. |

**What the corpus already holds.** Default chunk size, 16 MiB BSON ceiling, and the 256 KiB MMAPv1 padding rationale are documented coefficients — no GridFS storage model yet, because vendor docs do not publish a fixed per-chunk overhead constant beyond the payload size.

## PyMongo / driver connection demand

| Series | Unit | Status | Why |
|---|---|---|---|
| `connections.current` / `connections.available` | count | `obtainable` | Server view of what pools opened. Compare to `processes × maxPoolSize`. |
| Client `maxPoolSize` / `minPoolSize` / `maxConnecting` | count | `obtainable` (app config) | Defaults now cited (100 / 0 / 2). Config, not a mongosh metric. |

## Foreign fields / `$lookup`

No purpose-built server counter isolates "$lookup cost" as a single number. Proxies:

| Series | Unit | Status | Why |
|---|---|---|---|
| Aggregation `$lookup` stage explain / profiler | ms | `obtainable` | Per-query latency under a fixed workload — manufacturable, not a vendor constant. |
| COLLSCAN / `$lookup` stage latency (explain or profiler) | ms | `obtainable` | **The invitation:** measure stage time for a fallback query (COLLSCAN, relationship join, allow-list miss) vs indexed point-lookup on the same collection, at two sizes. |
| Ticket hold time L under that workload | s | `obtainable` | Pair with `totalTimeQueuedMicros` / `read.out` from the ticket section — L is hold time, not client RTT. Optional `scan_fanout` 1 vs N if tasks fan out. |
| `db.serverStatus().metrics.commands.aggregate` | count | `obtainable` | Volume, not join cost. |

Corpus holds ObjectId = 12 B and "prefer manual refs over DBRefs"; it does **not** hold a `$lookup` latency multiplier — none is published. **Submit measured stage latency and L as an observation** (`xy-observe` / `data/observations/` with `applies_to`); a measured L band may land later. Until then L stays a pasted input — not a YAML 6–12 coefficient.

## Alerting (vital subset)

Full board + thresholds: [`recommendations.md`](recommendations.md)
(Simple / Advanced / Evidence).

| Priority | Signal | Suggested start |
|---|---|---|
| Page | Sustained `pages evicted by application threads` rate > 0 | 5 min |
| Page | `read.out == totalTickets` and `queueLength > 0` | 2 min |
| Ticket | Occupancy ≥ 90% | 15 min |
| Ticket | Dirty ≥ 15% (headroom before 20% dirty trigger) | 10 min |

Do not page on RSS alone. Occupancy reference lines: 80 / 90 / **95**.

## What would validate the models today

The cheapest useful case needs no load generator — just a database small enough
to be fully resident:

1. `db.stats()` → `dataSize`, `storageSize`, `indexSize`
2. Touch everything (`db.coll.find().count()` per collection, or a full scan)
3. `db.serverStatus().wiredTiger.cache` → `bytes currently in the cache`

**Does `dataSize + indexSize` predict resident bytes?** That single comparison
tests the decompression term and the index term together, against reality, and
settles the model's weakest inference. A laptop and a restored dump are enough.

**When mongod shares a host:** RSS alone does not show page-cache reclaim.
Collect `memory.stat` `anon` / `file` / `workingset_refault_file` next to
this cache snapshot — see [`cgroup.md`](cgroup.md). Investigation 009 did
not take that pair.

## Collection

```javascript
// one-shot, both documents, ready to import
print(JSON.stringify({
  stats: db.stats(),
  cache: db.serverStatus().wiredTiger.cache,
  at: new Date()
}))
```

Feed the result to `xy-observe`. Nothing in it identifies a host or a dataset
beyond what you choose to put in `machine_class` and `workload`.
