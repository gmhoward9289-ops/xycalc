# Findings — WiredTiger cache cliff (T1 / #9)

**Investigated:** 2026-08-21 · **Harness:** `tools/bench/cache_cliff_probe.{py,sh}`
on swamplink-eu · **MongoDB 7.0.39**, `direct_io=[data]`, concurrency **1**,
fresh mongod per ratio, device throttle **8 MiB/s / 150 IOPS**,
`failedDeviceGuards=0` on every completed leg.

**Artifacts:** `artifacts/a1-r1.json`, `a1-r2.json`, `a2-transfer.json`.

**Validation status:** shape claim is **measured** (two A1 repeats + one A2
transfer). Absolute ops/s remain throttle artifacts — not capacity numbers.
No `mongodb.wt-cache` sizing coefficient promoted from this run.

---

## The short answer

Throughput vs oversubscription is **not** a flat plateau then a cliff at
1.0×. Relative ops/s falls hard already between 0.5× and 1.0× (steepest
adjacent segment **0.8→1.0** on every sweep), then the decline flattens into
a shallow log–log slope through 50×.

That shape **reproduced** on A1-r2 (same 0.25 GB cache) and **transferred**
to a 1.0 GB cache on A2 (knee ratios 0.5…2.0). Absolute 0.5× rates vary;
shape from ~0.8× upward does not.

| Claim | Verdict |
|---|---|
| "There is a cliff at 1.0×" | **Falsified as a single discontinuity.** There *is* a distinctly steeper band below/around 1.0× versus the shallow tail above ~2×. Treat as a **steep segment**, not a binary cliff. |
| "Cache-resident means cache == data" (boundary exactly at 1.0×) | **Falsified.** Performance erodes well before `dataSize` exceeds `maxCache` under uniform point lookups. |
| Ratio shape is an artifact of the 0.25 GB toy cache | **Not supported by A2.** Same steep band at 1.0 GB cache. |

---

## A1 — fixed 0.25 GB cache, two sequential sweeps

Container mem **640m**, WT cache **0.25 GB**. Ratios 0.5…50× (100× failed
during load on r1; dropped from the claim set).

| ratio | r1 ops/s | r2 ops/s | r1 pages/op | r2 pages/op |
|---|---:|---:|---:|---:|
| 0.5 | 1590 | 2189 | 0.001 | 0.002 |
| 0.8 | 514 | 520 | 0.163 | 0.141 |
| 1.0 | 219 | 219 | 0.400 | 0.404 |
| 1.2 | 162 | 158 | 0.536 | 0.589 |
| 1.5 | 128 | 129 | 0.678 | 0.724 |
| 2.0 | 107 | 106 | 0.894 | 0.875 |
| 4.0 | 82 | 82 | 1.271 | 1.273 |
| 8.0 | 73 | 74 | 1.640 | 1.660 |
| 50 | 59 | 56 | 2.524 | 2.698 |

Adjacent log–log slopes (Δlog ops / Δlog ratio):

| segment | r1 | r2 |
|---|---:|---:|
| 0.5→0.8 | −2.4 | −3.1 |
| 0.8→1.0 | **−3.8** | **−3.9** |
| 1.0→1.2 | −1.6 | −1.8 |
| 1.2→1.5 | −1.1 | −0.9 |
| 1.5→2 | −0.6 | −0.7 |
| 2→4 | −0.4 | −0.4 |
| 4→8 | −0.2 | −0.2 |
| 8→50 | −0.1 | −0.2 |

---

## A2 — transfer at 1.0 GB cache

Same harness; WT cache **1.0 GB**, container mem raised so the cache fits
(`PROBE_MEMORY` 2048m), ratios **0.5…2.0** only (knee region).

| ratio | ops/s | pages/op | slope vs prior |
|---|---:|---:|---:|
| 0.5 | 1810 | 0.004 | — |
| 0.8 | 503 | 0.191 | −2.7 |
| 1.0 | 215 | 0.532 | **−3.8** |
| 1.2 | 151 | 0.741 | −1.9 |
| 1.5 | 119 | 1.015 | −1.1 |
| 2.0 | 102 | 1.263 | −0.6 |

Steepest segment again **0.8→1.0** at −3.8 — matches A1 within noise.
Ratio transfer for the *shape* holds at this absolute-cache step. Far
oversub (4×…50×) at 1 GB was not re-run; the shallow-tail claim for those
ratios still rests on A1 only.

---

## Weakest inference (named)

Uniform random point lookups maximize miss probability per excess byte.
Skewed / Zipfian working sets may look "cache resident" longer. Do not
generalise the 0.8× erosion to skewed traffic without a second sweep.

Absolute cache here is still ≤1 GB. Whether the same steep band appears at
a 200 GB cache is an open extrapolation — name it in `applies_to`, do not
imply it.

---

## What the corpus gets / does not get

- **Gets:** observations for A1-r1/r2 ops + pages/op and A2 knee ops
  (`data/observations/swamplink-cache-cliff-2026-08-21.yaml`). Calculator
  Cache cliff tab can treat the relative-ops shape as **measured**, not
  provisional.
- **Does not get:** a `cache.hit_ratio_by_oversubscription` **sizing**
  coefficient in `mongodb.wt-cache`. Relative ops under a toy throttle are
  not a hit-ratio you multiply into a GB answer. Promote a coefficient only
  after a deliberate hit-ratio / miss-cost model exists.

---

## Harness notes

- Fresh mongod per ratio (no carry-forward eviction history).
- Device-byte guards passed; `direct_io=[data]` on.
- Tickets stayed at the MongoDB 7 idle floor of 4 — this is not the
  ticket cliff (003).
