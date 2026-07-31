# Investigation 001 — WiredTiger cache sizing

**Question as asked:** How large does my WiredTiger cache need to be to fit my
entire database and run properly?

**Status:** complete. Findings in `FINDINGS.md`, corpus in
`data/coefficients/mongodb.yaml`, model `mongodb.wt-cache`.

**Expected confidence ceiling:** `documented`. Unusual, and worth saying up
front — MongoDB and the WiredTiger project both publish the constants this
needs. Most infrastructure questions will not be this well served, and the
compression term below is the one that isn't.

---

## Why this subject

It is the question George actually has, and it breaks an assumption, which is
the better reason. The premise — that you would size a cache to hold an entire
database — turns out to be one the vendor explicitly advises against. A corpus
that could only produce the arithmetic and not the contradiction would be
worse than useless here, because the arithmetic alone points the wrong way.

It also establishes the schema features the whole project needs:

- **Versioned coefficients.** WiredTiger's eviction defaults are published per
  MongoDB release. A figure without a version is not reusable.
- **Constraint terms.** "The vendor says don't" is evidence. It does not enter
  the arithmetic and it must not be lost.
- **Band inversion.** Dividing by a fraction inverts the band. This is the
  first place it appears and it will appear everywhere.

---

## Decomposition

### FLOOR — what actually has to be resident

The trap is which number to start from. `db.stats()` reports three sizes and
they mean different things:

| Field | Meaning | Compressed? |
|---|---|---|
| `dataSize` | collection data | **no** — already uncompressed |
| `storageSize` | disk allocated to collections | **yes** |
| `indexSize` | disk allocated to indexes | partly (prefix compression) |

`totalSize` = `storageSize` + `indexSize`, so "my database is 500 GB" almost
always means `storageSize` or `totalSize` — a compressed figure — while the
cache holds the uncompressed form.

**To settle:** which of these occupies the cache, in the vendor's own words.

### AMPLIFIER — what raises it above the floor

1. **Decompression.** Does the WiredTiger cache hold compressed or
   decompressed pages? If decompressed, by how much do they expand?
2. **Index representation.** Do indexes expand in cache the same way
   collection data does, or does prefix compression survive?
3. **Eviction headroom.** WiredTiger targets a cache utilisation below 100%.
   Whatever that target is, configured cache and usable cache differ by it.

### HEADROOM — the tail, not the mean

Dirty-page limits. A write-heavy workload is throttled on the *dirty* fraction
of the cache, which is much smaller than the total. Sizing on total bytes and
then throttling at 20% occupancy is a real and common outcome.

### CONSTRAINT — bounds that do not compute

Anything the vendor says about *not* doing this. Look for it deliberately
rather than incidentally.

### The handoff

Every page that does not fit becomes a disk read. Where the answer lands
relative to available RAM decides how much read traffic the storage layer
sees — which is the EBS IOPS question, investigation 002. Note the link even
though EBS is not researched yet; the point of one corpus is that the two
questions are the same question at different layers.

---

## Do NOT do

- **Do not average a disagreement.** Compression ratios vary by an order of
  magnitude across workloads. Report the range; do not pick a midpoint and
  present it as the figure.
- **Do not grade a blog post `documented`.** `documented` means a vendor states
  it outright. Percona is `practitioner`, however good.
- **Do not omit `applies_to` because the number "hasn't changed in years".**
  That is a claim about the past.
- **Do not answer only the question as asked** if the honest answer is that it
  is the wrong question. Answer it, then say so, and cite the saying-so.
