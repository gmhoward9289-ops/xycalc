# Plan — Issue #18 / Roadmap T10: ClickHouse insert-frequency ceiling

Status: proposal only. Nothing here has been run. No figure below is in the
corpus yet.

## 1. The question, as a person would ask it

"I'm about to switch a ClickHouse loader from hourly batch files to
row-at-a-time streaming — will that break ingestion, and at what point?"

More precisely: at fixed total row volume, does shrinking the batch size (i.e.
raising insert frequency) drive MergeTree's active-part count past
`parts_to_delay_insert` and then `parts_to_throw_insert`, and where do those
crossovers sit for a pre-23.6 image versus a 23.6+ image?

## 2. What would falsify it

Two independent claims are on the table; either can fail on its own.

**Claim A — frequency, not volume, is the driver.** If active part count in
the target partition tracks *total rows inserted* rather than *number of
insert statements issued* — i.e. if a run of 500k rows in one batch produces
roughly the same part count as 500k rows in 500k single-row batches, once
merges have had a chance to run — then "streaming breaks ClickHouse" is wrong
as stated, and the real variable is something else (total volume, partition
key choice, disk speed). This is the falsifier named in the roadmap entry and
it is the one to weight most heavily.

**Claim B — the 23.6 default change actually moves the crossover.** If the
insert-frequency point at which delay/reject engages does not shift by
roughly the documented ~10x between the two images, one of two things is true:
either the settings query used to confirm the images differ was wrong (see
guard, item 6), or the *effective* ceiling is set by something else entirely
(background merge throughput, not the threshold) and raising the threshold
buys nothing in practice — which would itself be a finding worth having,
because it means the 23.6 change is weaker protection than the changelog
implies.

If both claims survive, the roadmap entry's premise is confirmed. If Claim A
fails, the whole investigation is void. If Claim A holds but Claim B fails,
that is not a wasted run — it is a sharper and more useful finding than the
one that was asked for.

## 3. Method

**No existing harness fits.** `tools/bench/ticket_probe.py` is
MongoDB/pymongo-specific end to end (ticket counters, WiredTiger cache
fields, `serverStatus`), and `tools/bench/celery_probe/` drives Celery against
that same Mongo setup. Nothing in either talks to ClickHouse. What *does*
transfer, and should be copied deliberately rather than re-invented, is the
shape: a guarded, disposable, uniquely-named Docker harness that refuses to
run when its own precondition isn't met and prints a JSON blob after a
`===JSON===` marker. Build `tools/bench/clickhouse_probe.sh` +
`tools/bench/clickhouse_probe.py` as a new pair on that template — same
unique-name-per-run + trap-based cleanup as `ticket_probe.sh`, same
refuse-to-run guard pattern as `ticket_probe.py`'s `MIN_OVERSUBSCRIPTION`
check.

**Images.** One pre-23.6 tag (e.g. `clickhouse/clickhouse-server:23.3`) and
one 23.6+ tag (e.g. `clickhouse/clickhouse-server:24.8`, or whatever the
current LTS is at run time). **Do not trust the tag name for the defaults.**
Immediately after each container starts, run:

```sql
SELECT name, value FROM system.merge_tree_settings
WHERE name IN ('parts_to_delay_insert', 'parts_to_throw_insert');
```

and assert the two containers report different values before the sweep
starts (see guard, item 6). Pin CPU explicitly and identically on both
containers (`--cpus=2 --memory=2g` as a starting point, tune during smoke
testing) — merge throughput is CPU-bound, and an unpinned container makes
this an uncontrolled variable across a run, let alone across the two images.

**Table.** `MergeTree() ORDER BY id`, deliberately **no `PARTITION BY`**, so
the whole table is one partition ("all") and every inserted part competes for
the same threshold. A partition key that varies during the run (e.g. an
advancing timestamp) would spread parts across partitions and silently
neuter the experiment — see guard, item 4.

**Client.** A Python client with persistent connections (`clickhouse-connect`
or `clickhouse-driver`), driven by a fixed pool of concurrent writer threads
(propose 8, tune in smoke testing), never a spawned `clickhouse-client`
process per insert. Spawning a process per row would make client-side fork
overhead the bottleneck long before ClickHouse's own limits are reached — the
same trap `ticket_probe.py`'s docstring names for `mongosh` (auto-awaited
calls serialize "concurrent" work and measure nothing). Explicitly confirm
`async_insert = 0` on the connection before the run — see guard, item 1.

**Sweep.** Batch size ∈ {1, 10, 100, 1_000, 10_000, 100_000} rows per
`INSERT`, fixed total row budget `R` per step (same `R` for every batch size
— the point of "fixed total rows" is that only the chunking changes). Start
`R` at 300,000 as a working guess and tune it in a smoke run: it must be large
enough that batch=1, spread across 8 concurrent writers, drives active part
count for the *lower* threshold (150, pre-23.6) well past the ceiling before
the row budget is exhausted, but small enough that the batch=100,000 step
(only 3 `INSERT`s total) finishes in seconds. Cap each step's wall clock at
120s regardless of whether `R` was reached — a step stuck emitting rejected
inserts must stop and report "did not complete," not spin.

