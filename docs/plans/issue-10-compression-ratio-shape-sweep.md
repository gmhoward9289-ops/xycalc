# Plan — issue #10 (roadmap T2): compression ratio as a function of data shape

**Status:** complete (2026-08-21). Ran on swamplink-eu; see
`docs/investigations/010-compression-shape/FINDINGS.md`.

## 1. The question

If you hand me the shape of your documents — not a `db.stats()` reading, just
"mostly random IDs" or "a handful of enum fields" or "near-duplicate log
lines" — can I tell you where on the 1.5×–3.5× snappy band you're likely to
land, instead of you having to guess a point estimate out of the whole range?

## 2. What would falsify it

Three possible outcomes, and the experiment has to be willing to report any of
them:

- **Insensitive to shape.** All five shapes land within a narrow sub-range of
  the band regardless of structure. Falsifies "shape predicts ratio" — the
  wide band isn't wide because real data varies, it's wide because nobody
  measured, and a single point estimate would have done. The axis this
  experiment tries to manufacture doesn't exist.
- **Wider than the current band.** The spread across shapes exceeds
  1.5×–3.5× (already true at the low end — 1.42× was measured on random
  base62 in investigation 001). Falsifies "the band is honest" — it's too
  narrow, and the model has been printing false confidence.
- **Within the band, and shape-ordered.** Ratio tracks shape in the expected
  direction (near-duplicates compress best, pure-random worst) and stays
  inside 1.5–3.5. This is the boring, useful outcome: it confirms the
  practitioner band was right, and now there's a real curve behind it instead
  of one citation.

An issue whose premise can't come out any of these three ways isn't running a
real experiment. This one can.

## 3. Method

No throttling, no concurrency, no cgroups — unlike T1/T9, this isn't a
device-timing question, so `ticket_probe.sh`'s throttled-container machinery
doesn't apply. It reuses `tools/bench/mongodb_load.js`'s generation style
(batched `insertMany`, high-cardinality vs. enum fields) and
`tools/import_mongodb.py` as the landing mechanism, unmodified.

**New harness needed:** `tools/bench/compression_probe.js` +
`tools/bench/compression_probe.sh`. Neither existing harness generates
multiple document shapes or varies `block_compressor` per collection, so this
isn't an extension of either — it's a new generator in the same style.

### 3.1 Five shapes, one generation pass each

Generate each shape **once**, then insert the *same* batch into three
collections (one per compressor) rather than regenerating per compressor.
This removes RNG variance as a confound between compressor arms — the only
thing that differs across a shape's three collections is `block_compressor`.

