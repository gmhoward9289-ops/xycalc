# Findings — the WT/page-cache split is a cgroup contest, and anon wins

**Investigated:** 2026-08-24 · **Harness:** `tools/bench/celery_probe/` on
swamplink (MongoDB **7.0** in Docker, 640 MiB cgroup limit, no swap,
**256 MiB** WT cache, **1.1 GB** data in `ticketprobe.docs` (1.5 M docs),
blkio 8 MiB/s / 150 IOPS on the data device). Metrics: percona
mongodb_exporter **0.47.1**, cAdvisor **v0.55**, cgroup v2 with PSI.
Boards: estate Grafana `/d/xycalc-wt-cgroup` (source
[`wt-vs-cgroup-split.json`](../../../deploy/grafana/dashboards/wt-vs-cgroup-split.json)).

**Validation status:** one host, one container, two load windows on one day.
Window 1 is a plain read workload; window 2 adds a deliberately hostile anon
allocator. The numbers are throttle-shaped and probe-specific; the sequence
and the observability gaps are the finding.

---

## The theory

WiredTiger's default cache — 50% of (RAM − 1 GB) — is not a size, it is a
**split**: WT holds decompressed pages in its own (anonymous) cache and
counts on the *other* half of memory holding the compressed data files in
the kernel's file page cache. A WT cache miss is designed to be a
RAM-speed page-cache hit, not a disk read. [1][2]

Inside a memory cgroup, both halves are charged to the **same**
`memory.max`, and nothing enforces the split. File pages are reclaimable;
anonymous memory (with no swap) is not. So when anything grows anon —
connections, sorts, aggregations, the WT cache itself — the kernel balances
the budget by discarding file pages. [3] The split degrades in a fixed
sequence:

1. **Cheap phase.** Inactive file pages are dropped; nothing read them
   recently; no cost.
2. **Squeeze.** Active file pages — the compressed blocks WT misses land
   on — are discarded. WT misses become device reads. On a throttled
   volume each miss now queues; latency rises. WT occupancy metrics stay
   normal throughout, because WT's own cache is fine.
3. **Kill.** If anon keeps growing, reclaim runs out of file pages to
   feed it, and the OOM killer ends the process. There is no graceful
   degradation step between 2 and 3 — reclaim *is* the graceful step, and
   it was spent defending anon.

Corollary (the observability gap): the failure lives between two
dashboards. WT metrics can't see it (their cache is healthy) and a host
dashboard can't see it (the contest is inside one cgroup on a mostly-idle
box). Only cgroup-level series — `memory.stat` splits, PSI — witness it.

## Predictions and what happened

| # | Prediction | Observed | Proof |
|---|---|---|---|
| P1 | Under load, anon grows and file cache shrinks while the stack stays at `memory.max`; WT occupancy stays unremarkable | W1: anon +63 MiB, file −24 MiB at 62→72% of limit; W2: anon 325→603 MiB, file 269→**27 MiB**; WT occupancy ~75% both times | replay links below; board panels 1 and 5 |
| P2 | Inactive file drains before active file | W1 took only inactive (−23.6 MiB; active 63.6→63.1). W2 exhausted inactive, then cut active to **22.7 MiB** | panel 2 |
| P3 | The cost appears as read() I/O against the throttle, **not** major faults (WT uses pread, not mmap) | majfault/s ≈ 0 in both windows while device reads pinned at 7.4–8 MiB/s (the blkio cap) | panel 3 |
| P4 | PSI records the stall while host metrics stay bland | W1: cgroup io-some **81%**; W2 pre-kill: host table below | panel 4 |
| P5 | Sustained anon growth ends in OOM kill, not a steady state | W2: healthy → OOM-killed in **74 s** (19:44:26Z, exit 137, `OOMKilled=true`); data intact on restart (1.5 M docs verified) | replay 2, death gap in every series |

## The two windows

**Window 1 — read load only** (2026-08-24 19:23:22–19:32:19Z; Celery
100/200/400 tasks/s × 120 s). The squeeze reached the cheap phase only:
inactive file paid, active survived. But the ticket pool saturated (7–8 of
8 read tickets out, 6,500-task backlog, 9.3 s cumulative queue wait) and
reads pinned the throttle with 81% io-stall — WT-cache-shortage pain,
running ahead of the memory contest.

