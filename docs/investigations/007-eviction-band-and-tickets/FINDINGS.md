# Findings — 80% vs 90% eviction target, and MongoDB 7 ticket contention

**Investigated:** 2026-08-21 · **Models:** `mongodb.wt-cache`,
`mongodb.ticket-throughput-ceiling` · **Harness:**
`tools/bench/occupancy_band_probe.sh` on swamplink (MongoDB **7.0.39**,
direct_io=[data], 0.25 GB WT cache, 640 MB cgroup, 8 MiB/s / 150 IOPS,
2.0× oversubscription, concurrency **1**).

**Validation status:** one 12 s smoke + two sequential 25 s confirmatory
passes (`n=1` host). Absolute ops/s are throttle artifacts — only the
cross-leg comparison and the snapshots are the finding. Ticket contention
numbers below restate investigation 003 (already in the corpus).

---

## The short answer

Raising `eviction_target` from **80 → 90** under this read-miss workload
**reliably holds the cache fuller** (mean occupancy ~78% → ~87–88% across
the 25 s passes). App-thread eviction stayed **0**; tickets stayed at the
7.0 idle floor of **4**.

Ops/s is a smaller, noisier effect on this throttle: a 12 s smoke was
essentially flat (−1%); two confirmatory 25 s passes showed **+6.7%** and
**+13.1%** for target=90 vs 80. Same direction twice at 25 s, but still a
single host and a miss-bound toy cache — **not** a reason to raise the
production target to 90 for capacity. The documented danger remains **95%**
(app threads conscripted) and, on MongoDB 7 under real concurrency,
**ticket climb against a saturated device**.

---

## Eviction variables (what they are, how to use them)

| Variable | Default | What it does | Example use |
|---|---|---|---|
| `eviction_target` | 80% | Occupancy WT *works to hold* | Size cache ≈ working_set / 0.8 so workers are not always fighting. Reef 2026-08-19 saturated scan settled at **80.55%**. |
| `eviction_trigger` | 95% | App threads do eviction | Diagnose latency with `pages evicted by application threads`, not RSS. |
| `eviction_dirty_target` / `_trigger` | 5% / 20% | Dirty-page analogues | Bulk load: dirty% can hit 20 while total occupancy is still low. |
| `eviction=(threads_min,threads_max)` | up to **20** hard max | Background eviction pool | **20/20 and still losing** → not a knob problem; IOPS / write rate / working set. |
| `tcmallocReleaseRate` | (runtime) | Return free pages to OS | Shrink `heap_size − allocated` gap. Prefer over aggressive decommit. |
| `tcmallocAggressiveMemoryDecommit` | off | Aggressive return to OS | Vendor: large perf penalty — last resort. |
| `concurrentTransactions.*.totalTickets` (7.0+) | dynamic 4–128 | Admission control | Read it live; idle often **4**. Climbing tickets ≠ more ops/s if the device binds. |

**Example — operator playbook**

1. Occupancy stuck mid-80s + `unable to reach eviction goal` rising → danger
   band before 95%; check disk and dirty%.
2. Workers 20/20, app-evict rising → raise IOPS or shrink working set; do not
   expect threads_max > 20.
3. High RSS, healthy occupancy, large tcmalloc gap → release rate, not
   cache size.
4. Flat ops/s, rising latency, climbing `totalTickets` on 7.0 → storage
   admission contention (003), not "need more tickets."

---

## Backing data — occupancy band

Artifacts:

- smoke 12 s: `occ-band-2026-08-21.json`
- confirm 25 s ×2: `occ-band-run1.json`, `occ-band-run2.json`

Device-byte guards **passed** on every leg.

| Pass | ops/s 80 | ops/s 90 | Δ ops | occ mean 80 | occ mean 90 |
|---|---|---|---|---|---|
| smoke 12 s | 103.6 | 102.6 | −0.97% | 78.25% | 83.9% |
| confirm 25 s #1 | 104.0 | 111.0 | **+6.73%** | 78.25% | 87.85% |
| confirm 25 s #2 | 104.3 | 118.0 | **+13.14%** | 78.46% | 86.71% |

Shared across all legs: app-thread evictions **0**, tickets **4→4**,
`direct_io=true`, oversubscription **2.0×**.

**Confirm #1 snapshot detail (25 s)**

| | target=80 | target=90 |
|---|---|---|
| mean latency (ms) | 9.61 | 9.01 |
| occupancy end % | 75.22 | 91.35 |
| dirty mean % | 1.47 | 1.02 |
| worker eviction Δ | 2190 | 1963 |
| tcmalloc frag % of heap (after) | 12.47% | 5.75% |

**Weakest inference (named):** single host, concurrency 1, 0.25 GB cache,
device-throttled. The ops/s delta moved from ~0 (12 s) to mid-single-digit /
low-teens (25 s) — window length matters here. Do **not** promote a
"raise target to 90 for +X% throughput" coefficient. The portable claim is:
target controls how full the cache sits; 95% and ticket/device contention
are still the failure modes that hurt.

---

## Backing data — MongoDB 7 throttling / contention (003, restated)

swamplink ticket_probe 2026-08-01, same class of throttle, concurrency
ladder 1…64:

| Concurrency | peak `totalTickets` | ops/s | mean latency (ms) |
|---|---|---|---|
| 1 | 4 | 108.8 | 9.2 |
| 8 | 7 | 114.0 | 70.1 |
| 64 | **74** | **117.7** | **535.5** |

Throughput flat (~9% range) while tickets climbed ~18× and latency ~58×.
That is how 7.0 `throughputProbing` causes contention: more operations
admitted into a storage engine waiting on a device that will not go faster.

---

## Calculator / docs surface

- `mongodb.wt-cache` constraints + `notes`: eviction ladder and examples.
- `mongodb.ticket-throughput-ceiling` reframe: 7.0 climb-without-ops
  paragraph with the measured table's conclusion.
- `docs/telemetry/mongodb.md`: snapshot recipe including tcmalloc + occupancy.
- Harness: `tools/bench/occupancy_band_probe.{py,sh}`.

---

## Open

1. Write-path dirty-trigger onset (roadmap T3 / issue #11) — different cliff.
2. Occupancy band at concurrency >1 with ticket queueing allowed — then the
   two mechanisms can interact; keep them labeled separately in the table.
3. Second host / larger absolute cache before any transfer claim on the ops
   delta magnitude.
