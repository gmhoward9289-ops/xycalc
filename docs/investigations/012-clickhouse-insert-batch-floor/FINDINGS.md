# Investigation 012 — FINDINGS

**Question.** At what insert frequency does part count outrun merges, so
inserts are first delayed and then rejected?

**Status.** Dual-image probe completed 2026-08-21 on the Cursor cloud agent
VM (Docker vfs driver; 2 vCPU / 2 GiB containers). Threshold coefficients
confirmed live. Measured inserts/sec floor is hardware- and merge-regime
specific — see below. Model `clickhouse.parts-insert-ceiling` remains
`unvalidated (n=0)` against production observations; the probe confirms the
*settings*, not a portable frequency number.

Artifact: `artifacts/clickhouse_probe_dual.json` (thresholds).
Write/read under load: `artifacts/clickhouse_probe_rw_dual.json`.

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

## Write vs read latency under load (Mongo-comparable keys)

Second dual-image run with `PROBE_READERS=4` concurrent point lookups while
inserts run. Step JSON carries nested `write` / `read` blocks with the same
keys as Mongo `ticket_probe` / `occupancy_band_probe`: `opsPerSecond`,
`meanLatencyMs`, `p50LatencyMs`, `p95LatencyMs`, `p99LatencyMs`.

**24.8, merges stopped, 4000 rows:**

| batch | write ops/s | write p99 | read ops/s | read p99 |
|---|---|---|---|---|
| 1 | 42 | **1005 ms** | 19 | **468 ms** |
| 10 | 289 | 236 ms | 74 | 265 ms |
| 100 | 115 | 96 ms | 38 | 57 ms |
| 1000 | 11 | 61 ms | 11 | 10 ms |
| 10000 | 3 | 38 ms | 17 | 9 ms |

**23.3 batch=1:** write p99 **1003 ms**, read p99 **59 ms** (reads stay cheap
while inserts sleep on the delay path). On 24.8 batch=1, reads also degrade
(468 ms) — more active parts (~3000 vs ~300) under the same reader pool.

Interpretation for Mongo compare:

- CH write p99 ≈1000 ms during delay is **`max_delay_to_insert` sleep**, not
  disk latency. Do not line that up against Mongo storage-stall read p99.
- CH read p99 under part pressure is the fairer analogue to Mongo
  `ticket_probe` / `occupancy_band_probe` read latency under load.
- Absolute ops/s remain harness- and hardware-scoped (2 vCPU / 2 GiB,
  `merges_stopped=true`).

---

## Claim B — 23.6 moves the crossover ~10× (survives)

Throw engaged at ~300 parts on 23.3 and ~3000 on 24.8 — matching the live
`system.merge_tree_settings` values and the code-graded coefficients. Guard 6
passed (`settingsDiffer: true`).

---

## Merges-running caveat (important)

With merges **left on continuously** (`merge_duty_cycle=1`) on this Cursor
cloud agent VM, batch=1 never approaches the pre-23.6 delay threshold of 150 —
even after deliberate slow-storage attempts:

| Attempt | Peak active parts | Guard 3 |
|---|---|---|
| Continuous merges, fast vfs/overlay (earlier) | ~19 | refuse |
| `background_pool_size=2`, 1 CPU | 41 | refuse |
| `/dev/vdb` cgroup throttle 512 KiB/s · 20 IOPS | 7 | refuse |
| loop0 throttle 20 IOPS + periodic `drop_caches` | 15 | refuse |

Tiny single-row parts merge faster than writers can pile them up on this box.
Artifact: `artifacts/clickhouse_probe_merges_on_continuous_negative.json`.

The dual-image threshold run therefore used `SYSTEM STOP MERGES`
(`merges_stopped: true`) to isolate the part-count ceilings. That answers
"do the thresholds exist and did they move 10×?" — not "will merges lose on
every 2 vCPU box." Absolute inserts/sec floors must not be published as
portable coefficients from the STOP MERGES run.

### Deliberate merge-throttle (guard 3 without permanent STOP)

Harness knob `PROBE_MERGE_DUTY_CYCLE` (plus optional
`PROBE_BACKGROUND_POOL_SIZE`, block-IO throttle, `PROBE_DATA_DIR`) duty-cycles
`SYSTEM START/STOP MERGES`. With **duty=0.05** / period 2s on 23.3, merges
still run some of the time (`merges_stopped: false`) and guard 3 **passes**:

| batch | peak parts | crossed delay (150) | write p99 |
|---|---|---|---|
| 1 | **242** | yes | ~617 ms |
| 10 | 265 | yes | ~779 ms |
| 100 | 25 | no | ~69 ms |

Artifact: `artifacts/clickhouse_probe_merges_on_duty.json`. Claim A shape
survives under partial merge allowance. This is **not** a portable
inserts/sec floor — `applies_to` would need the duty cycle + box; continuous
merges on real slow disk (reef / EC2) remain the unpaid infra next step if
we want a hardware-scoped `benchmark` coefficient.

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

Whether a given production loader will hit these ceilings **with continuous
merges** on its hardware. Thresholds and the 23.6 jump are proven; continuous
merges on this VM do not lose; duty-cycled merges do. A `benchmark`-graded
floor still wants continuous merges on named slower storage.

---

## What would validate further

1. Import dual-probe settings rows as observations of the threshold parameters
   (optional — already code-cited).
2. Concurrent read latency under insert load — **done** (`write`/`read`
   blocks in `clickhouse_probe_rw_dual.json`; see FINDINGS).
3. Merges-on without permanent STOP MERGES — **done** via duty-cycle 0.05
   (`clickhouse_probe_merges_on_duty.json`). Continuous merges on real slow
   disk still open if we spend infra $.
4. Optional: same duty-cycle sweep on 24.8 to cross the 1000-part delay line.
