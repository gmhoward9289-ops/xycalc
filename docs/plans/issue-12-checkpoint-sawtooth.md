# Issue #12 (T4) — Is investigation 003's flat throughput actually flat?

GitHub: `blocked-by:` none, `blocking:` none (checked via `gh issue view 12`).
Labels: `roadmap`, `validation`.

## 1. The question

As a person would ask it: **"MongoDB pauses to checkpoint every so often — does
that show up as a periodic latency spike that investigation 003's 25-second
averages are too coarse to see?"**

## 2. What would falsify it

003's harness (`tools/bench/ticket_probe.py`) reports one mean latency and one
`opsPerSecond` per 25-second window per concurrency level. That is one number
standing in for ~2,500–3,000 individual operations. If WiredTiger's checkpoint
(commonly documented at a ~60s interval — **confirm the live value, do not
assume it**, per the same discipline that already caught this corpus assuming
128 tickets when 7.0 rests at 4) causes a real stall, a 25s mean is long enough
to average two checkpoint cycles into invisibility.

**The premise is falsified if:** per-second p99 latency during the seconds a
checkpoint is actively running is statistically indistinguishable from p99
during seconds it is not — no periodic structure, confirming 003's mean was a
fair summary, not a concealment.

**The premise is confirmed if:** per-second p99 (or p95) rises sharply and
repeatedly in the seconds coinciding with `checkpoint currently running` = 1,
by an amount that would change what a reader takes from 003 — i.e. not a 10%
wobble, but a multiple, the same order of magnitude as the ticket-ceiling
collapse 003 itself documents (40 ops/s vs 128,000 ops/s territory, not
114 vs 108 ops/s territory).

**A real but small effect is a third outcome, not covered by either label
above** — see §7.

## 3. Method

Extend `tools/bench/ticket_probe.py`, don't build a new harness. It already
has the load generator, the two guards that matter (oversubscription ≥ 2x,
`pagesReadIntoCache` > 0), and the Docker/cgroup scaffolding in
`ticket_probe.sh`. What it doesn't have: per-second latency buckets (it
currently computes one mean/p95 over the *whole* window — the exact blindness
this issue is about) and any checkpoint telemetry at all.

