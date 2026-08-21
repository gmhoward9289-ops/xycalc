# Investigation 010 — Compression ratio as a function of data shape (T2)

**Question as asked:** How does the snappy ratio move between high-entropy and
structured documents, and where in that range do real collections sit?

**Status:** complete (2026-08-21). Findings in `FINDINGS.md`. Observations
in `data/observations/swamplink-compression-shape-2026-08-21.yaml`. Band
not rewritten.

**Expected confidence ceiling:** `measured` for synthetic shape × compressor
ratios and for the gzip-proxy axis on those same bytes. Does **not** authorize
narrowing `mongodb.compression-ratio-snappy` (1.5–2.5–3.5, practitioner) —
that needs real collections (#5). A high-entropy floor coefficient is allowed
only if pure-random/snappy lands below 1.5× again.

---

## Is this the right question?

Mostly. The practitioner band is the largest error term in `mongodb.wt-cache`,
and the only in-repo measurement before #5 was 1.42× on random base62 — below
the band. Manufacturing a curve of ratio vs a measurable property of the
*bytes* (gzip -9 self-compression of a raw export) lets a future real
collection place itself without guessing a point in 1.5–3.5.

It is *not* "what is the right snappy mode for production" — do not rewrite
the shipped band from this run alone.

---

## Decomposition

| Role | Ask |
|---|---|
| **floor** | Incompressible documents: ratio approaches ~1× (WiredTiger still pays block/file overhead). |
| **amplifier** | Document shape (entropy, field repetition, cross-doc redundancy) and `block_compressor` (snappy / zstd / zlib). |
| **headroom** | Fixed per-file/extent overhead — dominates if `dataSize` is too small. |
| **constraint** | Do not narrow the practitioner band on synthetic data. `creationString` must confirm the compressor actually took. |

---

## Do NOT do

- **Do not regenerate documents per compressor.** Generate each shape once;
  insert the same batch into three collections. RNG variance must not look
  like a compressor difference.
- **Do not trust `storageSize` before `fsync: 1`.** Pre-checkpoint reads
  inflate every ratio the same way ("everything compresses great").
- **Do not publish without reading back `creationString`.** Identical
  snappy/zstd/zlib rows with a silent default compressor is the #8 failure
  mode for this experiment.
- **Do not proceed if any shape's `dataSize` is under 100 MB.** That measures
  allocation floor, not compressibility.
- **Do not declare shapes distinct without hashing exports.** Two generators
  that hash-equal are a harness bug, not a finding.
- **Do not narrow `mongodb.compression-ratio-snappy` from this run.**

---

## Method (summary)

Harness: `tools/bench/compression_shape_probe.{js,sh,py}` (named apart from
the #5 real-sample `compression_probe.*`).

Five shapes × three compressors, ~300 MB `dataSize` each, one unthrottled
`mongod`. Shapes: `pure-random`, `random-repeated-fields`,
`low-cardinality-enums`, `realistic-mixed` (reuse `mongodb_load.js`),
`near-duplicate`. Gzip -9 proxy on one export per shape. Guards: compressor
string, size floor, export hash uniqueness, proxy rank order.

Falsifies: (a) shape-insensitive narrow cluster, (b) spread wider than
1.5–3.5, or (c) shape-ordered within the band. Proxy may rank without
predicting — report that failure plainly if it happens.
