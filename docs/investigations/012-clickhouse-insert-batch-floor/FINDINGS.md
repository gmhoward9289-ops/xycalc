# Investigation 012 — FINDINGS

**Question.** At what insert frequency does part count outrun merges, so
inserts are first delayed and then rejected?

**Status.** Dual-image probe completed 2026-08-21 on the Cursor cloud agent
VM (Docker vfs driver; 2 vCPU / 2 GiB containers). Threshold coefficients
confirmed live. Measured inserts/sec floor is hardware- and merge-regime
specific — see below. Model `clickhouse.parts-insert-ceiling` remains
`unvalidated (n=0)` against production observations; the probe confirms the
*settings*, not a portable frequency number.

Artifact: `artifacts/clickhouse_probe_dual.json`.

---

## Short answer

| Image | Live `parts_to_delay_insert` | Live `parts_to_throw_insert` | batch=1 peak parts | Rejected? |
|---|---|---|---|---|
| `clickhouse/clickhouse-server:23.3` | **150** | **300** | 315 | yes — `Too many parts (30x)` |
| `clickhouse/clickhouse-server:24.8` | **1000** | **3000** | 3015 | yes — `Too many parts (3001)` |

The documented ~10× jump is real on running servers, not folklore. Same
fixed row budget (4000), same writers (16), merges stopped to isolate the
ceiling: 23.3 rejects near 300 parts; 24.8 near 3000.

Delay is also real: `system.events.DelayedInserts` incremented (165 on 23.3,
2014 on 24.8 for batch=1), and client p99 rose to ~988 ms (the 1s
`max_delay_to_insert` cap).

---

## Claim A — frequency, not volume (survives)

Fixed 4000 rows, merges stopped, 24.8:

| batch_size | peak active parts | rejects |
|---|---|---|
| 1 | 3015 | 985 |
| 10 | 400 | 0 |
| 100 | 40 | 0 |
| 1000 | 4 | 0 |
| 10000 | 1 | 0 |

Same bytes in; part count tracks **insert statements**, not row volume.
Streaming (batch=1) hits the ceiling; batched inserts at the same total rows
do not. Folklore confirmed under this controlled regime.

---

## Claim B — 23.6 moves the crossover ~10× (survives)

Throw engaged at ~300 parts on 23.3 and ~3000 on 24.8 — matching the live
`system.merge_tree_settings` values and the code-graded coefficients. Guard 6
passed (`settingsDiffer: true`).

---

## Merges-running caveat (important)

With merges **left on** on this 2 vCPU box, batch=1 of 50k rows peaked at
**~19** active parts — never approached even the pre-23.6 delay threshold of
150. The harness correctly REFUSED TO CONCLUDE in that mode.

So: on this hardware, background merges keep up with tiny single-row inserts.
The dual-image run used `SYSTEM STOP MERGES` (`merges_stopped: true` in JSON)
to isolate the part-count ceilings. That answers "do the thresholds exist and
did they move 10×?" — not "will merges lose on every 2 vCPU box." Production
hit rates still depend on merge throughput vs insert rate (CPU, disk, part
size). Absolute inserts/sec floors must not be published as portable
coefficients from this run.

---

## Scale (1 GB vs 1 TB)

Unchanged from the model reframe: total table bytes are the wrong input.
`max_avg_part_size_for_too_many_parts` was 10 GiB on 23.3 and 1 GiB on 24.8
live. Probe parts stayed ~163–323 B average — deep in the regime where the
count ceilings bind.

---

## Confirmed telemetry series

Live on 23.3 / 24.8 (names not assumed — observed in `event_deltas`):

- `DelayedInserts`, `DelayedInsertsMilliseconds`
- `RejectedInserts`, `FailedInsertQuery`
- `InsertedCompactParts`, `InsertedRows`

---

## Weakest inference

Whether a given production loader will hit these ceilings **with merges
running** on its hardware. This run proves the thresholds and the version
jump; it does not prove a universal inserts/sec floor. Next measurement that
would tighten that: same harness with `PROBE_STOP_MERGES=0` on a box where
disk/CPU are slow enough that batch=1 actually outruns merges (or a deliberate
merge-throttle), then land a `benchmark`-graded floor with
`applies_to` naming that box.

---

## What would validate further

1. Import dual-probe settings rows as observations of the threshold parameters
   (optional — already code-cited).
2. Concurrent read latency under insert load (Mongo-comparable keys partially
   present for writes: `meanLatencyMs` / `p95LatencyMs` / `opsPerSecond`).
3. One merges-on run on slower storage where guard 3 passes without STOP MERGES.