| Shape | Design | Expected direction |
|---|---|---|
| `pure-random` | One large opaque random-base62 field per doc, minimal structure (like `ticket_probe.py`'s `pad` field). No cross-document redundancy to exploit. | worst compression |
| `random-repeated-fields` | Multiple named fields (5-8), each a random string, but real document structure. BSON field names repeat every document either way — this isolates the effect of *having several small fields* vs. one monolithic blob, since WiredTiger compresses per-block (default ~32 KB) and a block spans many adjacent documents. | slightly better than pure-random |
| `low-cardinality-enums` | All fields drawn from small fixed sets (5-10 distinct values each, like `mongodb_load.js`'s `STATUS`/`REGION`). | much better |
| `realistic-mixed` | Reuse `mongodb_load.js`'s existing document shape directly — its own comment already says it's built to be "roughly the shape of an events or orders collection." Don't reinvent this one. | middle of the pack |
| `near-duplicate` | Fixed template, 1-2 fields vary (a counter, a timestamp), rest identical across every document — closest to log lines from a fixed-format logger. | best compression |

Target **~300 MB dataSize per shape** (matching investigation 001's own
benchmark scale, `dataSize=299 MB`, which is known to behave cleanly) —
large enough that WiredTiger's fixed per-file/per-extent overhead is
provably under 1% of the total, small enough to load in minutes.

### 3.2 Three compressors per shape (15 collections, one mongod)

Per-collection compressor, not per-server — no need for 15 containers:

```js
db.createCollection("docs", {
  storageEngine: { wiredTiger: { configString: "block_compressor=zstd" } }
});
```

Values: `snappy`, `zstd`, `zlib`. zstd is community-edition since MongoDB
4.2, matching the existing `mongodb.compression-ratio-zstd` coefficient's own
`applies_to` — no licensing gotcha to trip over.

Fifteen collections total: `compressionprobe_<shape>_<compressor>.docs`.

### 3.3 Force a checkpoint before trusting `storageSize`

`storageSize` reflects on-disk allocation; reading it immediately after
`insertMany` risks catching WiredTiger mid-flush rather than checkpointed.
Run `db.adminCommand({fsync: 1})` once, after all 15 loads finish, before
reading any stats.

### 3.4 Read stats, verify the compressor actually took, land it

For each of the 15 collections:

```js
db.getSiblingDB("compressionprobe_<shape>_<compressor>").docs.stats()
db.getSiblingDB("compressionprobe_<shape>_<compressor>").runCommand({collStats: "docs"})
  .wiredTiger.creationString   // must contain "block_compressor=<compressor>"
```

Print one JSON line per collection: `{shape, compressor, dataSize,
storageSize, creationString, version}`. Reshape into the exact input
`tools/import_mongodb.py` expects (`{"stats": {"dataSize": D, "storageSize":
S}, "version": V, "at": T}`) and run it unmodified, 15 times, `--source-type
benchmark --no-validate --publish` (this is self-generated synthetic data —
nothing about a real customer to keep out of `data/`, unlike the default
`local/` destination the tool otherwise picks).

### 3.5 The measurable property, computed outside MongoDB entirely

The issue asks for "ratio as a function of a measurable property of the
data" — something a reader can compute on *their own* collection without
WiredTiger internals, root, or this repo's code. Proposal: **gzip -9
self-compression ratio on a raw export sample.**

```bash
docker exec "$NAME" mongoexport --db compressionprobe_<shape>_snappy \
  --collection docs --out /tmp/<shape>.jsonl
docker cp "$NAME":/tmp/<shape>.jsonl .
raw=$(wc -c < <shape>.jsonl)
gz=$(gzip -9 -c <shape>.jsonl | wc -c)
proxy_ratio=$(echo "$raw / $gz" | bc -l)
```

One export per shape (content is identical across its three compressor
collections, so export from any one of them — `snappy` by convention). Any
operator with `mongodump`/`mongoexport` and `gzip` can reproduce this on a
real collection and place it on the curve without touching this repo. That's
the actual deliverable of "manufactures the curve, so a real collection can
be placed on it from its own shape."

## 4. The guard

**What does this print if compression never actually varied?** The literal
failure mode: `block_compressor` is set in the `createCollection` call but
silently ignored (typo in the config string key, wrong API for the mongod
version, or all three collections quietly default to snappy) — the "zstd" and
"zlib" rows come back byte-identical to the "snappy" row for every shape. A
full, clean 15-row table. Every ratio plausible in isolation. Nothing wrong
visible anywhere except that three supposedly different compressors produced
identical numbers.

The counter, not an inference: **read back `creationString` for all 15
collections and assert it contains the expected `block_compressor=<x>`
before any ratio is trusted.** This is mechanical, not a plausibility check —
either the string is there or it isn't. Fail loud (exit nonzero, print which
collections mismatched) rather than publish a "clean" run that measured one
compressor three times.

Three more specific to this harness, each addressing "would this print a
plausible-looking table if the thing being measured never happened":

- **Fixed overhead dominating a too-small corpus.** Below the corpus-metadata
  and file-allocation floor, `storageSize` is mostly fixed cost and the ratio
  measures WiredTiger's minimum extent size, not the data. Refuse to proceed
  (same pattern as `ticket_probe.py`'s `REFUSING TO RUN`) if any shape's
  `dataSize` comes in under, say, 100 MB.
- **Reading `storageSize` before a checkpoint.** Addressed in 3.3 — an
  un-checkpointed read could show a partially-flushed, too-small
  `storageSize`, inflating the apparent ratio for every shape simultaneously
  in a way that looks like "everything compresses great" rather than "we
  didn't wait."
- **A copy-paste bug making two "different" shapes byte-identical.** Hash (or
  diff the first few KB of) each shape's raw export against the other four
  before trusting *any* cross-shape comparison. If `near-duplicate` and
  `realistic-mixed` hash the same, the generator has a bug, not a finding.
  Also assert the five gzip-proxy ratios are pairwise distinct and in the
  expected rank order (near-duplicate > enums > mixed > repeated-fields >
  pure-random) as a sanity check before trusting the MongoDB-side numbers at
  all — if the harness can't even reproduce that ordering on its own
  synthetic inputs, don't believe what it says about snappy.

## 5. What lands in the corpus

**Observations, not a rewrite of the shipped coefficient** — the issue is
explicit: "do not narrow the shipped band on synthetic data alone." This
experiment supplies data points and a proxy axis; it does not get to move
`mongodb.compression-ratio-snappy` unilaterally.

- **15 rows** in a new `data/observations/<host>-compression-shape-sweep-<date>.yaml`:
  `storage.compression_ratio` (`dataSize / storageSize`), one per
  shape × compressor, tagged in `workload` with the shape name and in
  `notes` with the paired gzip-proxy value so a future reader can
  reconstruct the curve from the observations table alone.
- **5 rows**, new parameter `storage.self_compression_proxy_ratio`
  (`dimension: ratio`) added to `data/parameters.yaml` — the gzip-proxy
  score per shape, compressor-independent since it's a property of the raw
  bytes. This is the reusable axis: a real collection (#5's job) gets
  exported and gzipped the same way and placed on the curve by its own
  score, with no dependency on this repo's generator.
- **1 new source** in `data/sources.yaml`, `source_type: benchmark`, harness
  committed at `tools/bench/compression_probe.js`/`.sh`, same pattern as
  `obs-mongodb-swamplink-bench-2026-07-31`.
- **One narrowly-scoped coefficient, conditional on the result:** if
  `pure-random`/snappy reproduces below 1.5× (likely — it already did once,
  at 1.42×, on similar data), propose a *new* coefficient
  `mongodb.compression-ratio-snappy-high-entropy-floor`, `confidence:
  measured`, `applies_to: "MongoDB >=3.0, snappy, high-entropy/incompressible
  documents (gzip-proxy ratio <~1.1)"`. This adds an anchored floor for a
  well-defined sub-population *without* touching the general practitioner
  band — the corpus gains a citable number for the case investigation 001
  already flagged as its worst error, while leaving the wide band's
  provenance exactly as honest as it was.
- **A new section in `docs/investigations/001-wiredtiger-cache/FINDINGS.md`**
  (append, don't fork a new investigation number — this is squarely
  001's compression term, and 003 already established the pattern of
  appending fault-injection results to an existing FINDINGS.md rather than
  minting a new directory for a follow-up measurement). Table: shape ×
  compressor × ratio × gzip-proxy, the falsification verdict actually
  reached, and the rank-order sanity check's result.

## 6. Effort and dependencies

Not blocked by anything; blocks nothing (issue's `blocked-by`/`blocking` are
both empty). Composes with #5 (real collections) but neither is a
prerequisite for the other — #5 can land points on this curve whenever a
real `db.stats()` shows up, before or after this runs.

- Harness (`compression_probe.js` + `.sh`): 2-3 hours, mostly the shape
  generators and the `creationString` verification step.
- Run time: single unthrottled `mongod` in Docker, no cgroup throttling, no
  concurrency sweep — just five generation passes and fifteen `insertMany`
  loads at ~300 MB each (4.5 GB total written). Comparable in scale to
  `mongodb_load.js`'s existing 500k-document load. Estimate 20-40 minutes
  wall-clock for the loads plus the `mongoexport`/`gzip` pass, once the
  harness is written — this is an estimate, not a measurement; the actual
  number is exactly the kind of thing this plan is not allowed to invent.
- Landing (15 `import_mongodb.py` calls + the parameter/source edits +
  the FINDINGS section): under an hour.

## 7. What could make this not worth doing

The gzip-proxy axis is a bet, and it could fail in a specific, checkable way:
gzip -9 is a substantially stronger, differently-tuned algorithm than
snappy/zstd/zlib block compression, so it may **rank** shapes correctly
without its absolute ratio **predicting** the MongoDB-side ratio to any
useful precision. If the five (proxy, snappy) points don't fall on anything
resembling a monotonic curve — even though the ranking guard in §4 passed —
the experiment still answers §2's falsification question (is the band
shape-sensitive, and how wide) but fails its stated purpose (a real
collection places itself on the curve from its own shape), and that failure
should be reported exactly as plainly as a working curve would be.

Five shapes is also a scatter, not a curve, in the statistical sense — it
does not by itself justify calling the result more than `measured` for the
specific sub-claims it lands (per §5), and it does not resolve #5's request
for observations from real, non-synthetic collections. Nothing here
substitutes for that; it only gives a real collection's `db.stats()` reading
somewhere honest to sit once it arrives.
