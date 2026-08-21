# Investigation 014 — Write rate vs `eviction_dirty_trigger` (T3)

**Short answer:** Under MongoDB **7.0.40** on a Docker cgroup write throttle
(journal off), sustained inserts did **not** drive dirty cache toward the
documented **20%** trigger, and `pages evicted by application threads` stayed
**0**. Peak dirty stayed **~2.5–4%** (near `eviction_dirty_target`). The
documented 20% figure is unchanged; this run did not observe an onset.

**Confidence:** `benchmark` for the dirty-peak observations; `documented` for
checkpoint interval (60s). Mechanism check on synthetic throttle — not an
operational closeness answer.

---

## Question as asked

At what sustained write rate does WiredTiger conscript application threads for
eviction, and is that really at 20% dirty?

## What we measured

Harness: `tools/bench/eviction_probe.{py,sh}`.

| Run | Write cap | Mode | Achieved | dirtyPeak | occPeak | evictΔ |
|---|---|---|---|---|---|---|
| cooper paced | 256 KiB/s | paced 8w | 64 docs/s (=cap) | **2.53%** | 6.3% | **0** |
| cooper flood | 256 KiB/s | unpaced 32w | ~576 docs/s | **2.58%** | 22.1% | **0** |
| reef insert (r4) | 32 MiB/s / 800 IOPS | paced 2–8× | journal=0 | **3.15–4.48%** | **80–82%** | **0** |

Artifact: `artifacts-smoke-256kib-2026-08-21.json`; reef
`tmp-reef-status/r4/t3-eviction-insert.json` →
`data/observations/reef-eviction-insert-2026-08-21.yaml`.

## Reframe

001 cites dirty_trigger 20% as the write-heavy ceiling bulk loads hit first.
On this harness, writers either **self-limit to device write rate** (paced
case: exactly 256 KiB/s) or background eviction **keeps dirty near the 5%
target** while clean pages accumulate in cache (flood case). The binary
signal named in the roadmap — `pages evicted by application threads` —
never moved.

So: **the documented trigger is real documentation; it was not the
operative ceiling on a cgroup-throttled, journal-off insert path.**

## Falsification outcome

Plan §2 bullet 2: trigger never binds at reachable rates because background
eviction / device backpressure absorb dirty bytes first. That is what we saw.
Not a misattribution to the 80%/95% overall triggers on the published cooper
runs (occupancy peaks 6–22%); reef 32 MiB/s smokes did climb occupancy toward
80% still without app-thread dirty eviction.

## Disagreements, unresolved

- **Documented 20%** (`mongodb.eviction-dirty-trigger-pct`, MongoDB 6.0
  WiredTiger tune page) vs **measured onset: absent** on 7.0.40 under this
  harness. No winner declared — different claims (vendor default vs. whether
  this path reaches it).
- Whether a **real NVMe/EBS** write path (or journal=on bulk load) reaches
  20% dirty in production remains open; needs `tracked dirty bytes` telemetry
  from a deployment, not more synthetic throttle knobs alone.

## Weakest inference

That Docker `--device-write-bps` on the data volume is a faithful stand-in for
"disk slower than writers." Achieved paced rate matched the cap exactly, which
is good evidence the throttle bound writers — but Desktop cgroup semantics are
not EBS. Do not read 014 as "bulk loads never hit dirty_trigger."

## Corpus

- Parameter `cache.tracked_dirty_pct_peak`; observations under
  `data/observations/cooper-eviction-probe-2026-08-21.yaml`
- Coefficient `mongodb.checkpoint-interval-seconds` = 60 (`documented`, v7.0)
- Notes on `mongodb.eviction-dirty-trigger-pct` record the absent onset
- Model deferred: no `mongodb.write-rate-ceiling` arithmetic until an onset
  exists; write-path story stays on `mongodb.wt-cache` dirty_ceiling note +
  these observations. **Unvalidated onset (n=0).**

## What would validate next

1. Production or staging series: dirty% and `pages evicted by application
   threads` during a real bulk load.
2. Optional: weaken background eviction via WiredTiger config (lab only) to
   show the 20% mechanism can fire when servers cannot keep up — separate from
   "does it fire under defaults."
