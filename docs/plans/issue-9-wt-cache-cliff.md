# Plan — Issue #9 / Roadmap T1: is the cache cliff a cliff?

## 1. The question

As a MongoDB working set grows past the size of the WiredTiger cache, does
read throughput fall off gradually, or is there a point where it drops
sharply — and if there's a drop, does it start right at 1.0x cache, or does
performance already erode before the dataset even exceeds the cache?

## 2. What would falsify it

Two separate claims are in play, and the experiment has to be able to kill
either one:

- **"There is a cliff."** Falsified if throughput (equivalently, latency —
  see the concurrency choice in §3) against oversubscription ratio is a
  smooth, monotonically-declining curve across the whole 0.5–8x sweep, with
  no ratio at which the local rate of decline is far outside the
  leg-to-leg noise. Concretely: fit the local log–log slope
  (Δlog(throughput)/Δlog(ratio)) between each adjacent pair of legs. No
  cliff means no segment's slope is dramatically steeper than its
  neighbours'.
- **"Cache-resident means the cache equals the data" (i.e. the boundary is
  at exactly 1.0x, not before).** Falsified if the steep segment — assuming
  one exists — starts at a ratio below 1.0x. That would mean performance is
  already degrading before the dataset exceeds the cache, which contradicts
  the working-set-sizing folklore this whole roadmap entry exists to check.

A run that can only produce "yes there's a cliff, no there isn't" without a
numeric slope comparison is not falsifiable — see the decision rule in §3.

## 3. Method

**Reuse, per the issue's own instruction:** `tools/bench/ticket_probe.py` /
`.sh` already have the pieces — a real-thread pymongo driver (not mongosh,
which auto-awaits and serialises "concurrent" calls), the block-IO cgroup
throttle scoped to one container, the `tickets()`/`cache_state()`
serverStatus readers, and the refuse-to-run guard for a working set that
fits the cache. This plan forks two new siblings rather than writing a
sweep from scratch:

- `tools/bench/cache_cliff_probe.py` — forked from `ticket_probe.py`.
  Differences: **fixed concurrency, swept dataset size** (the issue's
  instruction, inverted from the existing script). One probe per process
  invocation, not a ladder of levels.
- `tools/bench/cache_cliff_probe.sh` — forked from `ticket_probe.sh`.
  Difference: loops over oversubscription ratios and starts **a fresh
  `mongod` container for every ratio**, rather than one long-lived
  container.

**Why a fresh container per ratio, not one process with a growing
collection.** Investigation 003's own postmortem is explicit that running
levels back-to-back in one continuous `mongod` process let ticket state
carry forward and never settle within a level — the reason a precise
validation case couldn't be published from that run. Growing the dataset
in place across ratios would import the identical problem into T1: WT cache
occupancy, eviction history and page layout would all carry forward from
the previous ratio, contaminating each leg with the one before it. A fresh
container per ratio costs wall-clock time; it buys a clean measurement.

**Sizing the dataset without inventing a bytes-per-document constant.**
`ticket_probe.py`'s document schema (700-byte random pad + 24-byte random
key) is reused unchanged, but instead of hardcoding a bytes/doc estimate to
hit a target ratio, the driver inserts a small pilot batch (~2,000 docs),
reads `db.stats().dataSize` to get the real average, and computes the
remaining document count from that measured average against the *live*
`cache_state()["maxCache"]` (read from `serverStatus`, never assumed — the
`mongodb.ticket-throughput-ceiling` model was burned once already by
assuming 128 tickets on an instance that actually rested at 4). After the
full load, `db.stats().dataSize / maxCache` is checked against the ratio
target with a tolerance (±10%, `PROBE_RATIO_TOLERANCE`); outside tolerance,
the leg is refused rather than silently recorded off-target.

**Fixed parameters, reused from `ticket_probe.sh` where an established
value already exists:**

| Parameter | Value | Source |
|---|---|---|
| WiredTiger cache | 0.25 GB | `ticket_probe.sh` default |
| Container memory | 640 MB, **identical at every ratio** | `ticket_probe.sh` default — see §4 for why this must not scale with dataset size |
| Device read throttle | 8 MiB/s, 150 IOPS, this container only | `ticket_probe.sh` default |
| Seconds per leg | 25 | `ticket_probe.sh` default |
| Oversubscription ratios | 0.5, 0.8, 1.0, 1.2, 1.5, 2, 4, 8x | issue text, verbatim |
| Concurrency | **1** (new — not a sweep dimension here) | see below |

