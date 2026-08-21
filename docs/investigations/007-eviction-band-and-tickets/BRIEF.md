# Investigation 007 — 80% vs 90% cache band, and MongoDB 7 ticket contention

**Question as asked:** What is the performance impact between 80% and 90%
WiredTiger cache occupancy, and how might MongoDB 7 throttling
(`throughputProbing`) cause contention? Educate on the eviction / ticket
variables on the calculator page and in docs, with our own backing data.
Get a `serverStatus` snapshot (cache + tickets + tcmalloc) when we test.

**Status:** education landed; smoke 12s + confirmatory 25s×2 measured on
swamplink 2026-08-21 (see FINDINGS).

**Expected confidence ceiling:**

| Claim | Ceiling |
|---|---|
| Defaults (80 / 95 / 5 / 20) and what each does | `documented` |
| Saturated cache settles near 80% occupancy | `measured` (reef 2026-08-19: **80.55%**) |
| MongoDB 7 ticket pool rests at 4, climbs under load, climb ≠ more ops/s when device binds | `measured` (swamplink ticket_probe 2026-08-01) |
| Ops/s delta between steady ~80% and forced ~90% occupancy | `measured` only after occupancy-band probe lands; until then **uncited / open** |

---

## Is this the right question?

Yes, with a split. "80% vs 90%" is two different experiments:

1. **Occupancy band (read path).** Under a working set that oversubscribes
   the cache, does point-lookup latency/ops change when occupancy sits near
   `eviction_target` (80%) versus when workers are losing and occupancy is
   near 90% (approaching `eviction_trigger` 95%)?
2. **Config knob.** Does raising `eviction_target` from 80 → 90 change
   steady-state behaviour? (Different question; do not conflate.)

MongoDB 7 "throttling" is a third mechanism: adaptive ticket concurrency
(`throughputProbing`), not WiredTiger eviction %. Investigation 003 already
measured it; this investigation's job is to **put that next to the eviction
ladder on the page** so operators do not tune the wrong knob.

---

## Decomposition

| Role | Ask |
|---|---|
| **floor** | Documented targets: total 80%/95%, dirty 5%/20%. |
| **amplifier** | App-thread eviction past trigger; dirty eviction past dirty trigger. |
| **headroom** | Eviction worker pool (hard max 20); ticket pool N on 7.0+ (4–128). |
| **constraint** | Device IOPS; `throughputProbing` can climb N without raising ops/s. |

---

## Do NOT do

- Do not treat "raise eviction workers past 20" as an answer — WiredTiger
  hard-caps at 20.
- Do not use `tcmallocAggressiveMemoryDecommit` as a first lever (vendor:
  large perf penalty).
- Do not mix concurrency sweeps into the occupancy-band probe (that is the
  ticket cliff). Keep concurrency fixed when measuring 80 vs 90 occupancy.
- Do not claim an 80→90 ops delta without a guarded probe that recorded
  occupancy during the window.

---

## Method (occupancy band)

Harness: `tools/bench/occupancy_band_probe.{py,sh}` — fork of
`cache_cliff_probe`, two legs at the same oversubscription ratio, recording
ops/s, latency, occupancy %, dirty %, app-thread eviction rate, tickets,
and tcmalloc heap/allocated during each window.

Leg A: warm to ~80% and measure while workers hold the target.
Leg B: surge / reduced worker effectiveness (or configured higher target) so
the sampled window sits in 88–92%, then measure the same workload.

Guards: device bytes move above 1.0×; `pagesReadIntoCache` non-zero;
concurrency fixed; refuse if occupancy leaves the intended band for >half
the window.

---

## Education surface

- Calculator: constraints + reframe on `mongodb.wt-cache` and
  `mongodb.ticket-throughput-ceiling` name the variables with examples.
- `docs/telemetry/mongodb.md`: ladder table + snapshot recipe.
- This folder's FINDINGS: the narrative with measured numbers.