**Window 2 — read load + anon allocator** (19:43:12Z; two concurrent
`$group`/`$addToSet` aggregations, ~100 MB server memory each, on top of
400/s reads). The full sequence in 74 seconds: anon 325→603 MiB, file
269→27 MiB, active file cut to 23 MiB, OOM kill at 19:44:26Z. Recovery
after restart was itself throttled — the cold cache had to be re-read at
8 MiB/s while the worker replayed its backlog.

Replays (estate Grafana, swamp-id sign-in; also pinned on the board's
"Captured load windows" panel):

- Window 1, before/during/after:
  `/d/xycalc-wt-cgroup?from=1787598480000&to=1787600880000`
- Window 2, squeeze → OOM → recovery:
  `/d/xycalc-wt-cgroup?from=1787600100000&to=1787601120000`

## What a normal node dashboard showed

Same host, same clock, quiet baseline (18:55Z) vs the last minute before
the OOM kill (19:44:20Z), from node_exporter:

| Host signal | Quiet | Pre-OOM | Would it alarm? |
|---|---|---|---|
| CPU busy | 10% | 26% | no |
| Memory used | 44% | 49% | no |
| MemAvailable | 4.3 GiB | 3.8 GiB | no |
| Disk reads | 0 | 5.6 MiB/s | no — trivial for any real disk |
| load1 | 0.2 | 5.6 | ambiguous at best |
| PSI memory some | 0% | 11% | the one honest signal |
| PSI io some | 0% | **92%** | the other one |

A database died of memory starvation on a host that never dropped below
3.8 GiB free. The only host-level series that told the truth were the PSI
pair — which most stock node dashboards do not chart. Everything else read
as a healthy machine with a hint of activity.

## What would falsify this

- A cgroup v2 host where sustained anon growth does **not** strip active
  file pages before OOM (would contradict the reclaim ordering claim [3]).
- A workload where the squeeze's cost appears as majfaults rather than
  read() I/O on mongod (would mean WT maps files after all; it should not
  on any supported configuration).
- WT occupancy or dirty ratio moving distinctively during the squeeze
  (would close the observability gap from the WT side; not observed).

## Operator playbook

- Size the **cgroup**, not the WT cache: budget WT cache + working anon
  (connections, sorts, aggregations) + the file-cache share that keeps WT
  misses off the device. The 50/50 default assumes the file half exists.
- Chart `memory.stat` splits (anon / file / active_file / inactive_file)
  and PSI per cgroup. Working-set alone hides the contest; host memory
  hides everything.
- Treat active-file decline as the early warning, and read()-bytes against
  the volume ceiling as the cost meter. Ignore majfaults for mongod.
- No swap means step 3 is a kill, not a slowdown. That is a choice; make
  it knowingly.

## References

[1] MongoDB docs — WiredTiger memory use: the filesystem cache serves
    compressed blocks; default cache is 50% of (RAM − 1 GB).
    https://www.mongodb.com/docs/manual/core/wiredtiger/#memory-use
[2] WiredTiger tuning — cache size guidance and the role of OS cache.
    https://source.wiredtiger.com/develop/tune_cache.html
[3] Linux cgroup v2 memory controller — reclaim, `memory.stat` (anon /
    file / active_file / inactive_file), `memory.events` oom_kill.
    https://docs.kernel.org/admin-guide/cgroup-v2.html#memory
[4] PSI — pressure stall information (`some` / `full` semantics).
    https://docs.kernel.org/accounting/psi.html
[5] Percona mongodb_exporter (0.47.x series names verified live; see
    [`deploy/grafana/recording_rules.yml`](../../../deploy/grafana/recording_rules.yml)).
    https://github.com/percona/mongodb_exporter
[6] Prior art in this repo: investigation 001 (WT cache sizing), 003
    (storage stall → query collapse), 007 (eviction band and tickets), and
    [`docs/telemetry/cgroup.md`](../../telemetry/cgroup.md) — the layout
    table and reading rules this investigation instrumented.
[7] Porting the board elsewhere:
    [`deploy/grafana/PORTING.md`](../../../deploy/grafana/PORTING.md).
