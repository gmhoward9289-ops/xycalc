# Plan — issue #13 (ROADMAP T5): do covered queries change device load the way 001 assumes?

**Status:** proposal, not run. Nothing below has been executed.
**Targets:** `mongodb.wt-cache` (`data/models/mongodb.yaml`), the `indexes` term
specifically — the term investigation 001's own FINDINGS names as its weakest
inference, before any measurement existed.

---

## 1. The question, as a person would ask it

If my queries only ever touch an index — never the document — does my cache
only need to hold the index bytes, the way the model assumes? Or does MongoDB
pull document pages into cache anyway, even when the query plan says it
shouldn't have to?

## 2. Where the issue's premise needs sharpening

The issue is right about what the model does, but reading `data/models/mongodb.yaml`
closely changes what the experiment should measure.

**There is no index-expansion coefficient to correct — there isn't one yet.**
The `indexes` term is `apply: input, input_key: index_size`: it adds
`index_size` to the running total unmultiplied, straight from the caller. It
is not "treat in-cache index bytes as `indexSize` via some coefficient that
might be wrong" — it is "there is no coefficient here at all, on the theory
that prefix compression makes one unnecessary." If this experiment finds a
real expansion factor, that is a **new term to propose**, not an existing one
to fix.

**001's own 13.9% validation number cannot already be blamed on the index
term**, because that run never isolated it. `mongodb_load.js`'s comment says
the collection was "fully scanned to force residency" — a scan that fetches
whole documents, not a covered scan. So the 13.9% gap (measured resident bytes
over `dataSize + indexSize`) is a *combined* index-side and document-side
error, and nothing in 001 attributes any of it to either side specifically.
T5, done right, is the first experiment that can split that number in two.

**Writes populate the cache too, and 001's validation run did not control for
that.** `mongodb_load.js` inserts 500k documents and then builds four indexes,
all of which dirties WiredTiger pages before any read happens. On a dataset
this small (~0.3–0.4 GB) sitting in a multi-hundred-MB-to-multi-GB cache, it
is entirely possible the collection was already substantially resident from
the *write* path before the "scan to force residency" ever ran — in which
case that scan measured very little. A clean read-attribution experiment
needs a cache flush between load and measurement. WiredTiger's cache is
in-process memory; nothing short of restarting `mongod` clears it. **This plan
restarts the container between load and the read phases**, which 001's
original validation run did not do and does not claim to have done.

**Aggregate `indexSize` is the wrong denominator for a single-index covered
scan.** `mongodb_load.js` builds four indexes. A covered scan against one of
them warms only that index's pages. Comparing that against `db.stats().indexSize`
(the sum of all four) would make the index term look like it under-predicts
by roughly 75% for reasons that have nothing to do with the model — an
artifact of the experiment, not a finding about MongoDB. `db.collection.stats().indexSizes`
gives the **per-index** breakdown; use it.

None of this contradicts the issue. It says the method needs to be more
careful than "run a covered workload and a fetch workload and compare" to
avoid producing exactly the kind of clean-but-wrong table the roadmap's
guard rule warns about.

## 3. What would falsify it

The model's implicit claim, stated precisely: **in-cache index bytes for
pages actually touched by a query track that index's on-disk `indexSizes`
entry, to within noise** — no expansion coefficient needed, because prefix
compression carries the density into cache.

- **Falsified if** a genuinely covered scan (verified by `explain()`, not
  assumed) leaves resident bytes attributable to that index meaningfully
  above its `indexSizes` entry. That means the index term needs a
  multiplier the model does not have, and the 13.9% gap is partly a missing
  term rather than pure overhead.
- **Falsified the other direction** (the issue's own falsification, and the
  more dramatic outcome) if a query `explain()` certifies as covered still
  drives document-level reads under load — `pages read into cache` for the
  "covered" phase keeps climbing well past the point where the index should
  be fully warm, tracking toward index+document totals. That would mean
  `explain()`'s classification cannot be trusted as a proxy for "no document
  I/O," which is a much bigger problem than a mis-sized coefficient.
- **Supported** if covered-phase resident bytes land within a small band of
  the touched index's `indexSizes` entry (call it ±15%, matching the kind of
  tolerance 001 treated as "inside the band") for both a high-cardinality
  and a low-cardinality index, and the fetch phase's *incremental* resident
  bytes (see Method) track `dataSize` similarly closely. That would mean the
  13.9% gap is real but comes from neither side cleanly — see §7.

## 4. Method

### Reuse, and what's new

