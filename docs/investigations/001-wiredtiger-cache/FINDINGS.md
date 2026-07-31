# Findings — WiredTiger cache sizing

**Investigated:** 2026-07-31 · **Model:** `mongodb.wt-cache`, `mongodb.host-ram`
· **Validation:** none. Both models are unvalidated (n=0).

---

## The short answer

A 500 GB MongoDB database with 40 GB of indexes needs a WiredTiger cache of
about **1.6 TB** (band 988 GB – 2.2 TB) to hold all of it resident, which
implies a host with roughly **3.2 TB of RAM** if the default cache split is
left alone.

```bash
xycalc sizing mongodb.wt-cache --storage-size 500GB --index-size 40GB
xycalc sizing mongodb.host-ram --cache-size 1.6TB
```

Almost nobody should do this, and MongoDB says so.

---

## The reframe

The question contains a premise the vendor rejects twice, in writing.

> "Must my working set size fit RAM?" — **"No."**
> — [FAQ: MongoDB Diagnostics](https://www.mongodb.com/docs/manual/faq/diagnostics/)

> "Avoid increasing the WiredTiger internal cache size above its default value."
> — same page

That is not conservatism. There is a mechanism, and it is the finding worth
carrying away from this investigation:

> "Collection data in the WiredTiger internal cache is uncompressed and uses a
> different representation from the on-disk format."
>
> "Data in the filesystem cache is the same as the on-disk format, including
> benefits of any compression for data files."
> — [WiredTiger Storage Engine](https://www.mongodb.com/docs/manual/core/wiredtiger/)

**There are two caches, and they have different densities.** The WiredTiger
cache holds decompressed pages. The filesystem cache — the RAM that is *not* in
the WiredTiger cache — holds the same data still compressed, so per byte of RAM
it holds several times more of the database.

The consequence is the counterintuitive part. Raising `wiredTigerCacheSizeGB`
to "make everything fit" takes RAM away from the denser cache and gives it to
the sparser one. Past some point **you can reduce how much of the database is
cached in total by increasing the cache**. The default 50/50 split is not a
compromise between MongoDB and the OS; it is the vendor's judgement about the
best mix of the two.

So the useful question is not "how big a cache holds my database" but "how big
a cache holds my **working set**, with the rest of RAM left to the filesystem
cache" — and then: what do the misses cost? That is where this hands off to
storage. Every page that does not fit becomes a read, and reads become the EBS
IOPS question (investigation 002, not yet run).

---

## The arithmetic, and where each number came from

| Step | Factor | Grade | Source |
|---|---|---|---|
| Collection data on disk | input, `storageSize` | — | caller |
| Decompression into cache | ×2.5 (1.5 – 3.5) | `practitioner` | Percona |
| Indexes | + `indexSize`, unexpanded | — | caller |
| Eviction headroom | ÷ 80% | `documented` | WiredTiger docs |

**Decompression** is the term people leave out, and leaving it out understates
the answer by more than everything else combined. It is also the weakest term
in the corpus: the published 2.5× is measured on log data, which compresses far
better than a collection of ObjectIds and numbers. Hence the 1.5–3.5 band, the
widest here, and the `practitioner` grade.

**Indexes are added after decompression, not multiplied by it**, because prefix
compression survives into the cache while collection block compression does
not:

> "Indexes loaded in the WiredTiger internal cache have a different data
> representation to the on-disk format, but can still take advantage of index
> prefix compression to reduce RAM usage."

Multiplying indexes by the collection compression ratio would be a real error.
The term ordering in `data/models/mongodb.yaml` encodes the distinction, and
`test_model.py::test_indexes_are_added_after_decompression` pins it.

**Eviction headroom** is why a cache configured at exactly the size of its
contents is a cache under permanent pressure:

> "The `eviction_target` configuration value (default 80%) is the level at
> which WiredTiger attempts to keep the overall cache usage."

---

## Constraints — carried, not computed

Three figures that change what you do without changing the number.

- **95% — `eviction_trigger`.** Application threads start doing eviction work.
  Nothing crashes. Queries get slower. This is why an undersized cache is
  normally diagnosed from a latency graph, not a memory graph, and why the
  first symptom shows up in traces rather than in an alarm.
- **20% — `eviction_dirty_trigger`.** Only a fifth of the cache may be dirty
  before writers are throttled. **A bulk load can hit this at 20% total
  occupancy**, so a cache sized correctly by this model still throttles.
  Sizing never settles a write-heavy incident on its own.
- **50% — the default split.** Discussed above.

---

## Disagreements, unresolved on purpose

**How much does data compress?** No single answer exists. Percona's figures
(snappy ~2.5×, zlib ~3.5×, zstd ~4.0×) are measured on log data. A collection
that is mostly ObjectIds, timestamps and short numeric fields will do far
worse; one holding JSON blobs will do better. Reported as a range, not
averaged into a fact.

There is also a **counterintuitive interaction** worth stating: a *better* disk
compressor makes the cache problem *worse*. The same on-disk bytes decompress
into more cache bytes. zstd saves disk and costs RAM. The corpus carries zstd
and zlib coefficients so this can be modelled, but the shipped model assumes
the default snappy.

**Is `indexSize` compressed?** The dbStats reference defines it as "sum of the
disk space allocated to all indexes in the database, including free index
space" and does not say. The WiredTiger page's statement about prefix
compression surviving into cache is the closest thing to an answer, and the
model's decision to treat in-cache index bytes as ≈ `indexSize` follows from
it. **This is the weakest inference in the investigation.** It is recorded here
rather than smoothed over, and it is the first thing a measurement should
check.

---

## What would validate this

Both models are `unvalidated (n=0)`. One `db.stats()` plus one
`serverStatus().wiredTiger.cache` from any real MongoDB would produce the first
case:

```javascript
db.stats()                                    // dataSize, storageSize, indexSize
db.serverStatus().wiredTiger.cache            // "bytes currently in the cache"
```

The check that matters: does `dataSize + indexSize` predict "bytes currently in
the cache" when the cache is large enough to hold everything? That tests the
decompression and index terms together against reality, and it needs no load
generator — just a database that fits.

`docs/telemetry/mongodb.md` lists the full series. `xy-observe` imports them.

---

## Method note

Researched directly against vendor documentation rather than through the COOPER
batch pipeline, because the sources were few, authoritative and in English —
the pipeline earns its keep on breadth, not on four canonical pages. The
verbatim quotes were still captured for every `documented` coefficient and
`tests/test_corpus.py` enforces their presence, so the gate applies equally to
work done by hand.