**Why concurrency = 1.** At the same throttle (8 MiB/s / 150 IOPS) and a
similar oversubscription (4.2x), investigation 003 measured zero ticket
queueing at concurrency 1, 2 and 4, and real queueing from concurrency 8
up. T1 is not the ticket-ceiling investigation — that mechanism is a
*different* cliff, already characterised, and mixing it into this run would
make it impossible to tell whether an observed knee comes from the WT cache
or from the ticket pool. Concurrency 1 rules out ticket queueing by
construction (a single synchronous thread never holds more than one ticket
at a time), at the cost of making throughput and latency mathematically
identical (throughput = 1 / latency) — which is fine here, because that
redundancy is exactly what isolates the cache effect from the
queueing-driven throughput/latency divergence that made 003 interesting in
the first place. A secondary arm at concurrency 4 (verified clean of
queueing via `queuedMicrosDelta`) is worth running once the primary result
is in hand, to check the knee survives when *some* concurrency is present —
but it is not required to answer the question as posed.

**Repetition.** Run the full 8-ratio sweep twice, sequentially (not
overlapping — investigation 003's two runs shared a host and each
depressed the other's throughput). Treat a knee as real only if it appears
at the same ratio in both runs; a knee that appears once and vanishes on
repeat is noise, not a coefficient.

**Commands, once the two scripts above are written:**

```bash
./tools/bench/cache_cliff_probe.sh > /tmp/cache-cliff-run1.json
./tools/bench/cache_cliff_probe.sh > /tmp/cache-cliff-run2.json
```

Rough wall-clock per sweep: load time grows with ratio (roughly 10–20s at
0.5x up to somewhere near the ~60–70s `ticket_probe.sh` already spends
loading 1.5M docs, scaled up for the ~2.7M docs the 8x leg needs at this
cache size) plus 25s probe plus ~10s container start/teardown, across 8
legs — call it 15–25 minutes per sweep, 30–50 minutes for both. This is an
estimate to plan around, not a promised figure; the pilot-batch sizing step
in §3 will produce the real numbers on first run.

## 4. The guard

**The literal question the issue asks under "Watch for":** if the host page
cache absorbs the reads instead of the throttled device, what does this
experiment print?

It prints a table that looks exactly like a real result and says the
opposite of the truth: `pagesReadIntoCache` rises with ratio exactly as
expected (WT's own cache-miss accounting doesn't know or care where the
bytes came from underneath), but throughput and latency stay flat, because
a "miss" served from RAM-speed host page cache costs almost nothing — the
250ms-class cost the throttle was supposed to impose never happens. That
table says "no cliff" when the honest answer is "the experiment never
touched the thing being measured." This is not a hypothetical: it is
literally what happened to `ticket_probe.py`'s second smoke run, on this
same class of harness, in this same repo.

A fixed 640 MB container memory ceiling (not scaled up with dataset size,
per §3) is necessary but was already shown insufficient on its own once —
the third documented failure in #8 held the memory cap and still fit
605 MB in host RAM. Two additional checks, not optional:

1. **Direct I/O, tried first.** Start `mongod` with
   `--wiredTigerEngineConfigString="direct_io=[data]"` (or the equivalent
   `storage.wiredTiger.engineConfig.configString` in a config file). This
   makes host-page-cache absorption structurally impossible rather than
   merely unlikely — WT's block reads bypass the page cache by construction.
   **Risk, to be resolved by a one-leg smoke test before the real sweep:**
   Docker's overlay2 storage driver has a history of not supporting
   `O_DIRECT` reliably on the container's writable layer. If `mongod`
   refuses to start or errors on first read with direct I/O enabled, fall
   back to check 2 and say so plainly in the write-up rather than silently
   dropping the stronger guard.
2. **Device-byte cross-check, always run regardless of whether direct I/O
   works.** At each ratio above 1.0x, read the container's own cgroup
   block-IO read-byte counter before and after the probe window
   (`docker exec <container> cat /sys/fs/cgroup/io.stat` on cgroup v2, or
   `blkio.throttle.io_service_bytes` on v1) and compare the delta,
   order-of-magnitude, against `pagesReadIntoCache_delta × page_size`. If
   device bytes read is near zero while `pagesReadIntoCache` clearly rose,
   the miss was served from somewhere other than the throttled device —
   discard that leg's numbers rather than average over the defect. This is
   the counter that makes the failure loud: a plausible table with this
   check attached either passes it, or announces exactly why it can't be
   trusted.

Two more checks reused directly from the existing harnesses, both
load-bearing rather than decorative:

3. **`cacheOversubscription` within tolerance of the ratio target** (§3) —
   catches a load that landed at the wrong size, which would make every
   downstream number describe a different experiment than the one recorded.
4. **`queuedMicrosDelta` ≈ 0 at every leg** (concurrency 1 makes this
   almost automatic, but assert it rather than assume it — and it becomes
   the binding check on the optional concurrency-4 arm).

If none of these guards fire and the curve still comes out smooth, that is
a real result, not an artifact — "size for the working set" would be
better-supported advice than the issue currently gives it credit for. The
guards exist to make sure a smooth curve means that, not "the throttle
never engaged."

## 5. What lands in the corpus

- **New parameters** (`data/parameters.yaml`): `cache.oversubscription_ratio`
  (dataset:cache ratio, dimensionless) and `cache.miss_pages_per_op` (WT
  pages read into cache per operation) — neither exists in the corpus yet;
  grep confirms it.
- **Observations** (`data/observations/<host>-cache-cliff-probe-<date>.yaml`,
  `data/sources/<host>-cache-cliff-probe-<date>.yaml`, mirroring the
  `swamplink-ticket-probe-2026-08-01` pair exactly): one row per ratio per
  run (16 rows for two repeated sweeps of 8 ratios), each carrying
  `system_version` read live from `buildInfo()` — checked identical across
  all 16 legs as a cheap consistency assertion, not assumed — throughput,
  mean/p95 latency, and `pagesReadIntoCache` per op.
- **Either a knee coefficient or a documented absence, per the roadmap
  entry itself:**
  - If a knee holds across both repeat runs: a new coefficient, e.g.
    `mongodb.wt-cache-cliff-knee-ratio`, graded **`measured`** (our own
    benchmark — not `documented`, not `practitioner`). `applies_to` must
    name more than the MongoDB version: this run's cache size, throttle,
    and — importantly — **uniform random access**. Uniform lookups have no
    locality for the cache to exploit, which is close to a worst case;
    real workloads with access skew would likely show a softer or later
    knee at the same nominal ratio. The coefficient should say so in its
    `notes`, not be shipped as if it generalises to skewed access.
  - If no knee (smooth decline) survives both runs and the guards: a
    `notes:` addition to `mongodb.wt-cache` (or a small new model,
    `mongodb.cache-hit-ratio-by-oversubscription`, if the curve itself is
    worth carrying as a named quantity) recording the absence explicitly,
    per the SKILL's instruction that "documented absence" is a valid
    corpus entry, not a null result to discard.
- **Feeds `mongodb.wt-cache`.** Its `reframe:` currently tells a reader to
  "size the WORKING SET, not the database" without saying anything about
  how close to that boundary is safe. Whichever outcome lands, that prose
  should get a follow-up sentence — not part of this experiment's own
  scope, but a direct, concrete consequence of it.

## 6. Effort and dependencies

- **Build:** ~2 hours to fork `ticket_probe.py`/`.sh` into the dataset-sweep
  pair described in §3. Most of the plumbing (docker orchestration,
  serverStatus readers, the real-thread driver) is a direct port.
- **Run:** 30–50 minutes for two sequential sweeps (§3's estimate), plus
  time for the direct-I/O smoke test in §4 and, if it fails, no extra time
  (the fallback guard is already built in). Add ~15 minutes for the
  optional concurrency-4 robustness arm.
- **Write-up:** ~1–1.5 hours for `FINDINGS.md` in the house style —
  applies fully to a benchmark result, not just a literature review.
- **Total: half a day**, one Linux box with Docker, nothing external.
- **Depends on nothing else being done first.** Does not block or get
  blocked by any other roadmap item. Softly related to T3 and T4, which
  instrument the same WT cache/eviction machinery on a similar
  fixed-cache-throttled-device skeleton — worth factoring the
  `tickets()`/`cache_state()`/docker-orchestration pieces into a shared
  module once a third harness needs them, rather than forking a third copy.
  Not worth doing as part of this plan; worth flagging for whoever picks up
  T3.
- **Feeds `mongodb.wt-cache`'s prose** (§5) — that follow-up edit should
  wait for this result rather than guess at it.

## 7. What could make this not worth doing

- **Uniform random access is close to worst-case for a cache.** The result
  this produces is a defensible, conservative bound — but if the honest
  goal is "tell a reader with a realistic (skewed) access pattern where
  their cliff is," this experiment alone doesn't answer that, and the
  coefficient must be labeled accordingly (§5) rather than oversold. That
  is a scoping caveat, not a reason to skip the experiment — the uniform
  case is the one nobody has checked, and the skewed case is a reasonable
  follow-up, not a prerequisite.
- **If direct I/O fails and the byte-level cross-check still can't rule
  out partial host-cache absorption right around the 0.8–1.5x region** —
  the exact region the question is actually about — the sweep would still
  honestly answer "does it fall off a cliff by 4–8x" (not seriously in
  doubt) without answering "does the knee sit before 1.0x," which is the
  more interesting and more roadmap-relevant of the two claims in §2. If
  the smoke test in §4 fails, that risk should be named in the write-up
  rather than glossed over — the qualitative "cliff or no cliff" finding
  is still worth publishing; the precise knee location claim should not be
  asserted with confidence it cannot support.
- **Toy cache size (0.25 GB).** The mechanism (page-level LRU eviction
  against a ratio) should generalise to production-scale caches — nothing
  about WiredTiger's eviction design is a function of absolute cache size —
  but the absolute latency and throughput figures are artifacts of the
  deliberately slow throttle and this cache size, and must not be quoted
  as capacity numbers. Only the *shape* (where the slope changes, if it
  does) is the thing worth carrying forward.
