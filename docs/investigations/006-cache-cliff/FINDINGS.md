# Investigation 006 — FINDINGS (draft; A1-r2 + A2 in flight)

**Status:** A1 run 1 complete on swamplink-eu (Mongo 7.0.39). A1 run 2 and
A2 transfer queued/running. Not yet imported to the corpus.

**Host:** swamplink-eu, Docker linux 29.7.0, `/dev/sda` throttled
8 MiB/s + 150 IOPS per container, WT cache 0.25 GB, container mem 640m,
concurrency=1, `direct_io=[data]`, fresh mongod per ratio.
`failedDeviceGuards=0` for completed legs.

**100× leg:** failed during load (connection reset / name resolution after
~13.6 GB at 50×). Dropped from the default claim set; 50× stands.

## Short answer (from A1-r1; confirm on A1-r2)

Throughput vs oversubscription is **not** a flat plateau then a cliff at
1.0×. Relative ops/s falls hard already between 0.5× and 1.0×, then the
decline flattens into a shallow log–log slope through 50×.

| ratio | ops/s | pages/op | notes |
|---|---:|---:|---|
| 0.5 | 1590 | 0.001 | near-resident |
| 0.8 | 514 | 0.163 | already ~3× slower |
| 1.0 | 219 | 0.400 | steepest adjacent segment ends here |
| 1.2 | 162 | 0.536 | |
| 1.5 | 128 | 0.678 | |
| 2.0 | 107 | 0.894 | |
| 4.0 | 82 | 1.271 | |
| 8.0 | 73 | 1.640 | |
| 50 | 59 | 2.524 | far oversub; still slow decline |

Adjacent log–log slopes (Δlog ops / Δlog ratio), A1-r1:

| segment | slope |
|---|---:|
| 0.5→0.8 | ≈ −2.4 |
| 0.8→1.0 | ≈ −3.8 (**steepest**) |
| 1.0→1.2 | ≈ −1.7 |
| 1.2→1.5 | ≈ −1.1 |
| 1.5→2 | ≈ −0.6 |
| 2→4 | ≈ −0.4 |
| 4→8 | ≈ −0.2 |
| 8→50 | ≈ −0.1 |

## Claims under test

1. **"There is a cliff."** Not a single discontinuous drop, but there *is*
   a distinctly steeper band below/around 1.0× versus the shallow tail
   above ~2×. Treat as a **steep segment**, not a binary cliff.
2. **"Cache-resident means cache == data" (boundary at exactly 1.0×).**
   **Falsified on this run.** The steepest segment is 0.8→1.0, and 0.5→0.8
   is already steep. Performance erodes well before dataSize exceeds
   maxCache under uniform point lookups.

## A1-r2 (in progress on swamplink-eu)

| ratio | r1 ops/s | r2 ops/s |
|---|---:|---:|
| 0.5 | 1590 | 2189 |
| 0.8 | 514 | 520 |
| 1.0 | 219 | 219 |
| 1.2 | 162 | 158 |
| 1.5 | 128 | 129 |
| 2.0 | 107 | 106 |

Knee region **reproduces**. Absolute 0.5× rate varies; shape from 0.8× up
does not. Legs 4× / 8× / 50× still running; competing `s3_stack` /
`occ-band` containers were stopped mid-run when discovered.

## Transfer (A2) — not yet run

Required before any absolute-size claim: same ratios at WT cache **1.0 GB**
(`PROBE_MEMORY` raised to 2048m so the cache fits; still fixed across
ratios). If R\* / steep-band disagrees, ratio transfer is falsified.

## Weakest inference (named)

Uniform random point lookups maximize miss probability per excess byte.
Skewed working sets may look "cache resident" longer. Do not generalise
the 0.8× erosion to Zipfian traffic without a second sweep.

## Do not promote yet

No `cache.hit_ratio_by_oversubscription` **coefficient** into `mongodb.wt-cache`
until A1-r2 agrees on the steep band through the far ratios and A2 either
confirms transfer or forces `applies_to: absolute-cache-class`.

A1-r1 (+ knee-region A1-r2) **observations** are in the corpus and drive the
site's Cache cliff tab as `provisional` relative-ops shape. That is a chart of
measured legs, not a sizing input.
