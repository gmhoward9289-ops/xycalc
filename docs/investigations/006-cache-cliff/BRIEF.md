# Investigation 006 — Is the WiredTiger cache cliff a cliff?

**Question as asked:** As the working set grows past the cache, does
throughput degrade smoothly or fall off a knee? If a knee, where — at
1.0× cache, or later?

**Status:** harness in progress (`tools/bench/cache_cliff_probe.py` /
`.sh`). Not yet run.

**Expected confidence ceiling:** `measured` for any knee (or documented
absence) from this benchmark. Absolute ops/s and latency are artifacts of
the toy cache + throttle and are not capacity numbers — only the *shape*
of the curve is portable. Access pattern is uniform random; skewed
workloads are out of scope.

**Scale constraint (issue #9 comment):** working sets of 10–1000 GB are
normal. Oversubscription is the ordinary regime. The sweep therefore
includes **50× and 100×**, not only the 0.5–8× knee neighbourhood. Absolute
cache here is still 0.25 GB — whether the ratio curve transfers to a
200 GB cache is an open extrapolation and must be named in FINDINGS /
`applies_to`, not implied.

---

## Is this the right question?

Yes, with one caveat. Investigation 001's reframe says "size the WORKING
SET, not the database," which silently assumes performance holds up to
that boundary. Nobody has checked whether the boundary is a cliff, a
smooth decline, or already eroding below 1.0×. That is what this answers.

It is *not* the ticket-ceiling question (003) and must not mix that cliff
into the measurement — concurrency stays at 1 for that reason.

---

## Decomposition

| Role | Ask |
|---|---|
| **floor** | Cache-resident working set: when `dataSize ≤ maxCache`, misses should be rare under uniform point lookups. |
| **amplifier** | Oversubscription ratio (`dataSize / maxCache`). Every excess byte is a potential device read under uniform access. |
| **headroom** | Host page cache absorbing WT "misses" — the failure mode that prints a clean "no cliff" table while measuring RAM. |
| **constraint** | Ticket-pool queueing at higher concurrency (ruled out by concurrency=1). Device throttle must bind. |

---

## Do NOT do

- **Do not grow the collection in one long-lived mongod across ratios.**
  Eviction history and page layout carry forward (003's postmortem).
  Fresh container per ratio.
- **Do not hardcode bytes-per-document.** Pilot-batch measure
  `dataSize`, then size against live `maxCache` from `serverStatus`.
- **Do not scale container memory with dataset size.** A fixed 640 MB
  cap is what keeps the host page cache from serving the working set.
- **Do not sweep concurrency.** That is a different cliff (003).
- **Do not trust `pagesReadIntoCache` alone.** Cross-check against
  cgroup device read bytes above 1.0×, or the miss may be host RAM.
- **Do not declare a knee from one sweep.** Two sequential repeats;
  knee must appear at the same ratio in both.

---

## Method (summary)

Harness: `tools/bench/cache_cliff_probe.{py,sh}` forked from
`ticket_probe`. Ratios 0.5, 0.8, 1.0, 1.2, 1.5, 2, 4, 8, **50, 100**×.
Fixed 0.25 GB WT cache, 8 MiB/s / 150 IOPS throttle, 640 MB container
memory, concurrency 1, 25 s per leg. Prefer
`--wiredTigerEngineConfigString="direct_io=[data]"`; fall back and say
so if overlay2 rejects `O_DIRECT`.

Plan: `docs/plans/issue-9-wt-cache-cliff.md`. Issue: #9.
