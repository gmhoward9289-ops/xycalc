# Issue #14 (T6) — How much does Celery prefetch hide the backlog?

## 1. The question, as a person would ask it

"My alert fires on Redis queue depth. If depth says 20 but the fleet actually
has 200 tasks it hasn't finished, how late does that alert go off — and does
it get worse the higher I set prefetch?"

## 2. What would falsify it

The premise is: **`LLEN` on the broker key understates outstanding work by
roughly `prefetch_multiplier × concurrency`, and the understatement grows with
prefetch.**

That is falsified if either holds:

- Broker depth tracks true outstanding (`enqueued − completed`) to within
  noise **at every prefetch setting tested**, including 16. Then the concern
  in the issue is unfounded, and the plan should say so — the issue itself
  says this is worth saying plainly, since "lower your prefetch" is common
  advice.
- The gap exists but does **not** move with prefetch (e.g., it's dominated by
  in-flight tasks currently executing, which is independent of prefetch and
  present even at `prefetch=1` with `concurrency=8`). That would mean the
  issue's mechanism is real but mis-attributed — the culprit is concurrency,
  not prefetch, and lowering prefetch alone would not close the gap.

Both are genuine possible outcomes, not just the null. Redis's Celery
transport does not pull tasks off the queue purely via prefetch in a vacuum —
`concurrency` in-flight tasks are *also* off the queue and not "outstanding
but visible." A plan that only tests prefetch and doesn't separate its effect
from concurrency's baseline effect risks confusing the two. Method (§3)
controls for this by holding concurrency fixed and by having a `prefetch=1`
data point as the baseline where reservation is minimal.

## 3. Method

Reuse `tools/bench/celery_probe/` almost entirely — do not build a new
harness. Three changes are needed to the existing code, all small.

### 3a. What already exists

- `PROBE_PREFETCH` is already wired end to end: `run.sh` → `compose.yml` →
  `tasks.py`'s `worker_prefetch_multiplier=int(os.environ.get("PROBE_PREFETCH", "4"))`.
  Nothing to add here.
- `drive.py::run_rate()` already samples every 0.5s: `r.llen(QUEUE)` (broker
  depth) and `counters()` (`completed`, `executions`, `duplicates`) — see
  `tools/bench/celery_probe/drive.py:117-179`.
