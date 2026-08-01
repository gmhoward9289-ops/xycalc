# Plan — Issue #11 (Roadmap T3): write rate vs. `eviction_dirty_trigger`

**Issue:** [#11](https://github.com/gmhoward9289-ops/xycalc/issues/11), labels
`investigation`, `roadmap`. **Spec:** `docs/investigations/ROADMAP.md`, T3.
**Touches (not blocks):** #2 (ticket pool not pinned on 7.0 — app-thread
eviction is the mechanism that conscripts tickets in that story), #12 / T4
(checkpoint sawtooth — this issue's checkpoint-interval finding is an input to
that one, not a dependency in either direction).

---

## 1. The question

**As a person would ask it:** "If I write to MongoDB fast enough, at what
point does my own query threads start doing the disk-writing work that
eviction is supposed to do in the background — and is that really at 20% of
the cache being dirty, like the docs say, or does it kick in earlier?"

## 2. What would falsify it

The issue's premise is: `eviction_dirty_trigger` (20%, `documented`,
MongoDB 6.0) is a real, measurable, operative threshold, and nobody has
checked it. That premise is checkable and can fail in either direction:

- **The trigger binds well below 20% dirty.** `pages evicted by application
  threads` goes non-zero while `tracked dirty bytes in the cache` is still,
  say, 8–12% of `maximum bytes configured`. This falsifies "20% is the
  operative threshold" and means every model or mental model built on that
  number understates the risk.
- **The trigger never binds at any write rate the harness can sustain**, even
  well past 20% dirty, because checkpoints (default every 60s) or background
  eviction absorb it first. This would falsify "bulk loads hit this
  routinely" as a *mechanism* problem rather than a *scale* problem — it
  would say the real-world symptom is caused by something else (write
  concern timeouts, journal contention, lock contention) and this constraint
  is a red herring in practice, at least at reachable synthetic rates.
- **It binds, but not because of the *dirty* trigger.** If application-thread
  eviction starts only once `bytes currently in the cache / maximum bytes
  configured` crosses the unrelated 80%/95% *overall* thresholds
  (`mongodb.eviction-target-pct` / `mongodb.eviction-trigger-pct`, already in
  the corpus), while dirty% is nowhere near 20%, the experiment has measured
  the wrong trigger and attributed it to this one. This is not named in the
  issue's Method section and is the likeliest way this experiment produces a
  plausible-but-wrong table — see §4.

A result that just says "yep, roughly 20%, checks out" is not a failure to
find something interesting — it is the first real observation against a
`documented` figure that currently has **zero** observations. Confirmation is
a legitimate, valuable outcome here, same as it was for the ticket-ceiling
model's core claim.

## 3. Method

Extend the `tools/bench/ticket_probe.{py,sh}` pattern — same Docker
orchestration skeleton (unique per-run container names, cleanup trap that
warns rather than destroys a concurrent run, cgroup block-IO throttle scoped
to the mongod container only, container memory bound to stop the host page
cache from absorbing the workload, wait-for-ready loop, real OS threads via
pymongo rather than mongosh) — but the workload and the sweep axis are
different enough that this should be a **new harness**, not a flag on the
existing one: `tools/bench/eviction_probe.py` + `tools/bench/eviction_probe.sh`.

**Why a new harness rather than extending ticket_probe:** ticket_probe sweeps
*concurrency* against a *read* workload and needs the dataset to oversubscribe
the cache so random reads miss. This experiment sweeps *sustained write rate*
against a write workload, and — this is worth being explicit about, because
it is a real design difference, not a stylistic one — it does **not** need
dataset oversubscription the way the read probe did. Dirty bytes accumulate
from the rate of *newly written, not-yet-checkpointed* pages, which can
exceed 20% of a cache within one checkpoint interval regardless of how big
the total dataset ends up relative to cache size. The guard this harness
needs is a write-rate-vs-device-throughput comparison, not a
dataset-size-vs-cache-size one.

**Two workload arms, same harness, selected by an env var:**

1. **Insert (primary).** Sustained bulk insert at a target rate — this is the
   literal motivating scenario ("bulk loads hit it routinely"). Collection
   grows during the run. Drop and recreate the collection between rate
   levels so each level starts from the same (empty) dirty-byte baseline —
   deliberately unlike `ticket_probe.sh`, which carries ticket state forward
   between levels on purpose because the pool's climb *was* the object of
   study. Here, letting one level's residual dirty pressure bleed into the
   next would contaminate the onset measurement.