Reuse `tools/bench/mongodb_load.js` **unmodified** — same dataset the issue
asks for: 500k documents, four indexes (`account_id`, `created_at`,
`{status,region}`, `idempotency_key`), the events-shaped generator already
built to compress like application data rather than lorem ipsum.

Do not reuse `tools/bench/ticket_probe.py`'s concurrency machinery. This
experiment is single-threaded on purpose — deterministic, exhaustive coverage
of a key range is what "fully warm the index" needs, and `ticket_probe.py`'s
whole reason for existing in Python rather than mongosh (avoiding mongosh's
auto-await serialising "concurrent" calls) does not apply when serial
execution is the goal. A new, small harness is warranted: **new**
`tools/bench/covered_query_probe.js` (mongosh, phased single-threaded scans +
`explain()` guards + `serverStatus`/`stats` snapshots), driven by **new**
`tools/bench/covered_query_probe.sh` (docker lifecycle, modeled on
`ticket_probe.sh`'s unique-name-per-run and trap-based cleanup, minus the
block-IO cgroup plumbing — not needed here, see §5).

### Container

One `mongo:7` container (same image family as 001's 7.0.39 validation and
`ticket_probe.sh`, for version continuity), no `--rm`, so it survives a
restart between phases:

```bash
docker run -d --name "$NAME" \
    --memory "${PROBE_MEMORY:-1g}" \
    "$IMAGE" --wiredTigerCacheSizeGB "${PROBE_CACHE_GB:-1}"
```

