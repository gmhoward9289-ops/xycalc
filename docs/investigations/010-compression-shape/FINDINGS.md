# Investigation 010 — Compression ratio as a function of data shape (T2)

**Status:** complete (2026-08-21). Full sweep on swamplink-eu,
MongoDB **7.0.39**, ~300 MB raw JSONL per shape × snappy/zstd/zlib;
**replicated on reef** the same day (`mongo:7`, work on V:).
Harness: `tools/bench/compression_shape_probe.{py,sh}` (+
`tools/bench/reef_run_t2_shape.ps1` for the Windows/Docker path).
Artifacts under `artifacts/`. Observations:
`data/observations/swamplink-compression-shape-2026-08-21.yaml`,
`data/observations/reef-compression-shape-2026-08-21.yaml`.

**Short answer.** Shape moves the snappy ratio a lot — **wider than the
shipped 1.5–3.5 band**. Measured snappy `dataSize/storageSize`:

| Shape | Snappy | Zstd | Zlib | gzip-9 proxy |
|---|---:|---:|---:|---:|
| pure-random | 0.990 | 1.230 | 1.244 | 1.338 |
| random-repeated-fields | 0.988 | 1.327 | 1.366 | 1.384 |
| realistic-mixed | 1.453 | 1.900 | 1.914 | 2.122 |
| low-cardinality-enums | 3.832 | 6.854 | 9.001 | 33.18 |
| near-duplicate | 9.172 | 27.41 | 13.83 | 57.74 |

Verdict: **`wider-than-band`**. Snappy span **0.99–9.17**. The practitioner
band is too narrow at both ends for synthetic extremes; it is still a
reasonable prior for *application-shaped* data (see #5 samples 1.73–3.49).
**Band not rewritten** — synthetic extremes are not a population.

---

## Is this the right question?

Yes. The band was the largest error term in `mongodb.wt-cache`. Manufacturing
the curve answers whether shape predicts ratio and how wide honest advice
must be. Placing *real* collections on the curve remains #5 / Track B.

---

## Falsification outcome

From the plan's three outcomes:

1. **Insensitive to shape** — falsified. Snappy spread ≈ 8.2×.
2. **Wider than the current band** — **confirmed.** Low end below 1.5
   (high-entropy ≈ 1.0; WT overhead can push slightly *under* 1.0 because
   `storageSize` includes allocation that `dataSize` does not). High end
   far above 3.5 on near-duplicate / enum-heavy shapes.
3. **Within band, shape-ordered** — not reached.

Gzip-proxy **rank** matched the expected order
(near-duplicate > enums > mixed > repeated-fields > pure-random). Absolute
proxy values do **not** equal snappy ratios (gzip -9 is much stronger on
redundant shapes). Proxy→snappy **order** matched on the smoke (20 MB) run;
on the full run the two high-entropy shapes swapped (0.990 vs 0.988) —
noise at the overhead floor, not a ranking failure for compressible
shapes. Useful placement rule: gzip-proxy ≳ 2 tracks “at least
application-like”; proxy ≈ 1.3–1.4 is the incompressible floor.

---

## Guards

All 15 cells: `creationString` contained the expected
`block_compressor=…`. Every shape `dataSize` ≫ 100 MB floor. Export
sha256 pairwise distinct. No silent “three compressors, one number” table.

---

## Weakest inference (named)

That gzip-9 on a JSONL sample is a *placement* axis for production BSON
on disk. It ranked shapes correctly here; absolute proxy≫snappy on
redundant data, so do not read proxy as a snappy forecast. Real
collections (#5) still have to sit on the curve from their own
`dataSize/storageSize`, with proxy as a coarse prior only.

---

## Corpus impact

- Observations landed (ratios + sizes + gzip-proxy parameter
  `storage.self_compression_proxy_ratio`).
- **`mongodb.compression-ratio-snappy` unchanged** (1.5–2.5–3.5,
  practitioner). Notes updated to cite this sweep.
- **No high-entropy floor coefficient shipped.** Measured ~0.99 would be
  the wrong number to multiply through the decompression term (it is
  overhead, not expansion). Treat high-entropy as **≈1.0×** and prefer
  `db.stats()`.

---

## Replication — reef (2026-08-21)

Same harness on **reef** (`owner@192.168.68.20`), Docker `mongo:7`, work
dirs on **V:** (WD BLACK SN770). Artifacts:
`V:\xycalc-results\compression-shape\shape-sweep.json` and
`docs/investigations/010-compression-shape/artifacts/reef-shape-sweep.json`.
Observations: `data/observations/reef-compression-shape-2026-08-21.yaml`.

| Shape | Snappy (reef) | Snappy (swamplink) | Δ |
|---|---:|---:|---:|
| pure-random | 0.9905 | 0.990 | ~0 |
| random-repeated-fields | 0.9873 | 0.988 | ~0 |
| realistic-mixed | 1.4321 | 1.453 | −0.02 |
| low-cardinality-enums | 3.9089 | 3.832 | +0.08 |
| near-duplicate | 9.2203 | 9.172 | +0.05 |

Verdict again **`wider-than-band`** (0.99–9.22). Gzip-proxy rank matched;
proxy→snappy order still flips only the two floor shapes. Confirms the
curve is not a one-host artifact.

---

## What would validate further

1. Place #5 / production `db.stats()` points on the gzip-proxy axis.
2. One more absolute size (e.g. 1 GB raw) for the three middle shapes if
   anyone claims transfer of absolute ratios — shape *order* already
   transferred smoke→full.