**Step 0 — confirm the field names and the real interval, live, before writing
a line of analysis code.** `docs/telemetry/mongodb.md` already got the 7.0
ticket location wrong once (`queues.execution` doesn't exist) and the corpus's
own findings got the ticket floor wrong once (assumed 128, measured 4). Do not
repeat that pattern for checkpoints. On a running 7.0.39 container:

```bash
docker exec <name> mongosh --quiet --eval \
  'JSON.stringify(db.serverStatus().wiredTiger.transaction)'
```

Confirm the exact keys for checkpoint count, checkpoint-in-progress, and
checkpoint duration (expected candidates: `"checkpoints"`,
`"checkpoint currently running"`, `"checkpoint most recent time (msecs)"` —
verify, don't assume the exact strings or that they aren't split across
`wiredTiger.transaction` and a separate `wiredTiger["checkpoint-cleanup"]`
section added in later WT versions). Also read `checkpoint currently running`
a few times a second on an otherwise-idle container to get the actual
interval on this image before picking a run duration.

**Step 1 — code changes to `tools/bench/ticket_probe.py`:**

- Add `checkpoint_state()`, mirroring `tickets()`/`cache_state()`, returning
  the checkpoint count, the running flag, and last-checkpoint duration using
  the field names confirmed in Step 0.
- Change `worker()` to record `(t_epoch, latency_ms)` instead of a bare
  `latency_ms` — the raw material needed to bucket by wall-clock second.
- Change `sampler()` to run at 1s cadence (not the current 0.5s — fine either
  way, 1s matches the issue's ask) and to **also** call `checkpoint_state()`
  each tick, timestamped.
- **Fix the silent-failure bug already in `sampler()`:** it currently does
  `except Exception: pass`. For this experiment that is fatal — see §4. Count
  and surface sampler exceptions instead of swallowing them.
- Add a `run_long(level, seconds)` path (or a `PROBE_MODE=timeseries` branch)
  that: runs one concurrency level for `seconds`; buckets the timestamped
  latencies into 1-second windows; computes p50/p95/p99 per window; joins
  each window against the checkpoint-running flag sampled in that window;
  emits a per-second table (JSON or CSV) instead of one summary row.

**Step 2 — run it.**

```bash
PROBE_LEVELS=8 PROBE_SECONDS=<see below> PROBE_MODE=timeseries \
  ./tools/bench/ticket_probe.sh
```

Same container settings as 003's fault-injection run: 8 MiB/s / 150 IOPS
device throttle scoped to the mongod container only, 640 MB container memory
(bounds page cache so the throttle actually engages — the failure mode that
cost two earlier smoke runs), 0.25 GB WiredTiger cache, 1.5M docs
(≈4.2x oversubscription).

**Concurrency level: 8, not 1 and not 64.** From 003's own table: c=1/2/4 show
no queueing at all (`queuedMicrosDelta` = 0) — the device isn't yet the
constraint an application would feel, so a checkpoint stall would have nothing
to compete with. c=16/32/64 never reached a steady ticket count within a 25s
window (still climbing at window close) — running those for minutes risks
confounding "the pool is still adapting" with "a checkpoint just fired." c=8
is the lowest level where the device is already the bottleneck (queueing
begins, 40.5s cumulative queued time in 25s) and the ticket pool is nearly
steady (5→7, not still climbing). That is the cleanest level to isolate a
checkpoint signal at.

**Duration: floor of 480s (8 min), or 6× the interval confirmed in Step 0,
whichever is larger.** Six cycles gives margin above the guard threshold in
§4 even if the real interval differs from the commonly-cited ~60s.

**Step 3 — analyze and write up**, not as a plot image (this repo has no
plotting dependency and no precedent of shipping images in `docs/`; every
existing FINDINGS.md conveys a time series as a table) but as: a per-second
table condensed to the checkpoint-active seconds and a matched sample of
checkpoint-inactive seconds, p50/p95/p99 for each group, and the ratio.
Update `docs/investigations/003-storage-stall-query-collapse/FINDINGS.md`
with the result either way — confirming or qualifying it is the deliverable,
not a new document.

## 4. The guard

**What would this print if the sawtooth never happened, or if the experiment
never actually watched for one?** The same thing: a flat per-second p99
series. That is the trap, and it is not hypothetical — the code being
extended already has the exact bug that produces it. `sampler()` in
`ticket_probe.py` today reads:

```python
try:
    samples.append(tickets())
except Exception:
    pass
```

If the checkpoint field names from Step 0 are wrong (plausible — this corpus
has been wrong about a WiredTiger field location once already, on this exact
version), every call to `checkpoint_state()` raises, every sample is silently
dropped, and the run finishes with a perfectly flat, perfectly empty
checkpoint series — indistinguishable from "checked and found nothing." A
clean table and a broken instrument look identical unless something forces
the difference to be loud. Concretely, this run is invalid — not "flat",
**invalid** — unless all of the following hold, checked in code and printed,
not eyeballed off a chart:

1. **Sampler errors are counted and must be zero.** Replace the bare `pass`
   with an incrementing counter; abort/flag the run if `sampler_errors > 0`
   instead of silently returning a shorter list.
2. **At least 4 checkpoints must be observed** (the cumulative checkpoint
   counter's delta over the run). Fewer means the run was too short relative
   to the real interval — refuse to conclude "flat" the same way
   `ticket_probe.py` already refuses to run below 2x oversubscription.
3. **The checkpoint-running flag must actually toggle** (not stuck at 0 the
   whole run — nothing ever checkpointed within the window despite #2's
   counter moving — and not stuck at 1 — the sampler is reading a wedged or
   misparsed field).
4. **Checkpoint I/O must reach the throttled device**, the same trap that
   burned this exact harness twice already for the *read* path (page-cache
   absorption, and a working set that fit the cache). Confirm a WiredTiger
   bytes-written-from-cache or block-manager bytes-written counter
   (name TBD at Step 0) increases during checkpoint-active seconds. If it
   doesn't, the checkpoint completed almost for free because there was
   nothing dirty to flush (see §7) — a "no spike" result under that condition
   confirms nothing about checkpoint cost, only that this workload never
   dirtied the cache, and must be reported as such rather than folded into a
   general "flat" conclusion.
5. **Aggregate cross-check against 003's own published c=8 row.** This run's
   overall mean latency and ops/s, averaged across the whole several-minute
   window, should land near 003's c=8 figures (70.1ms mean latency,
   114.0 ops/s) — same throttle, same cache, same concurrency, same
   workload, just longer and finer-grained. If the aggregate disagrees
   sharply, something about the new setup differs from the old one and the
   per-second detail cannot be trusted until that's resolved.
6. **Attribution by number, not by eye.** Report p99 conditioned on
   checkpoint-active seconds vs. checkpoint-inactive seconds as two numbers
   and a ratio. "The graph looks bumpy" is exactly the unfalsifiable check
   this repo's guard culture (issue #8) exists to rule out.

## 5. What lands in the corpus

New parameter, `data/parameters.yaml`:
- `db.checkpoint_interval_seconds` — unit seconds, dimension seconds. The
  measured (not assumed) gap between checkpoints on this WiredTiger version.

Either, depending on outcome:

**If confirmed (spike found):**
- New coefficient `mongodb.checkpoint-tail-latency-multiplier` —
  `confidence: measured`, `applies_to:` scoped narrowly and honestly —
  something like "MongoDB 7.0.39, standalone, default WiredTiger checkpoint
  config (~60s), read-only workload, this host/throttle" — **not** "MongoDB
  7.0" unqualified. This is one machine, one workload shape; the `applies_to`
  field is free text specifically so this kind of narrow, honest scope
  doesn't have to be faked into a version range it hasn't earned.
- A new `role: headroom` term on `mongodb.ticket-throughput-ceiling` in
  `data/models/mongodb-concurrency.yaml`. Headroom is explicitly defined in
  `src/xycalc/schema.sql`'s own header comment as "what the tail costs...
  concurrency spikes, **checkpoint bursts**" — this is that term, currently
  absent from the model.
- `docs/investigations/003-storage-stall-query-collapse/FINDINGS.md` gets a
  qualification, not a rewrite: the flat-throughput headline stands for
  ops/s-over-25s, with a named exception for p99-within-the-second.

**If falsified (no spike):**
- Observation rows (`data/observations/`) recording p99-checkpoint-active vs.
  p99-checkpoint-inactive and their ratio (≈1), `confidence: measured`,
  same narrow `applies_to`. Confirmatory evidence, still worth citing —
  the README's own words: "a confirmation with better evidence... is worth
  having."
- A short addition to 003's FINDINGS.md "What this does not say" section:
  checked at 1s resolution at c=8 for N minutes, no periodic tail found —
  with the read-only caveat from §7 stated plainly, not buried.

Either way: new source entry in `data/sources.yaml` /
`data/sources/<slug>.yaml` for the run, following the existing
`obs-mongodb-ticket-probe-swamplink-2026-08-01` pattern — harness path,
exact container settings, guard values, machine class.

## 6. Effort and dependencies

- Harness changes (checkpoint_state, per-second bucketing, fixing the silent
  sampler-exception bug, the new guards): ~1.5–2 hours.
- Step 0 calibration (confirm field names and real interval on a live
  container): ~15 minutes, likely 1–2 short container runs.
- Main run: 8–10 minutes of container time; budget for 1–2 retries if a
  guard trips (the original ticket_probe needed two smoke-run iterations
  before its guards were right) — call it 30–45 minutes wall time.
- Analysis and FINDINGS.md update: ~1 hour.
- **Total: half a day**, the cheap end of the roadmap — reuses nearly all of
  an existing, working harness, needs no new infrastructure, and the host
  requirement (Docker + a throttleable block device) is already satisfied on
  swamplink from the 003 runs.
- Depends on nothing else being done first. Blocks nothing per GitHub
  (`blocked-by`/`blocking` both empty). Soft dependency: if T3 (#issue for
  the write-rate/eviction-dirty-trigger test) lands first, its harness will
  already produce a workload that dirties the cache under load, which is
  exactly the missing ingredient for the optional write-mix extension in §7
  — worth checking before building that extension from scratch.

## 7. What could make this not worth doing

**The honest risk with the literal, minimum-scope version of this experiment
(§3 Step 2, as specified): the workload it re-runs is 100% reads.**
`ticket_probe.py`'s `worker()` only calls `db.docs.find_one(...)` — no writes
happen during any measured window, only during the initial bulk load before
the timed levels start. A WiredTiger checkpoint's cost is dominated by how
much *dirty* data it has to write back. A checkpoint of an all-clean cache is
close to a no-op: walk the tree, confirm nothing needs flushing, update
checkpoint metadata. So the literal re-run this issue specifies is likely to
find little or nothing — not necessarily because 003's flat-throughput claim
generalizes, but because this specific workload never gives a checkpoint
anything expensive to do. Investigation 001 already established that the
dangerous WiredTiger interaction (eviction conscripting application threads)
is a *dirty-page* mechanism.

This is not a reason to skip §3 Step 2 — it is cheap, it directly answers the
issue exactly as worded ("is 003's flat throughput actually flat" — 003's
workload was read-only, so testing it read-only is the correct, direct
falsification of that specific published claim), and a clean negative result
at 1s resolution is real, citable evidence even if unsurprising. But it means
a negative result from Step 2 alone should **not** be written up as "WiredTiger
checkpoints don't matter here" — only as "003's specific read-only claim holds
at finer resolution too." Overclaiming that scope is the failure mode to avoid
in the write-up.

**The version worth its own follow-up, not required to close this issue:** a
second run of the same harness with a light update mix (5–10% of ops touch
the document instead of just reading it) so checkpoints have real dirty
content to flush — that is the run with a real chance of finding something
that would change a conclusion, and it is where §6's soft T3 dependency
matters. Flagging it here rather than silently expanding this issue's scope.

**If the true result is "a real but small effect"** (say p99 rises 30-50%
during checkpoint-active seconds, at absolute latencies in the tens of
milliseconds) — that's worth recording as an observation, but by itself it
would not require qualifying 003's headline the way the issue's framing
implies ("this corpus would have made... the same error"). That framing is
earned only by an effect on the order of the ticket-ceiling collapse itself
(orders of magnitude), not a double-digit-percent wobble. Say which one was
found; don't let a modest, real number get written up with the rhetorical
weight of a correction it doesn't support.
