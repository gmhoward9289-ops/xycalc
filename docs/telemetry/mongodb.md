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
| `bytes read into cache` | bytes | rate | 1 min | `obtainable` | Miss volume in bytes. Multiply out to get the read bandwidth the storage layer sees — the direct handoff to `ebs.md`. |

**Sampling interval matters.** These are cumulative counters; a rate needs two
samples. At 60 s you will see sustained pressure and miss bursts entirely. At
10 s you will see checkpoint sawtooth. Record the interval in the observation's
`notes` — a rate without its window is not a number.

## Is the ticket pool the bottleneck?

Investigation 003's series. These decide whether a storage stall has become a
concurrency ceiling, which is the difference between "queries are slow" and
"queries never return".

**Where these live was checked rather than assumed**, and the assumption was
wrong. This section first said the 7.0+ location is
`serverStatus().queues.execution`. On MongoDB 7.0.39 that path **does not
exist** — the figures are still under `wiredTiger.concurrentTransactions`,
which has instead grown new fields. Verified on a running instance
2026-07-31.

| Series | Unit | Agg | Status | Why |
|---|---|---|---|---|
| `wiredTiger.concurrentTransactions.read.totalTickets` | count | last | `obtainable` | **The divisor in the model, and it moves on 7.0+.** Measured at **4** on an idle 7.0.39 instance — the documented floor, not the 128 everyone assumes. |
| `…read.out` / `.available` | count | last | `obtainable` | `out` equal to `totalTickets` means the pool is exhausted and new operations are queueing. |
| `…read.queueLength` | count | last | `obtainable` | **New in 7.0.** The queue itself, reported directly rather than inferred. This is the number that "queries never return" looks like. |
| `…read.totalTimeQueuedMicros` | µs | rate | `obtainable` | Cumulative time spent waiting for a ticket. Rising sharply while `out` is pinned is this failure, unambiguously. |
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

## What would validate the models today

The cheapest useful case needs no load generator — just a database small enough
to be fully resident:

1. `db.stats()` → `dataSize`, `storageSize`, `indexSize`
2. Touch everything (`db.coll.find().count()` per collection, or a full scan)
3. `db.serverStatus().wiredTiger.cache` → `bytes currently in the cache`

**Does `dataSize + indexSize` predict resident bytes?** That single comparison
tests the decompression term and the index term together, against reality, and
settles the model's weakest inference. A laptop and a restored dump are enough.

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
