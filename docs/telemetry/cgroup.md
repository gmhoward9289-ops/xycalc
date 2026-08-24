# Cgroup memory — page cache vs anonymous RSS

Investigation 009 recorded **neighbor RSS** and **not** this split.
WiredTiger's default 50% of (RAM − 1 GB) only means what the docs say if
the other 50% is still **file-backed page cache charged to mongod's
cgroup**, not anonymous RSS in that same budget, and if a **parent cgroup
or the host** has not reclaimed those file pages.

## Topologies (generic)

These are Linux layouts, not a description of any one estate. Samples must
name which layout they came from; do not mix them.

| Layout | What the 50/50 split sees | Typical failure |
|---|---|---|
| **A.** One cgroup per process, parent uncapped, host has spare RAM | Each child's `memory.max` | 009's shape: neighbor RSS can stay flat; page cache uncontested |
| **B.** Same isolation, parent `memory.max` near the sum of children | Child formula still local; reclaim is hierarchical | Mongo `file` pages dropped while WT occupancy looks fine |
| **C.** Same isolation, host RAM tight, parent unbounded | Host `MemAvailable` is the real ceiling | Same as B at the machine, plus swap if enabled |
| **D.** Several databases in **one** `memory.max` | Every vendor default reads the **same** number | Over-claim, then OOM — not 009 |

Rootless engines (Docker/Podman as a user) are still A–C if each container
has its own cgroup. The extra parent is often the **user session slice**.
That wrap can reclaim `file` pages. It is not, by itself, `EMFILE` / "too
many open files" — that is `nofile`, a different knob.

## What to collect (quiet vs loaded, same host)

One pair of snapshots: idle, then a neighbor-heavy window. Same clock.

**Per child** (`memory.stat`, `memory.max`). **Parent** of that hierarchy
(same fields; is `memory.max` set?). **Host** `MemAvailable` / `Cached` /
`AnonPages`. **Mongo** WT occupancy, `pages read into cache`, app-thread
eviction. **Disk** `iostat` on Mongo's volume.

| Field | Why |
|---|---|
| `anon` | Heap / WT cache / other process RSS |
| `file` | Page cache charged to **this** cgroup |
| `workingset_refault_file` | File pages thrown away and needed again |
| `pgscan` / `pgsteal` | Reclaim (steal without refault can be idle cache) |

## How to read a pair

| Pattern | Reading |
|---|---|
| `anon`↑ `file`↓, refaults flat, iostat quiet | Cache shrinking; not yet proven costly |
| Same + **refaults rising** | Kernel discarded file pages still in the working set |
| Same + WT misses↑ + **iostat r/s / await up** | Extra WT misses are real IOPS |
| `oom` in `memory.events` | Anon won; latency fight already over |
| Neighbor RSS flat (009) | Does **not** rule out B/C — 009 did not contest `file` vs `anon` |

WT occupancy and tickets show Mongo is waiting. They do not name swap vs
page-cache reclaim vs volume burst. RSS/`file`/`anon` and iostat split those.

Status: `obtainable` on Linux with cgroup v2; `work only` if only cAdvisor
RSS/working-set is scraped (working set ≠ `file`).

The estate cAdvisor scrape carries more than working-set: `rss` (anon),
`cache` (file), active/inactive file, `pgmajfault`, PSI, and `failcnt` for
the probe mongo — everything above except `workingset_refault_file` and
`pgscan`/`pgsteal` (majfault rate is the refault-cost proxy). Board:
`/d/xycalc-wt-cgroup` in the estate Grafana xycalc folder.