- `drainSeconds` (time from "arrivals stop" to "queue empty and completed
  catches up to enqueued") is already computed per rate — exactly the
  time-to-quiet metric the issue asks for.
- The MIN_OVERSUB guard (dataset ≥ 2× wiredTiger cache) and the
  `pagesReadIntoCache == 0` warning are already in place and reused as-is.

### 3b. What's missing — and it's the one thing this experiment actually needs

`run_rate()` builds a `samples` list with exactly the two numbers this
question needs (`queue` depth and `completed` count) at 0.5s resolution, but
**the list is discarded** — only `"samples": len(samples)` survives into the
returned dict (`drive.py:178`). The per-sample dict also never records
enqueued-so-far, only `completed`, so even keeping the list wouldn't yet let
you compute `outstanding = enqueued − completed` per sample.

Two edits to `drive.py`, both inside `run_rate()`:

1. Track a running `sent_so_far` alongside the existing `enqueued` counter
   (they're the same value; just capture it at sample time) and add it to
   the per-sample dict: `samples.append({"queue": r.llen(QUEUE), "enqueuedSoFar": enqueued, **tickets(), **counters()})`.
2. Return the actual `samples` list in the result dict (rename the current
   scalar `"samples": len(samples)` to `"sampleCount"` if the count is still
   wanted), and add a derived per-sample `outstanding = enqueuedSoFar - completed`
   and `understatement = outstanding - queue` to each sample before
   returning, plus summary scalars: `understatementMax`, `understatementMean`
   (over samples taken after the queue has visibly backed up, i.e.
   `queue > 0`, to exclude the startup transient).

This is the entire code change. No new harness, no new container, no new
metrics collection — the numbers were already being computed and thrown away.

### 3c. Sweeping prefetch requires restarting the worker, not just the driver

`worker_prefetch_multiplier` is read from `PROBE_PREFETCH` at Celery app
construction (`tasks.py:37`, module-import time), so one running worker
process cannot change prefetch mid-run — unlike `PROBE_RATES`, which the
existing driver already sweeps *within* a single `docker compose up`. Sweeping
prefetch means recreating the `worker` service between values.

Concretely: a new wrapper script, `tools/bench/celery_probe/sweep_prefetch.sh`,
that for each value in `1 2 4 8 16`:

```bash
PROBE_PREFETCH=$p docker compose up -d --build --force-recreate worker
docker compose run --rm --no-deps -T driver python drive.py
```

reusing the already-running `redis` and `mongo` containers (started once,
outside the loop, by the existing `run.sh` logic) and appending each run's
JSON to a combined output file tagged with its prefetch value. `reset()` in
`drive.py` already clears all `probe:*` keys and the queue key at the top of
each `run_rate()` call, so state doesn't leak between prefetch values sharing
the same Redis.

### 3d. Fixed rate, not the existing rate sweep

Set `PROBE_RATES` to a single value comfortably above the completion ceiling,
not the default list. The celery_probe README's own smoke evidence puts the
ceiling near 150–160 tasks/s at the default `PROBE_CONCURRENCY=8` behind this
harness's throttled Mongo (8 MiB/s, 150 IOPS, 0.25 GB cache) — consistent with
investigation 003's device-bound ceiling, which is set by the device, not by
Celery, so it should not move materially with prefetch. Use `PROBE_RATES=400`
(top of the existing default sweep) for headroom, and verify per §3e that 400
actually cleared the ceiling at every prefetch value tested rather than
assuming it from one smoke run under different settings.

`PROBE_SECONDS` needs to be long enough for backlog to visibly build and for
the understatement to reach something like steady state — the existing
default of 30s produced a queue depth of 478 at a *lower* rate (200/s) in
twelve seconds, so 30s at 400/s should be plenty; confirm rather than assume.

### 3e. Avoiding a 5x reload cost

`drive.py::load()` drops and reloads 1,500,000 documents at the start of
*every* driver invocation. The sweep in §3c calls the driver 5 times against
the same Mongo container. Add an idempotency check to `load()`: if
`db.docs.estimated_document_count() == DOCS`, skip the drop/reinsert and go
straight to the oversubscription check. This is a small, low-risk change to
existing code, not a new mechanism — do not skip it, since re-loading 1.5M
documents five times turns a runnable experiment into an hours-long one for
no reason.

### 3f. Version capture

`tools/bench/celery_probe/Dockerfile` installs `celery[redis]>=5.3`
unpinned, and `compose.yml` uses floating tags `mongo:7` / `redis:7-alpine`.
`drive.py`'s JSON output currently records `mongoVersion` (from
`buildInfo`) but nothing for Celery, kombu, or the Redis server. Add
`celery.__version__`, `redis.Redis.from_url(...).info()["redis_version"]`,
and `pymongo.version` to the output JSON before this lands in the corpus —
`applies_to` cannot honestly name a version nobody recorded. Pinning the
Dockerfile is optional; recording what actually ran is not.

### Commands, concretely

```bash
cd tools/bench/celery_probe
# start redis+mongo once, build images
docker compose up -d --build redis mongo
PROBE_RATES=400 PROBE_SECONDS=30 ./sweep_prefetch.sh   # new script, §3c
```

## 4. The guard

**What would this print if prefetch had no real effect and nothing was
actually being measured?**

Two boring, specific ways this experiment produces a clean table that means
nothing, and the check that makes each loud:

1. **The load generator never actually clears the ceiling.** `drive.py`
   issues `app.send_task()` synchronously inside the same loop that samples
   Redis (`drive.py:127-138`). If that call's latency rises (e.g. under
   Redis contention from 5 back-to-back sweep runs, or a slow container), the
   *achieved* enqueue rate falls below the 400/s target silently — nothing
   currently checks `enqueued / elapsed` against `rate`. If arrivals never
   exceed completions, the queue never backs up at *any* prefetch value, and
   `outstanding ≈ queue` trivially everywhere — a flat "prefetch doesn't
   matter" result that is actually "the experiment never loaded the system."
   **Guard:** compute `achievedRate = enqueued / elapsed` per run and assert
   it's within, say, 90% of the target `rate`; report and flag (not silently
   drop) any run that fails this, because a low-prefetch run failing it
   differently from a high-prefetch run would itself bias the comparison.

2. **The fleet isn't actually running the tasks it's said to be running.**
   If the worker container fails to come up cleanly after
   `--force-recreate` (§3c), or the queue name is wrong, or Mongo isn't
   actually throttled this run, `completed` stays near zero while `enqueued`
   climbs — depth and true outstanding then both grow together, dramatically
   and "for real," but for a reason unrelated to prefetch. **Guard:** reuse
   the existing `pagesReadIntoCache == 0` check (already in the harness) as a
   sanity that Mongo is genuinely being hit, and add a floor check that
   `completedDuringLoad > 0` and `throughputPerSecond` sits in the
   previously-observed ~100-160/s band (not near-zero, not near the arrival
   rate) at every prefetch value — a throughput far outside that band on any
   run means something broke in the restart, not that prefetch changed the
   database's ceiling.

A third, more specific check worth having because it turns the result from
"a gap existed" into "the gap is the mechanism the issue describes": the
*expected* steady-state understatement at overload is approximately
`prefetch_multiplier × concurrency` (the tasks each worker process is
holding reserved) plus a roughly constant, prefetch-independent term for
tasks currently executing (bounded by `concurrency`). With `concurrency`
fixed at the default 8, that predicts understatement climbing roughly
linearly with prefetch: something like 8, 16, 32, 64, 128 tasks at prefetch
1, 2, 4, 8, 16. If the measured `understatementMax`/`understatementMean`
per prefetch value doesn't track that shape at all, that's not just a null
result — it means the mechanism as described (reservation, not something
else like network buffering or sampling lag) isn't what's producing the
gap, and the write-up needs to say so rather than report a correction factor
that happens to be numerically true but attributed to the wrong cause.