2. **Update (confirmation).** Pre-load a fixed working set, then sustained
   in-place `$set` of a ~1 KB field on a random `_id` at a target rate. No
   growth, so this arm isolates the dirty-trigger mechanism from allocation
   and file-growth effects, and is the cleaner one to reason about if the
   insert arm's results are ambiguous.

Run insert first; only build and run the update arm if insert's result needs
disambiguating (e.g., trigger point unclear, or need to rule out that
allocation overhead rather than dirty-byte accounting caused the onset).

**Container / device config** (env-overridable, mirroring `ticket_probe.sh`'s
`PROBE_*` convention):

- `mongo:7` (MongoDB 7.0.x), matching the rest of `tools/bench/`.
- `--wiredTigerCacheSizeGB 0.25` (`PROBE_CACHE_GB`).
- `--memory 640m --memory-swap 640m` (`PROBE_MEMORY`) — same page-cache trap
  as the read probe; writes go through the OS page cache before device
  writeback (fsync at journal commit / checkpoint), so an unbounded page
  cache could let WiredTiger's checkpoint report "flushed" against RAM while
  the throttled device never actually saw the bytes in this window. Bound it
  tight enough that this can't happen silently.
- `--device-write-bps` / `--device-write-iops` (new — `ticket_probe.sh` only
  throttles reads). Default e.g. 4 MiB/s / 100 IOPS
  (`PROBE_WRITE_BPS` / `PROBE_WRITE_IOPS`), tunable.

**Sweep axis:** target write rate expressed as a **multiple of the configured
device write throughput**, not an absolute ops/sec guess — e.g.
`[0.25, 0.5, 1, 2, 4, 8] × PROBE_WRITE_BPS`. This guarantees the crossover
sits inside the sweep regardless of which absolute throttle value is chosen,
the same reasoning T1 uses for sweeping working-set size as multiples of
cache size rather than absolute GB.

**Rate limiting:** N worker threads, each pacing itself to `rate / N` ops/sec
(sleep-to-target-interval, tracking achieved vs. target rate so the probe can
report if the limiter itself fell behind — that's a real failure mode above
the device throughput, and it should be visible in the output rather than
silently producing a lower effective rate than the label claims).

**Duration per level: at least 3× the checkpoint interval, not
`ticket_probe`'s 25s.** MongoDB's default checkpoint interval
(`storage.syncPeriodSecs`) is 60s — a level shorter than that risks measuring
mid-cycle, and a level only slightly longer risks catching a single flush
that empties the dirty buffer and manufactures a false "never binds" result
even at a rate that would trip the trigger given a second cycle. This is the
write-side analogue of the exact problem `FINDINGS.md` documents for
`totalTickets` never reaching a steady value within a 25s window — same
failure shape (measuring a transient and calling it steady state), different
knob. Recommend 180–200s per level. **Confirm the 60s default against a
MongoDB 7.0-specific source before relying on it** (see §5 — it is currently
uncited in this corpus at all).

**Metrics, sampled every 1–2s throughout each level** (coarser than
`ticket_probe`'s 0.5s is fine; write dynamics here span tens of seconds, not
milliseconds):

- `wiredTiger.cache["tracked dirty bytes in the cache"]`, and that as a
  fraction of `"maximum bytes configured"` — the number this whole
  experiment is about.
- `wiredTiger.cache["bytes currently in the cache"]` / `"maximum bytes
  configured"` — **overall** occupancy, sampled alongside dirty%
  specifically so onset can be attributed to the right trigger (§2, §4).
- `wiredTiger.cache["pages evicted by application threads"]` (cumulative;
  take the delta per sample) — the binary "app threads are doing storage
  work now" signal named in the issue.
- `wiredTiger.cache["eviction server unable to reach eviction goal"]` —
  precedes app-thread conscription per `docs/telemetry/mongodb.md`; useful
  as a leading indicator in the trace.
- Achieved insert/update rate (from the workload itself) vs. target rate.
- Device write bytes/sec actually observed (see §4 — this is the guard
  counter, not optional telemetry).

**Commands:**

```bash
./tools/bench/eviction_probe.sh                          # full run, insert arm
PROBE_ARM=update ./tools/bench/eviction_probe.sh          # confirmation arm
PROBE_SECONDS=20 PROBE_RATES=0.5,1,2 ./eviction_probe.sh  # smoke run first
```

Smoke run before the real one, same as `ticket_probe.sh`'s own history —
that harness needed two failed smoke runs before it produced a real result,
and there is no reason to expect this one to need fewer.