`PROBE_CACHE_GB=1` is deliberately larger than the ~0.41 GB investigation 001
measured resident from this same generator at 500k docs, so nothing evicts
mid-experiment (occupancy should finish under ~45%, comfortably below the 80%
eviction target — checked explicitly, see §5's guard list).

### Sequence

1. `docker exec ... mongosh mongodb_load.js` — load, build all four indexes.
2. `docker restart "$NAME"` — clears the in-process WT cache; wait for
   `ping:1` to succeed again (same poll loop as `ticket_probe.sh`).
3. Guard: `wiredTiger.cache['bytes currently in the cache']` must be small
   (near baseline, not near the ~0.41 GB from before restart). Abort if not —
   the restart didn't take, or something is warming the cache before we're
   ready to measure it.
4. Snapshot `db.events.stats()` → `dataSize`, `indexSizes` (per-index), and
   `wiredTiger.cache` → this is **T1**, the true cold baseline.
5. **Phase A — covered, high-cardinality index.**
   `db.events.find({}, {_id:0, account_id:1}).hint({account_id:1})`.
   Before running: `explain("executionStats")` the same query; assert the
   plan tree contains no `FETCH` stage and `totalDocsExamined === 0`. Abort
   loudly, printing the actual plan, if either check fails. Iterate the full
   cursor (all 500k docs). Snapshot cache state → **T2**. `T2 − T1` is index
   `account_id`'s covered residency. Re-run the identical scan once more;
   its `pages read into cache` delta must be ~0 (plateau check — confirms
   the index is genuinely fully warm and stable, not still filling).
6. **Phase B — covered, low-cardinality index.** Same shape, against
   `{status:1, region:1}`: `db.events.find({}, {_id:0,status:1,region:1}).hint({status:1,region:1})`.
   Same `explain()` guard, same full iteration, same plateau re-run. Snapshot
   → **T3**. `T3 − T2` is that index's covered residency. This index is the
   more interesting case for the model's "prefix compression survives into
   cache" claim — low-cardinality repeated keys are exactly where on-disk
   prefix compression does the most work, so it's the shape most likely to
   show in-cache bytes diverging from `indexSizes`.
7. **Phase C — document fetch, same predicate and index as Phase A.**
   `db.events.find({}).hint({account_id:1})` — no projection restriction,
   forcing a `FETCH` stage (assert this in `explain()` too — the opposite
   check from Phase A). Because `account_id`'s index pages are already warm
   from Phase A, this phase's cache growth is attributable to document pages
   *alone*, not index+document conflated. Snapshot → **T4**. `T4 − T3` is the
   incremental document-fetch residency for all 500k documents. Plateau
   re-run as before.
8. Re-read `db.events.stats()` at the end (unchanged since no writes happened
   after step 1 — read defensively anyway).

### What to compute

| Comparison | Tests |
|---|---|
| `(T2−T1).bytes` vs `indexSizes["account_id_1"]` | does a high-cardinality index's covered residency track its on-disk size |
| `(T3−T2).bytes` vs `indexSizes["status_1_region_1"]` | same, for a low-cardinality index — the shape most likely to break the prefix-compression assumption |
| `(T4−T3).bytes` vs `dataSize` | isolates the document-side expansion, independent of any coefficient — `dataSize` is already uncompressed, so this is a direct test of "does cache hold decompressed document bytes and nothing more," decoupled from the (separately, and already, contested) snappy ratio |
| `(T4−T1).bytes` vs `dataSize + indexSizes["account_id_1"] + indexSizes["status_1_region_1"]` | reproduces a more precise version of 001's 13.9%, restricted to exactly the two indexes actually touched — the two untouched indexes (`created_at`, `idempotency_key`) must stay unread, which is itself a check worth printing |
| per-phase `pagesReadIntoCache` deltas | do covered queries drive materially less device-facing work than fetches, per operation — the issue's literal question |

### Commands (once the harness exists)

```bash
./tools/bench/covered_query_probe.sh                                  # default: 500k docs, 1GB cache
PROBE_CACHE_GB=2 PROBE_DOCS=1000000 ./tools/bench/covered_query_probe.sh
```

Prints JSON after a `===JSON===` marker, matching `ticket_probe.py`'s
convention, for the same reason: easy to pipe into an observation file
without scraping prose.

## 5. The guard

**What would this print if covered queries never actually avoided touching
documents?** A clean, complete table where Phase A and Phase C show similar
`pagesReadIntoCache` and similar resident-byte growth — which, read
carelessly, looks like "covered queries don't help much" when the real
story might be "the harness's 'covered' query wasn't covered." Three checks
close this, and all three must be printed, not just checked silently:

1. **`explain()` before, not after.** Phase A and B assert `FETCH` is absent
   and `totalDocsExamined === 0` *before* the timed scan runs. Phase C
   asserts `FETCH` *is* present. A silent planner fallback (wrong hint,
   `_id` sneaking into the projection, an index that turns out multikey for
   these fields) is caught here, loudly, before a single number is trusted.
   This is the primary guard and the one the issue's own falsification
   clause depends on.
2. **The plateau re-run.** Every phase runs its full scan twice. If the
   second pass shows non-trivial `pagesReadIntoCache` growth, the phase
   never reached a stable, fully-resident state — either the cache is too
   small (evicting under a workload assumed not to evict) or something is
   re-reading pages that should already be warm. Either way the snapshot
   taken is not measuring what it claims to. This is the harness-level
   version of the `MIN_OVERSUB` / zero-pages-read guards in `ticket_probe.py`
   and the celery probe, adapted to a residency question instead of a
   throughput one: there the danger was a working set that fit trivially in
   cache; here it's a working set that *never stabilizes* in cache.
3. **Occupancy ceiling.** `bytes currently in the cache / maximum bytes
   configured` printed after every phase; abort if it exceeds ~70%
   (comfortably under WiredTiger's 80% eviction target). Above that, WT may
   start evicting mid-measurement, and a cumulative "resident bytes" snapshot
   silently stops meaning "everything touched since the restart" — it would
   also mean the T1–T4 deltas are no longer strictly additive.
4. **The two untouched indexes stay untouched.** `created_at` and
   `idempotency_key` should show zero contribution to residency growth
   across all three phases (nothing queries them). Printed explicitly as a
   sanity check — if either grows, something in the harness (or in MongoDB)
   is touching an index nothing asked for, and the T4 comparison in the
   table above is invalid until that's explained.

None of these existed in the two harnesses that previously "measured
nothing" ([#8](https://github.com/gmhoward9289-ops/xycalc/issues/8)) — this
plan is explicit about which one would have caught which failure mode: (1)
would have caught mongosh silently serialising or mis-planning; (2) and (3)
are the direct analogues of the working-set and page-cache guards that
`ticket_probe.py`/`celery_probe` already carry, re-derived for a residency
question instead of a throughput one.

## 6. What lands in the corpus

Conditional on outcome — do not pre-decide the number:

- **If `(T2−T1)` and `(T3−T2)` both land within ~15% of their `indexSizes`
  entries:** a new source (`data/sources/<host>-covered-query-probe-<date>.yaml`,
  `source_type: benchmark`, notes naming `tools/bench/covered_query_probe.sh`)
  and an observation
  (`data/observations/<host>-covered-query-probe-<date>.yaml`) recording the
  ratios. No new coefficient — this *confirms* the model's existing
  "no multiplier needed for indexes" structure for the first time, which is
  itself worth having; the model currently asserts this with zero
  supporting measurement.
- **If either index shows a real, consistent expansion** (materially outside
  that band): a new coefficient, e.g. `mongodb.index-cache-expansion-ratio`
  — parameter `cache.index_expansion_ratio`, dimension `ratio` — grade
  `measured` (not `benchmark`; `benchmark` is a `source_type`, not a
  `coefficient.confidence` value in this schema — the coefficient's grade is
  `measured`, citing a `source_id` whose `source_type` is `benchmark`).
  `applies_to: MongoDB 7.0.39, single-threaded read path, this dataset shape
  (events-shaped, ~700-byte documents, 4 indexes) — n=1`. Explicitly **not**
  promotable beyond that scope on this one run, same discipline 001 already
  applied to its own compression measurement: one synthetic dataset does not
  license a general correction.
- **If the two indexes disagree with each other** (e.g., the low-cardinality
  compound index expands more than the high-cardinality one, or vice versa):
  report both, unresolved, the same way 001 reported the compression range
  as a disagreement rather than averaging it away. That result alone would
  be worth a `notes:` on the model even before a coefficient is proposed,
  because it means "index bytes ≈ indexSize" is shape-dependent the same way
  the compression ratio is (T2's subject) — a second instance of the same
  pattern, not a coincidence.
- **`(T4−T3)` vs `dataSize`** feeds the same model regardless of the index
  results: if it lands near 1.0, that's the first direct (uncoefficiented)
  confirmation that the cache holds decompressed document bytes and nothing
  structurally more; if it doesn't, that residual is evidence for a generic
  cache-overhead term (B-tree/page-structure bytes) that would apply to the
  model as a whole, not to the index term specifically — 001's FINDINGS
  already named this as the leading alternate hypothesis for the 13.9% and
  explicitly declined to add a term for it on n=1. This experiment is a
  second n=1 on the same hypothesis, not a second independent dataset — say
  so if it's what gets reported.
- Either way: `docs/investigations/005-covered-query-cache-residency/BRIEF.md`
  and `FINDINGS.md`, cross-linking `001-wiredtiger-cache/FINDINGS.md`'s
  "weakest inference" section rather than editing it — 001 is marked
  complete and its number should stay a record of what was known when it
  was written.

## 7. Effort and dependencies

- Harness (`covered_query_probe.js` + `.sh`): 1–2 hours. Simpler than
  `ticket_probe`'s pair — no cgroup throttle, no thread pool, no concurrency
  sweep, single container.
- Run: load (~1 min, per the existing generator's own batch pace) + restart
  (~15s) + three phases with a plateau re-run each, single-threaded over
  500k documents — likely under 15 minutes wall-clock end to end. Small
  enough to smoke-test with `PROBE_DOCS` lowered before committing to the
  full run, the same escape hatch `ticket_probe.sh` gives.
- Write-up: 45–60 minutes.
- **Total: well under a day**, single session, no container fleet, no
  swamplink scheduling needed beyond one Docker host.
- **Depends on nothing.** Reuses `mongodb_load.js` as-is. Does not block or
  get blocked by any other open roadmap item (T1–T4, T6–T10). Loosely
  related to [#5](https://github.com/gmhoward9289-ops/xycalc/issues/5) and
  T2 (both are about the same class of question — does a coefficient
  generalize across data shape — for the compression ratio rather than the
  index term) but nothing here needs either to land first.

## 8. What could make this not worth doing

The index term is a small lever on the final answer even if it's wrong. In
001's own validation, `indexSize` (0.057 GB) was about a fifth of `dataSize`
(0.299 GB); even a 2x error in how index bytes expand in cache would move the
total answer by a much smaller fraction than the ~64% error already found in
the *compression* coefficient. If the honest read of this experiment is
"index residency tracks indexSize within noise, and document residency
tracks dataSize within noise" — i.e., every ratio in §4 comes back near 1.0 —
then the 13.9% gap survives, unexplained, and this experiment's contribution
is to rule out two plausible causes rather than fix the number. That's a real
possible outcome, not a strawman: it's explicitly the "supported" branch in
§3. It is still worth running, because it's cheap (under a day, no
infrastructure dependency, no other issue blocks or is blocked by it) and
because ruling out both obvious candidates for a named, previously-guessed-at
error term is progress even when it doesn't ship a coefficient — but it
should not be sold going in as likely to overturn `mongodb.wt-cache`'s
headline number the way T1, T4, or T7 might overturn theirs. It is a
correctness/auditability task, not a "this changes the sizing answer" task,
and the plan should not pretend otherwise.