## 5. What lands in the corpus

This is `celery`'s **first** entries — it is currently a stub in
`data/systems.yaml` with no coefficients or model file. Land only what the
evidence supports; do not manufacture a full model from one sweep.

- **New parameters** in `data/parameters.yaml`, a new "task queues" section:
  `queue.broker_depth` (bytes: count), `queue.true_outstanding` (count),
  `queue.understatement_ratio` (dimensionless, `outstanding / max(depth, 1)`),
  `queue.prefetch_multiplier` (dimensionless, an input not a coefficient),
  `queue.time_to_quiet_seconds` (seconds).
- **Observations** (not `documented` coefficients — this is a measurement of
  a running configuration): `data/observations/<host>-celery-prefetch-sweep-<date>.yaml`,
  one row per prefetch value, each with `system: celery`,
  `system_version:` set from the captured Celery/Redis versions (§3f — do
  not write this field from memory or from the Dockerfile's floating
  constraint), `machine_class`, `workload` describing the fixed rate and
  concurrency, and `notes` naming `tools/bench/celery_probe/` per the
  `source_type: benchmark` convention already used in
  `data/sources/swamplink-ticket-probe-2026-08-01.yaml`.
- **Confidence: `benchmark`**, not `measured`. Per investigation 004's own
  BRIEF: "the interesting quantities... are properties of a configuration,
  not of Celery, so they will never be better than `benchmark` and must not
  be presented as general." `applies_to` must name the full configuration —
  Celery version, Redis version, `concurrency=8`, the specific throttled-Mongo
  setup — not just "Celery," per that same BRIEF's explicit instruction.
- **Do not create `data/models/celery.yaml` from this alone.** A model needs
  a floor (completion ceiling) and the other terms investigation 004's BRIEF
  lists (redelivery amplification, drain time) — this issue supplies one
  constraint term among several. Land the correction factor as an observation
  now; a `queue_depth_understated_by` constraint term can be added to a
  celery model once issue #1's broader sweep exists to hold the rest of it.
  Landing a single-term "model" here would be the kind of premature model
  investigation 004's own brief warns against generalizing from one
  configuration.

## 6. Effort and dependencies

- Code changes (§3b idempotent-load, sample capture, sweep script, version
  capture): under an hour, all edits to files that already exist.
- Execution: 5 prefetch values × (~2 min load, skipped after the first run
  once idempotency lands, + 30s load window + drain wait, drain timeout
  currently 120s) — call it 15-20 minutes of wall clock if drains stay well
  under timeout, more if `drainSeconds` runs long at high prefetch (plausible,
  since that's exactly the effect being measured). Budget 45 minutes.
- Write-up: an hour, following the `003-storage-stall-query-collapse/FINDINGS.md`
  structure — short answer with band, weakest inference named, what would
  validate it further.
- **Depends on:** nothing blocking — the harness and its guards already
  exist and are smoke-tested (per `docs/investigations/004-celery-queue-amplification/BRIEF.md`).
- **Relationship to #1:** #1 is "run the full investigation-004 sweep across
  `PROBE_VISIBILITY_TIMEOUT`, `PROBE_ACKS_LATE`, `PROBE_PREFETCH`,
  `PROBE_CONCURRENCY`" — broader and unscoped on which knob matters most.
  This issue is a tighter, pre-specified slice of that same sweep (prefetch
  only, one fixed rate, with the specific depth-vs-outstanding comparison
  named). Running this first and landing its result under `celery` gives #1
  a real example to follow for the rest of the knobs, rather than #1
  reinventing the same harness changes independently. Does not block #1;
  should probably land before #1's broader sweep so the sample-capture and
  idempotent-load changes aren't duplicated.
- Not blocked by, and does not block, T1–T5, T9, T10 (different systems).
  Loosely related to T7 and T8, which reuse this harness's Redis-broker setup
  under different failure modes (`maxmemory` eviction, retry storms) — no
  code dependency, just shared infrastructure worth being aware other agents
  are also touching.

## 7. What could make this not worth doing

If §2's first falsification holds — depth tracks outstanding within noise at
every prefetch value — the honest write-up is short: say the concern is
unfounded, cite the numbers, and that's a real, useful result (the issue
itself asks for exactly this candor). It would not mean the exercise was
wasted; "the widespread advice to lower prefetch has no basis at these
scales" is a corpus-worthy finding on its own.

The case that *would* make this not worth doing: if the achieved-rate guard
(§4.1) fails and can't be fixed cheaply — e.g. if `send_task()` latency is
itself the bottleneck regardless of tuning, making it impossible to sustain
400/s against this harness's containers on the available host. That would
mean the experiment can't actually get above the completion ceiling to create
the backlog the whole question depends on, and would need a lighter-weight
load generator (not this driver's synchronous per-task Redis round trip)
before the question is answerable at all. Worth checking with a short manual
smoke run (a minute at the target rate, checking `achievedRate`) before
investing in the full 5-point sweep.