## 4. The guard

**What would this print if the thing being measured never happened?** A
clean table where `evictedByAppDelta` climbs from 0 to something positive as
the rate sweep goes up, dirty% crosses ~20% right around the same row, and
the reported rate matches the requested rate. That table is exactly as
plausible whether it's real or an artifact of any of the following, so each
needs its own loud check, not an eyeball of the summary:

1. **Page cache absorbing the writes, not the throttled device.** Same trap
   that cost two runs of `ticket_probe`. Guard: read `io.stat` (cgroup v2)
   for the mongod container's write bytes during each level and assert it
   is actually pinned near the configured `--device-write-bps` at the
   higher rate levels. If observed device write throughput never approaches
   the configured cap, the throttle never engaged and the run is vacuous —
   refuse to publish results from that level, the same way `ticket_probe.py`
   refuses to run below `MIN_OVERSUB`.
2. **Wrong trigger attributed.** Per §2's third falsification case: sample
   overall cache occupancy alongside dirty%. If app-thread eviction's onset
   lines up with overall occupancy crossing 80%/95% rather than dirty%
   crossing 20%, say so plainly — that is a different, already-modelled
   constraint, and reporting it as a dirty-trigger finding would be a
   misattribution error of exactly the kind investigation 001/003 have
   already been burned by once each.
3. **Rate limiter falling behind.** If achieved rate is well under target
   rate at the higher levels (worker threads can't keep up with their own
   pacing target under load), the "sweep" silently degenerates into fewer
   effective levels than requested, and a flat result at the top of the
   range would look like a ceiling when it's actually the harness, not
   MongoDB. Guard: log achieved vs. target rate per level and flag any level
   where they diverge by more than ~10%.
4. **Measuring inside one checkpoint cycle.** Covered in §3 — durations
   below ~3 checkpoint intervals can manufacture either a false trigger
   (measuring mid-accumulation, before a flush that would have relieved it)
   or a false non-trigger (measuring right after a flush). Guard: report
   dirty% as a time series per level, not just a single end-of-level number,
   so a reviewer can see whether it plateaued or was still moving when the
   window closed — same discipline `FINDINGS.md` had to retroactively apply
   to `totalTickets` after the fact. Do it from the start here.
5. **`PROBE_ARM=insert` collection growth confounding cache pressure with
   dataset-size pressure.** If the insert arm's dataset grows past the
   *overall* cache size during a long high-rate level, overall eviction
   (guard #2's failure mode) becomes likely for an unrelated reason — the
   cache is just filling up with new data, dirty or not. Cap total inserted
   bytes per level to something safely under cache size (e.g. ≤50% of
   `PROBE_CACHE_GB`) so a positive result can't be explained by "the cache
   simply ran out of room."

If none of guards 1–5 fire and dirty% still tracks the onset cleanly, the
result is real.

## 5. What lands in the corpus

- **`mongodb.checkpoint-interval-seconds`** (new coefficient). MongoDB's
  default checkpoint interval, `storage.syncPeriodSecs` = 60s. `documented`,
  cheap, high-confidence, and currently **absent from the corpus entirely**
  despite being load-bearing for this experiment's duration design and for
  T4/#12's checkpoint-sawtooth work. `applies_to`: needs a version-matched
  citation — confirm the default against a MongoDB 7.0-specific manual page
  (the corpus currently cites `source.wiredtiger.com/mongodb-6.0/` for the
  eviction thresholds; a 7.0-specific WiredTiger tuning page has not been
  checked for either the checkpoint interval or a possible change to the 20%
  dirty trigger itself). **Do this cheap check before the benchmark, not
  after** — it's a documentation read, not a build.
- **`mongodb.eviction-dirty-trigger-pct`** (existing, `documented`,
  `applies_to: MongoDB 6.0`) — not overwritten. Land the run's result as a
  **new observation** (`data/observations/`) against `parameter:
  cache.eviction_dirty_trigger_pct`, `system_version: 7.0.x`, sourced to a
  new `source_type: benchmark` entry citing `tools/bench/eviction_probe.py`
  per `tests/test_corpus.py`'s requirement that benchmark sources name their
  harness. If the observed onset disagrees materially with 20%, record that
  as a disagreement in the coefficient's own `notes` (documented figure vs.
  measured onset, side by side, no winner declared) rather than editing the
  value — `documented` means the vendor states it, and the vendor still
  does; a 7.0.39 measurement differing from a 6.0-documented default is a
  version-drift finding, not a correction.
