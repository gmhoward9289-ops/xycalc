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
