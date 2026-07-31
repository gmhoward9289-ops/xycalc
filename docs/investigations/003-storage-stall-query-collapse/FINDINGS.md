# Findings — why MongoDB stops returning queries when storage is throttled

**Investigated:** 2026-07-31, from a field observation ·
**Model:** `mongodb.ticket-throughput-ceiling` · **Validation:** none (n=0).

> "When IOPS or throughput exceed, MongoDB starts not returning queries,
> especially when there is some contention." — George

---

## The observation is documented behaviour

MongoDB describes this failure in its own words, and the operative verb is
*queued*:

> "Performance problems that are the result of locking occur when the remaining
> number of available read or write tickets reaches zero, meaning **any new
> read or write requests will be queued** until a new read or write ticket is
> available."
> — [Performance Tuning, MongoDB 7.0](https://www.mongodb.com/docs/v7.0/administration/performance-tuning/)

Queued. Not rejected, not served slowly. That single word is the difference
between the slope you would expect and the cliff you observed: demand above the
ceiling does not degrade throughput, it grows a queue that never drains. From
the client, a query that is queued behind a queue that cannot empty is
indistinguishable from a database that has stopped answering.

So the observation is not a mystery to explain. It is the documented
consequence of a mechanism most people never look at.

---

## The mechanism, and why it is a cliff

MongoDB caps how many operations may be inside the storage engine at once. Call
it N. If each operation holds its ticket for L seconds, the sustainable rate is
N/L — Little's law — and no amount of client concurrency exceeds it.

**The trap is that L moves by orders of magnitude and N does not.**

| Situation | N | L | Ceiling |
|---|---|---|---|
| Healthy SSD | 128 | 1 ms | **128,000 ops/s** — never binds |
| Throttled EBS volume | 128 | 100 ms | **1,280 ops/s** |
| Throttled, on a resting 7.0 instance | 4 | 100 ms | **40 ops/s** |

```bash
xycalc sizing mongodb.ticket-throughput-ceiling --storage-latency-seconds 0.1
xycalc sizing mongodb.ticket-throughput-ceiling --storage-latency-seconds 0.1 --tickets 4
```

Nothing about the workload changed between those rows. The application is
issuing the same queries at the same rate. The ceiling fell by a factor of 100,
and then by another factor of 32, entirely because of storage.

**40 operations per second is not a slow database. It is a stopped one.**

### Why contention is the amplifier you named

The ticket limit is invisible without concurrency. One client at a time never
holds more than one ticket, so N never binds however slow the disk gets — you
would observe queries that are slow, and that is all.

Contention is what fills the pool. And once it is full, the failure stops being
proportional: the 129th concurrent operation does not run 1/129th as fast, it
waits for a ticket, and if arrivals keep coming it waits forever. That is why
your observation pairs "IOPS exceeded" with "especially when there is
contention" — neither one alone produces the symptom. Together they do.

### The feedback loop, which is why it is abrupt

The brief hypothesised a loop, and investigation 001 supplies the missing half.

Past the eviction trigger, WiredTiger pulls **application threads** into
eviction work. Eviction writes pages out. Writing pages needs the disk that is
already saturated. So threads that came in to run a query end up blocked on the
device that is already the bottleneck — **while holding tickets**.

That is positive feedback: more stalled threads → longer device queue → higher
L → lower ceiling → more stalled threads. Positive feedback is what turns a
slope into a cliff, and it is the reason the transition feels like a switch
flipping rather than a gradual slowdown.

It also explains the part that looks most like a bug: **queries that would have
been pure cache hits stall too.** They need no disk at all. They need a ticket,
and the tickets are all held by operations waiting on storage. That is why the
symptom reads as "MongoDB stopped" rather than "storage got slow" — the
slowness is not where the queries are.

---

## The version detail that changes the size of the problem

Before 7.0, N was a static 128 read + 128 write. From 7.0 it is dynamic:

> "Starting in version 7.0, MongoDB uses a dynamic algorithm to adjust the
> maximum number of concurrent storage engine transactions, optimizing database
> throughput during cluster overload."

The algorithm is `throughputProbing`, and Percona reports its bounds where the
manual does not: **minimum concurrency 4, maximum 128**, with the algorithm
starting from a much lower baseline than the old constant.

That is a 32× range in the term that divides into the ceiling — and it moves
*while you are having the incident*.

### Measured, and worse than the write-up assumed

The `serverStatus` reading was cheap, so it was taken rather than assumed. On
the MongoDB **7.0.39** benchmark instance, **idle**:

```
wiredTiger.concurrentTransactions.read.totalTickets  = 4
wiredTiger.concurrentTransactions.write.totalTickets = 4
```

**Four.** The documented floor, on an instance doing nothing at all. Not the
128 that every pre-7.0 mental model — including the first draft of this
document — takes as the starting point.

So the third row of that table is not a pessimistic worst case reached after
the algorithm gives up. It is where a 7.0 instance *rests*. The algorithm has
to climb from 4, and the ceiling is 40 ops/s at 100 ms until it does.

Two corrections fell out of one command:

- **`serverStatus().queues.execution` does not exist in 7.0.39.** The telemetry
  doc had confidently named it as the 7.0+ location. The figures are still
  under `wiredTiger.concurrentTransactions`, which has instead grown
  `queueLength`, `totalTimeQueuedMicros`, `addedToQueue` and
  `removedFromQueue`. Those four are new in 7.0 and they measure this failure
  *directly* — the queue, and the time spent in it, rather than something you
  infer from tickets running out.
- **"You have 128 tickets" is wrong on 7.0+, by 32x, in the dangerous
  direction.**

### Still open

Does the pool climb under load — and does it climb when the bottleneck is the
*device* rather than concurrency?

That distinction is the whole question. `throughputProbing` raises concurrency
and measures whether throughput improves. When the limit is the disk, adding
concurrency does not improve throughput; it just deepens the device queue. An
algorithm doing exactly what it was designed to do could therefore conclude
that more concurrency does not help and stay near the floor — precisely when
the floor hurts most.

That is a hypothesis with a mechanism, not a finding. It needs `totalTickets`
and `totalTimeQueuedMicros` sampled through a real storage stall, and it
remains the highest-value measurement outstanding in this project. One idle
reading establishes the resting value and the field location. It establishes
nothing about behaviour under load.

---

## What to look at during an incident

In order of how directly each one confirms this diagnosis:

| Reading | Where | Says |
|---|---|---|
| `totalTickets`, and `out` against it | `serverStatus().wiredTiger.concurrentTransactions` — **not** `queues.execution`, which does not exist in 7.0.39 | whether the pool is exhausted, and **what N currently is**. Do not assume 128 |
| `totalTimeQueuedMicros`, `queueLength` | same object, new in 7.0 | the queue and the time spent in it, measured rather than inferred. Rising sharply while `out` is pinned is this failure with nothing left to interpret |
| queued readers/writers | `serverStatus().globalLock.currentQueue` | how much demand is stacked behind the pool |
| pages evicted by application threads | `wiredTiger.cache` | whether the feedback loop is running |
| `VolumeIOPSExceededCheck` | CloudWatch | whether storage is actually throttled — investigation 002 |
| read latency | `VolumeAvgReadLatency`, `iostat -x 1` | L, the term that moved |

The diagnostic sequence: tickets exhausted **and** the exceeded-check firing
means storage is the cause and the ticket pool is the transmission. Tickets
exhausted **without** the exceeded-check means look somewhere else — a slow
query holding tickets, a lock, or a workload that genuinely needs more
concurrency.

---

## What this does not say

- **It is not an argument for raising the ticket count.** Raising N against a
  saturated device deepens the device queue and raises L; the ceiling N/L may
  not improve at all, and latency for everything gets worse. The lever that
  works is reducing L or reducing the reads that reach the disk — which routes
  straight back to investigation 001, because the reads exist because the cache
  did not hold the working set.
- **The model treats read and write pools independently, and they are not.** A
  write stall that fills the write pool still consumes the device that reads
  depend on.
- **`mongodb.ticket-throughput-ceiling` is unvalidated.** Validating it needs
  fault injection — a MongoDB driven past its ticket ceiling behind
  artificially slow storage, checking where throughput actually flattens
  against where the model says it should. That is a deliberate experiment, not
  something to wait for.

---

## The three investigations are one chain

1. **001** — the cache cannot hold the whole database, so misses go to disk.
2. **002** — the disk throttles on the peak second, and the metrics most people
   watch cannot see it.
3. **003** — the throttle converts into a concurrency ceiling, and the queue
   behind it does not drain.

Each was asked as a separate question. They are one failure, and the corpus
now carries it end to end.
