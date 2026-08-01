# Findings — why MongoDB stops returning queries when storage is throttled

**Investigated:** 2026-07-31, from a field observation. Fault-injection run
added 2026-08-01. ·
**Model:** `mongodb.ticket-throughput-ceiling` · **Validation:** none (n=0) —
survived one fault-injection experiment on one machine; no formal validation
case published yet (see "Measured under load" below for why).

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

### Measured under load — the pool climbs, and climbing does not help

A fault-injection run on 2026-08-01 answered the question this section used to
ask. Harness: `tools/bench/ticket_probe.sh` /
`tools/bench/ticket_probe.py`. MongoDB 7.0.39, block-IO cgroup-limited to
8 MiB/s and 150 IOPS on its container only, 1.5M documents (4.2x the
wiredTiger cache), random point lookups by `_id` for 25 seconds at each of
seven concurrency levels — 1 through 64 threads, doubling each step — on a
real connection pool of real OS threads. All three run-validity guards held
(`cacheOversubscription` 4.22, `totalPagesReadIntoCache` 21,944,
`queuedMicrosDelta` zero below the ceiling and clearly non-zero above it).
Full data: `data/observations/swamplink-ticket-probe-2026-08-01.yaml`,
`data/sources/swamplink-ticket-probe-2026-08-01.yaml`.

**The pool climbs, and climbs far past what a smaller smoke run suggested.**
An earlier, shorter smoke run (800k docs, 10s per level, three concurrency
points) had shown `totalTickets` reaching 9 at concurrency 8 and 13 at
concurrency 64 — a modest climb, consistent with "sits near the floor." The
full run, with a complete seven-step ladder and 25 seconds per level, told a
different story:

| Concurrency | 1 | 2 | 4 | 8 | 16 | 32 | 64 |
|---|---|---|---|---|---|---|---|
| `totalTickets` (peak in window) | 4 | 4 | 5 | 7 | 18 | 35 | **74** |
| Mean latency (ms) | 9.2 | 16.9 | 34.0 | 70.1 | 143.8 | 274.9 | 535.5 |
| Throughput (ops/s) | 108.8 | 118.4 | 117.6 | 114.0 | 111.0 | 115.5 | **117.7** |

At concurrency 64 the pool reached 74 tickets — 58% of the documented dynamic
maximum of 128 — and was **still rising** when the 25-second window ended
(29 → 68 over the window, peak 74). It had not converged; sustained demand
would very likely push it higher still. The floor is not a ceiling the
algorithm refuses to leave. Given long enough sustained demand, it leaves it
substantially.

**And it does not matter.** Throughput sat in a 108.8–118.4 ops/s band —
a 9% range — across the entire 64x sweep in offered concurrency. Eighteen and
a half times more tickets and fifty-eight times more latency bought nothing:
the device was rate-limited to 150 IOPS, and 150 IOPS is what the workload
got, regardless of how many operations MongoDB let into the storage engine at
once. This is `throughputProbing` doing exactly what investigation 003
originally worried it might *not* do — climbing — but the climb is not
protective and not diagnostic of anything working correctly. It simply means
more concurrent operations pile up against a device that was never going to
serve them faster, each one now waiting behind more neighbours than before.
A closed-queueing-system sanity check (offered threads ≈ throughput × mean
latency) matched to within 1.5% at every level, which is the measurement
behaving exactly as it should — the flatness is not an artifact of the
harness.

**Little's-law hold time vs. client latency — confirmed in direction, not
yet in magnitude.** The smoke run had inferred that `storage_latency_seconds`
means ticket-*hold* time, not round-trip client latency, because the naive
`predictedCeiling = totalTickets / meanLatency` undershot actual throughput by
~4.8x at its one data point. The full run confirms queue wait is real and
distinct — `queuedMicrosDelta` is exactly zero at concurrency 1, 2 and 4 (no
queueing below the resting pool) and climbs to 40.5s, 67.7s, 80.1s and 302.2s
of cumulative queued time per 25-second window from concurrency 8 upward —
directly measured, not inferred. Subtracting mean queued-time-per-op from
mean client latency puts held time at roughly 80–90% of client latency across
the levels that queued, the *opposite* proportion from the smoke run's
one-point estimate (21% held / 79% queued at its single c=64 sample).

That disagreement is itself informative rather than a contradiction to
paper over: it traces to `totalTickets` never reaching a steady value within
a level at these higher concurrencies (see the table above — start and end
differ by 2-3x at c=16 through c=64). Little's law assumes a stable N over
the interval being measured; this harness's 25-second levels, run back to
back in one continuous `mongod` process so ticket state carries forward, do
not give N time to settle before the level ends. Two different ways of
estimating hold time from this run's own data (subtracting measured queue
time from latency, versus the smoke run's `N / throughput` using the peak N)
disagree with each other by roughly 1.8x, and the latter is not even always
physically possible here — at c=64, `peak tickets / throughput` (74/117.7 =
629 ms) exceeds the 535 ms of latency actually observed, which cannot be
right if that quantity really is a subset of latency. The peak is real; using
it as if it were the level's steady N is what breaks.

**So: the qualitative fix stands, a precise numeric correction factor does
not — yet.** `storage_latency_seconds` is documented as hold time, not
client-observed latency, in the model input (`data/models/mongodb-concurrency.yaml`)
and that correction is low-risk and should be trusted. But no
`validation:` case was added against `predictedCeiling` from this run,
because doing so would compare a formula that assumes steady-state N against
a run where N was still moving — precisely the "comparing the wrong two
quantities" trap this project has already been burned by once (see
`.claude/skills/xy-observe/SKILL.md`). The concrete next step, not yet done:
re-run with each level held long enough for `totalTickets` to visibly
plateau (or discard a warm-up portion of each level before measuring), and
record time-averaged tickets over the window rather than only start/end/max,
so hold time can be computed against a genuinely stable N.

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
- **`mongodb.ticket-throughput-ceiling` is unvalidated.** The 2026-08-01 fault
  injection did drive a MongoDB past its ticket ceiling behind artificially
  slow storage, and throughput did flatten exactly as the model's core claim
  predicts (108.8–118.4 ops/s across a 64x concurrency sweep). But a formal
  point-validation of the model's `predictedCeiling` formula was not published
  from that run, because `totalTickets` never reached a steady value within a
  measurement window at the concurrencies where it mattered — see "Measured
  under load" above. The model has survived one experiment's central claim; it
  has not yet been validated to a number.

---

## The three investigations are one chain

1. **001** — the cache cannot hold the whole database, so misses go to disk.
2. **002** — the disk throttles on the peak second, and the metrics most people
   watch cannot see it.
3. **003** — the throttle converts into a concurrency ceiling, and the queue
   behind it does not drain.

Each was asked as a separate question. They are one failure, and the corpus
now carries it end to end.