- **`mongodb.write-rate-device-bound-ratio`** (new coefficient, likely
  grade `benchmark`). Investigation 003's read-side finding was that
  throughput pins to the device once the ticket ceiling binds, regardless of
  offered concurrency. The symmetric, testable claim on the write side is
  that sustained *achievable* write rate converges to ≈ the device's
  sustained write throughput once app-thread eviction is active, regardless
  of offered write rate above that point. Report the actual ratio observed
  (could be ~1.0, or could be higher if batching/journal buffering lets
  MongoDB sustain more than raw device bps for a while — that would itself
  be a real, interesting finding, not a bug). `applies_to` must name both
  the MongoDB version **and** that this was measured under a synthetic
  cgroup write throttle, not a real device — do not imply it generalizes to
  EBS or NVMe without saying so.
- **`mongodb.write-rate-ceiling`** (new model, `data/models/mongodb-write.yaml`).
  Shape mirrors `mongodb.ticket-throughput-ceiling`: a floor (device
  sustained write throughput, supplied as an input — this is a deployment
  fact, not something the corpus can know in advance) and a **constraint**
  term (`role: constraint`, `apply: note`) describing the dirty-byte grace
  period before conscription, built from `mongodb.checkpoint-interval-seconds`
  and `mongodb.eviction-dirty-trigger-pct` — deliberately **not** wired into
  the computed arithmetic on this landing. The exact grace-period formula
  (dirty budget ÷ net inflow rate) depends on numbers this experiment
  produces and has a genuine failure mode — dividing when net inflow is
  negative or zero — that shouldn't be designed blind before the data
  exists. Land it as a documented, human-readable constraint first; promote
  to computed once the coefficient's band is known and stable. State
  `unvalidated (n=0)` on landing regardless of whether its founding
  coefficient came from this same benchmark run — a model needs an
  independent validation case, not just a coefficient with the same
  birthday, exactly the distinction `FINDINGS.md` draws for
  `mongodb.ticket-throughput-ceiling`.

## 6. Effort and dependencies

**Effort:** roughly one working day (6–9 hours), same order of magnitude as
investigation 003's fault-injection work:

- ~30 min: confirm checkpoint-interval default and dirty-trigger default
  against a MongoDB 7.0-specific source before writing any code.
- ~2–3 hr: build `eviction_probe.py` / `.sh` off the `ticket_probe` skeleton,
  including the `io.stat` device-throughput check and the two-arm
  insert/update selection.
- ~2–4 hr: smoke runs, guard failures, real sweep, likely a repeat run on a
  separate occasion given `ticket_probe`'s own experience with host
  contention between overlapping runs (see `FINDINGS.md`'s "Reproduced, and
  a caveat about how").
- ~1 hr: land coefficients/observations/model YAML, `xycalc build && xycalc
  audit && pytest -q`.
- ~1 hr: `FINDINGS.md`.

**Dependencies:** none. Runs standalone on one Linux box with Docker, per the
ROADMAP's own constraint. Not blocked by and does not block any other open
issue. **Related, not blocking:** #2 (app-thread eviction is the mechanism
that conscripts read tickets in the feedback loop `FINDINGS.md` describes —
this issue's onset measurement is a useful input to eventually closing that
loop's write-side half, but neither issue needs the other to land first);
T4/#12 (shares the checkpoint-interval fact; sequencing them together would
save the ~30 min research step once, not required).

## 7. What could make this not worth doing

The mechanism-level result is worth having regardless of outcome — it is a
`documented` figure with zero observations, cited in a `constraint` role in
the corpus's flagship model's own write-side story, and the ROADMAP already
prioritizes it. The honest caveat is narrower: **this experiment validates
the mechanism on a synthetic, deliberately-throttled write path. It does not
by itself say how close typical production write rates get to typical disk
write throughput** — that requires real telemetry (`tracked dirty bytes in
the cache`, already listed `obtainable` in `docs/telemetry/mongodb.md`) from
an actual deployment, which is a "bring your own observation" ask outside
this issue's scope, the same category as #5's request for real compression
samples. If nobody ever supplies that telemetry, `mongodb.write-rate-ceiling`
stays a mechanism demonstration rather than an operationally load-bearing
model — worth shipping either way, since it converts an unmeasured,
cited-since-001 assumption into a checked one at low cost (one Docker box,
one day), but the summary to George should say plainly that this run answers
"does the trigger work as documented" and not "how worried should I be about
hitting it" — those are different questions and only the first is in scope
here.