Per step, drop and recreate the table (clean slate — no carryover parts from
the previous batch size), then poll every 250ms for the duration:

- `SELECT count() FROM system.parts WHERE table = 'probe' AND active` — active
  part count, the central measurement.
- `SELECT count(DISTINCT partition) FROM system.parts WHERE table = 'probe'`
  — must stay at 1 throughout (guard, item 4).
- Diffs of whatever `system.events` counters this ClickHouse version exposes
  for insert delay/rejection — **name to be confirmed at experiment time**
  against the running version (`SELECT event FROM system.events WHERE event
  ILIKE '%insert%' OR event ILIKE '%part%'`), not assumed in advance. This
  document intentionally does not assert an exact counter name it hasn't
  verified.
- Per-insert client-observed latency, and the literal exception text of any
  failed `INSERT` (ClickHouse raises a "Too many parts" `DB::Exception`) —
  count these, don't just note that latency rose.

Run the full six-batch-size sweep once per image. Total: 12 steps, plus two
settings-confirmation queries.

## 4. The guard

**What would this print if the thing being measured never happened?** A flat
table: active part count staying low and roughly constant across every batch
size, latency flat, zero caught "Too many parts" exceptions — indistinguishable
by eye from "confirmed: batching doesn't matter at this scale," when the real
cause is one of the following. Each gets an explicit, loud check; the harness
should raise and abort the step (not print a clean row) when one fires.

1. **`async_insert` absorbs the burst server-side.** If async insert is on
   (client default or server default varies by version), many single-row
   client calls get coalesced into fewer actual parts by ClickHouse's own
   async-insert queue — the exact mechanism under test disabled without
   anyone noticing. Check: assert `SELECT value FROM system.settings WHERE
   name = 'async_insert'` reads `0` for the session before the sweep starts.
   Additionally, sanity-check that observed new-parts-created is within a
   small multiple of INSERT statements issued at each step — if it's far
   lower, something coalesced them regardless of the setting.

2. **Client-side overhead, not the server, is what's slow.** If per-request
   overhead (connection setup, Python overhead) throttles achievable
   inserts/sec at batch=1 well below what a real streaming producer would
   sustain, part count may simply never accumulate fast enough within the
   wall-clock cap — a "healthy" result that only proves the test harness was
   the bottleneck. Check: use persistent, pooled connections (not one socket
   per insert), and record the *achieved* inserts/sec at each batch size in
   the output. If batch=1 achieves an implausibly low rate, that number is
   itself the finding to report, not something to paper over — say so rather
   than silently raising concurrency until the "right" result appears.

3. **Merges keep up because the box is too fast for the test budget.** If the
   container's CPU/disk consolidates parts faster than even batch=1 can
   create them, the threshold is never crossed inside `R` rows or the 120s
   cap, producing a flat table that means "the test didn't apply enough
   pressure," not "batching doesn't matter." Check: require the batch=1 step
   specifically to drive active part count above the *lower* of the two
   thresholds for that image (150 pre-23.6) at least once during the run;
   abort with an explicit "REFUSING TO CONCLUDE" message and a suggestion to
   raise `R` or writer concurrency if it doesn't — the same posture as
   `ticket_probe.py`'s `MIN_OVERSUBSCRIPTION` refusal.

4. **Parts spread across partitions.** An accidental partition key (or one
   that advances during the run) divides the count across many partitions,
   each individually under threshold, hiding a real aggregate problem behind
   several small, healthy-looking numbers. Check: `count(DISTINCT partition)`
   polled alongside part count; abort if it ever exceeds 1.

5. **Latency alone is a false positive.** Insert latency can rise for reasons
   that have nothing to do with `parts_to_delay_insert` — Docker CPU
   scheduling, network jitter, connection-pool exhaustion in the client — and
   would look identical to ClickHouse's internal delay sleep on a latency
   graph alone. Check: credit a "delay" only when latency rise coincides with
   both the part-count crossing the documented threshold *and* server-side
   evidence (the confirmed `system.events` counter, or the literal message in
   `system.text_log`). Credit a "reject" only from the caught client-side
   exception text, never inferred from a latency spike.

6. **The two images don't actually differ.** If both tags resolve to the same
   effective settings (tag aliasing, or picking two patch releases on the
   same side of 23.6 by mistake), the "before/after" comparison silently
   becomes the same experiment run twice, and two plausible-looking but
   uninformative tables result. Check: the `system.merge_tree_settings` query
   in the Method section, asserted to differ, run and logged *before* any
   sweep step executes on that container.

## 5. What lands in the corpus

Two things land independently of each other and can ship on different
timelines — worth doing that way rather than gating the cheap one on the
expensive one (see §7).

**Cheap, benchmark-independent, do first:** the two documented coefficient
pairs, sourced directly from ClickHouse's own settings documentation (no
container required) —

- `clickhouse.parts-to-delay-insert`, two rows:
  `applies_to: ClickHouse <23.6 (parts_to_delay_insert default)`, value 150;
  `applies_to: ClickHouse >=23.6 (parts_to_delay_insert default)`, value 1000.
- `clickhouse.parts-to-throw-insert`, two rows: 300 (`<23.6`) and 3000
  (`>=23.6`).
- Grade `documented`, contingent on locating and quoting the actual settings
  page during research (step 3 of `.claude/skills/xy-investigate/SKILL.md`) —
  this plan does not assert the source sentence because it hasn't been
  fetched yet, only the values already stated in the roadmap issue text as
  the working hypothesis.
- New parameters in `data/parameters.yaml`:
  `clickhouse.active_parts_delay_threshold`,
  `clickhouse.active_parts_throw_threshold` (dimension: count).
- New file `data/coefficients/clickhouse.yaml` and `data/models/clickhouse.yaml`
  — this system currently has zero coefficients, so these are the first.
  New investigation directory, next available number as of this writing —
  `docs/investigations/005-clickhouse-insert-batch-floor/` — **check for
  collisions before creating it**, other roadmap items being planned in this
  same session may also claim a 005 slot.

**Requires the benchmark:** a `clickhouse.insert-batch-floor` model
(`data/models/clickhouse.yaml`) carrying the measured crossover — the
insert-frequency point at which delay/reject engaged in this specific run.
This must be graded `benchmark` (per `tests/test_corpus.py`'s requirement
that a `benchmark`-graded source name its harness under `tools/bench/`), and
**its `applies_to` must name the test rig, not just the ClickHouse version** —
container CPU/memory allocation, e.g. `ClickHouse 24.8, 2 vCPU / 2 GiB
container`. Merge throughput is a function of local CPU and disk; the
absolute inserts/sec floor this experiment measures does not generalize to
arbitrary hardware the way the two threshold constants above do. Grading it
`benchmark` with a hardware-scoped `applies_to` is the honest move — grading
it `documented` or leaving `applies_to` as just the ClickHouse version would
overclaim portability the evidence doesn't support.

Also worth landing regardless of outcome: whichever `system.events` counter
name is confirmed live (item 3 in §4) belongs in `docs/telemetry/clickhouse.md`
(new file) the same way `docs/telemetry/mongodb.md` records
`wiredTiger.concurrentTransactions` fields — this is exactly the kind of
"series the investigation wished it had" `SKILL.md` step 5 asks for.

## 6. Effort and dependencies

No dependency on any other roadmap item or open issue; ClickHouse is an
untouched stub system, so nothing here conflicts with MongoDB/EBS/Celery work
in flight. Does not block anything else either.

Rough wall clock, assuming no ClickHouse-specific surprises:

- Coefficient landing (docs-only, no container): 30–45 min.
- New harness (`clickhouse_probe.py` + `.sh`, on the `ticket_probe` template):
  3–5 hours.
- Smoke testing to tune `R` and writer concurrency so batch=1 actually trips
  the threshold without the run taking forever: 1–2 hours, expect 2–3
  iterations — the MongoDB harness needed exactly this kind of tuning to get
  oversubscription right, and there is no reason to expect ClickHouse needs
  less.
- Full sweep (12 steps, capped at 120s each, plus image pulls and table
  setup/teardown): well under an hour of pure run time.
- Model YAML + FINDINGS.md: 2–3 hours.

Call it **most of a day** for the full benchmark, or **under an hour** for
just the documented coefficients if the benchmark is deferred.

## 7. What could make this not worth doing

**The premise, as written, slightly overclaims portability.** The roadmap
entry's "Corpus gets... a `clickhouse.insert-batch-floor` model" reads as if
this experiment produces a portable "too few rows per insert" number. It
can't — merge throughput depends on the box, so the floor this benchmark
measures is honestly local to a 2 vCPU container, not a general-purpose
coefficient. That doesn't make the experiment pointless, but it does mean the
most valuable, durable output is *not* the floor number — it's (a) the two
documented threshold coefficients, which are portable and citeable with zero
benchmarking, and (b) the mechanism confirmation and version-drift
demonstration, which the issue itself says is worth having "even [as] the
best version-drift example the corpus will ever get." If time is tight, (a)
alone is worth doing on its own and does not need this whole plan — it's a
half-hour docs task. The benchmark's marginal value on top of that is
confirming causality (Claim A) and the magnitude of the version-drift effect
(Claim B), not discovering the threshold numbers, which are already known
from vendor docs.

**This is a well-trodden ClickHouse failure mode.** "Too many parts" is one
of the most commonly discussed ClickHouse operational problems in
practitioner writing. Running the benchmark won't surprise anyone who has
operated ClickHouse; its value here is specifically that this corpus doesn't
accept practitioner folklore without a citation and a version — which is the
whole thesis of the project, so this is a reason to still do it, not a reason
to skip it. But it's worth naming plainly: this experiment is unlikely to
overturn anything, unlike the ones the ROADMAP explicitly flags as
overturn-candidates (#9, #12, #15). It confirms and dates a known mechanism
rather than discovering a new one.
