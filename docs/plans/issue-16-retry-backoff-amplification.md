# Plan — issue #16 (roadmap T8): does backoff actually help a stalled dependency?

## 1. The question, as a person would ask it

"When Mongo goes slow enough that Celery tasks start timing out, does turning
on exponential backoff — with or without jitter — actually take load off the
database while it's struggling, and does it get the fleet back to normal
faster once Mongo recovers, compared to just retrying immediately with no
backoff at all?"

## 2. What would falsify it

The premise is "backoff (and jitter on top of it) measurably reduces the extra
load a stalled dependency sees, and/or measurably shortens recovery time,
relative to retrying with no delay." Falsified if, across the three policies
(none / exponential / exponential+jitter), both of these come back
statistically indistinguishable at the scales one box can produce:

- **amplification** — attempts reaching Mongo per task originally enqueued,
  during the stall window
- **recovery time** — wall-clock from "throttle lifted" to "throughput and
  queue depth both back to baseline"

If both are flat across policies, the issue's own words apply: "the standard
advice is cargo cult here, even if it is sound at larger fan-out." That is a
real, publishable result, not a failed experiment — see §7.

There's a second, narrower way to falsify a piece of it even if the headline
holds: if jitter specifically adds nothing over plain exponential backoff
(same amplification, same recovery), that's worth reporting on its own —
jitter's textbook justification is avoiding *synchronized* retry waves across
many independent clients, and a single-fleet, single-box experiment may be
too small to ever show that effect. Say so if that's what happens, rather than
folding it into "backoff didn't help."

## 3. Where the issue's premise doesn't match the code

Two things worth flagging before anyone starts building.

**Celery isn't retrying anything today.** `tools/bench/celery_probe/tasks.py`'s
`probe.lookup` task does a blocking `find_one` with no timeout and no
`self.retry()` anywhere. Under the existing harness, a task that hits a
stalled Mongo just sits — queued behind the ticket pool, exactly as
investigation 003 describes — until it eventually completes. The only
mechanism that currently manufactures *extra* load is the Redis broker's
`visibility_timeout` redelivery, which is investigation 004's mechanism, not
retries. T8 needs real retry logic added to the task; it doesn't exist yet.
This isn't a blocker, just a correction to "Celery retries" as the issue
states it — nothing retries out of the box here.

**"No backoff" is not Celery's default.** `self.retry()` called with no
arguments uses `default_retry_delay`, which defaults to **180 seconds** — that
is itself a fixed delay, i.e. a (non-exponential) form of backoff. Treating
"leave retry_backoff off" as the no-backoff arm would actually compare
"180s fixed delay" against "exponential from ~1s" — the wrong two policies,
in the same shape as the `at_term` trap `xy-observe` warns about. The
no-backoff arm has to set `countdown=0` (or some deliberately tiny fixed
value) explicitly.

**The stall-tightening mechanism named in the issue needs a decision, not an
assumption.** "The block-IO throttle can be tightened live" is true, but not
via `docker compose` — `compose.yml`'s `blkio_config` is fixed at container
creation. Live tightening means rewriting the container's cgroup directly:
cgroup v2, `io.max` for the device (`rbps=`/`riops=`), resolved from the
container's PID via `/proc/<pid>/cgroup`; cgroup v1 would be
`blkio.throttle.read_bps_device` / `read_iops_device` instead. This needs
root and needs to be validated against whichever cgroup version swamplink
actually runs — first step of Method, below, not an assumption baked into the
sweep. `docker pause`/`unpause` is a usable fallback for "total outage" (not
"slow" — the whole process freezes) if the cgroup rewrite doesn't pan out, and
should be named as a deliberate substitution if used, not a silent one.

## 4. Method

Extend `tools/bench/celery_probe/` — same task shape (random point lookup by
`_id`), same guarded loader, same throttled-Mongo container, so anything this
run shows beyond investigation 004 is attributable to retries specifically.

**4.1 — add real retry logic to the task.** In `tasks.py`, give `probe.lookup`
a `max_time_ms` on the `find_one` (server-enforced — this matters, see §4
below) and catch the timeout to call `self.retry()`. Read the policy from an
env var, `PROBE_RETRY_POLICY` ∈ `{none, backoff, backoff_jitter}`:

```python
@app.task(bind=True, name="probe.lookup", max_retries=int(os.environ.get("PROBE_MAX_RETRIES", "8")))
def lookup(self):
    mongo, r = _clients()
    r.incr("probe:executions")
    if r.incr(f"probe:exec:{self.request.id}") > 1:
        r.incr("probe:duplicates")     # broker redelivery — track separately, see guard
    try:
        mongo.ticketprobe.docs.find_one({"_id": random.randrange(DOCS)}, max_time_ms=MAX_TIME_MS)
    except PyMongoError as exc:
        r.incr("probe:retries")
        r.incr(f"probe:retry_reason:{type(exc).__name__}")
        raise self.retry(exc=exc, **RETRY_KWARGS)   # RETRY_KWARGS set from PROBE_RETRY_POLICY
    r.incr("probe:completed")
```

`RETRY_KWARGS` per policy:

- `none`: `countdown=0` (explicit — see §3)
- `backoff`: `retry_backoff=True, retry_backoff_max=<calibrated, see below>, retry_jitter=False`
- `backoff_jitter`: same as `backoff` with `retry_jitter=True`

Prefer `max_time_ms` (server cancels the op, frees its ticket) over a client
`socketTimeoutMS`. A client-side timeout gives up on the *response* without
cancelling the *operation*, so the ticket stays held server-side — that would
make "no backoff, retry immediately" look catastrophically worse for a reason
that has nothing to do with retry policy. Confirm which one is actually firing
via `probe:retry_reason:*` (see guard).

**4.2 — isolate retries from broker redelivery.** Set
`PROBE_VISIBILITY_TIMEOUT` comfortably longer than `max_retries × (worst-case
backoff delay)` for this run, so the existing redelivery mechanism
(investigation 004) can't also fire during the experiment. `probe:duplicates`
(existing counter) should stay at/near zero throughout; if it doesn't, the two
feedback loops are mixed together and the amplification number is not
attributable to retry policy. Report both counters regardless — don't just
suppress one.

**4.3 — calibrate `MAX_TIME_MS` in a short smoke run before the sweep.**
Investigation 003's numbers (70ms mean latency at concurrency 8, 535ms at 64)
came from a different workload shape (raw threads, no Celery, different
worker/prefetch concurrency) and shouldn't be copied in blind. Run a 30–60s
smoke at the target arrival rate with a generous `max_time_ms` (e.g. 2000ms),
read back p50/p95 latency and mean queue-hold time from
`wiredTiger.concurrentTransactions`, and pick a deadline that sits clearly
above healthy latency and clearly below stalled latency. **This number is an
unknown the smoke run must produce, not a figure to import from 003.** Same
smoke run picks `retry_backoff_max`: long enough that exponential backoff gets
several doublings in before capping, short enough that a retry can still
complete inside the stall window.

**4.4 — the stall itself.** One arrival rate, well above the known ceiling
(003's fault-injection measured 108.8–118.4 ops/s flat against this exact
8 MiB/s / 150 IOPS throttle) — 300 tasks/s is a reasonable starting point,
close to the smoke run in the existing README (200 tasks/s against a fleet
managing 158/s). Three phases, run back to back, once per policy:

1. **baseline** (~60s) — throttle at a level that does not bind (or container
   freshly started, unthrottled) — confirms healthy behavior before the stall
2. **stall** (~90–120s) — tighten the cgroup throttle live to the 8 MiB/s /
   150 IOPS values `ticket_probe.sh` already uses, so this result is
   comparable to investigation 003's
3. **recovery** — loosen the throttle back, then poll until throughput and
   queue depth both return to baseline (see §5 for the precise stop condition)
     — cap with a timeout and report `recoveryTimedOut` rather than blocking
       forever, same pattern `drive.py` already uses for `drainSeconds`

Load the 1.5M-doc dataset **once** (reuses the existing `MIN_OVERSUB` guard,
unchanged) and run all three policies against it back to back — no need to
reload data between policies, only reset Redis counters and recreate the
`worker` container with the new `PROBE_RETRY_POLICY`. Do at least **two**
independent runs of the full three-policy sweep — investigation 003's
"reproduced" section is the bar; a single run of a system this noisy is an
anecdote.

Sample every 0.5s (matching `drive.py`'s existing cadence):
`probe:executions` delta, `probe:retries` delta, `probe:duplicates` delta,
`probe:retry_reason:*`, queue depth, `wiredTiger.concurrentTransactions`
tickets, `queuedMicrosDelta`, and completions/sec.

**4.5 — driver.** Extend `drive.py` rather than write a new file — it already
has `load()`, `tickets()`, and the sampling loop; add a phase-aware runner
(baseline/stall/recover) and thread `PROBE_RETRY_POLICY` through to the
worker's environment in `compose.yml`.

## 5. The guard

**What would this print if the thing being measured never happened?** Several
distinct ways this experiment produces a clean, wrong table:

- **`max_time_ms` never actually fires.** If the deadline was calibrated too
  loose, or the stall isn't tight enough, `probe:retries` stays at zero (or
  near it) across all three policies — and the three tables look identical
  for a completely different reason than "backoff doesn't matter." **Refuse
  to report an amplification coefficient if total retries during the stall
  window falls below some floor (e.g. 50) — print the warning loudly, the
  same way `drive.py` already does for `pagesReadIntoCache == 0`.**

- **Retries are conflated with broker redelivery.** If `probe:duplicates`
  is non-negligible relative to `probe:retries` during the stall, the
  "additional load" number is a mix of investigation 004's mechanism and this
  one, and cannot be attributed to retry policy. Report both counters
  unconditionally; treat a non-trivial `probe:duplicates` count as
  disqualifying for that run, not as noise to average away.

- **Timeouts come from the wrong place.** `probe:retry_reason:*` must show
  the server-side timeout (`ExecutionTimeout` or equivalent) as the dominant
  cause. If it's dominated by a connection-pool wait or a network timeout
  instead, the "retries" are really measuring `maxPoolSize=8` contention or
  driver-level flakiness, not the database stall — a plausible-looking table
  that's actually a benchmark-harness bug.

- **The "no backoff" arm DoSes the worker, not the database.** Immediate,
  unlimited-rate retries can pin worker CPU or exhaust the local connection
  pool before they ever add meaningful load to Mongo — the failure would look
  like "no backoff causes way more load" but the bottleneck would be the
  worker process, not the target of the experiment. Track executions/sec
  alongside queue depth: if executions/sec flattens while retries pile up
  locally, that's a worker-bound ceiling, and it must be named as such, not
  folded into "amplification against the stalled dependency."

- **Recovery inherits investigation 003's exact trap.** Do not call it
  "recovered" the moment queue depth hits zero — the ticket pool in 003 was
  still climbing when the measurement window ended, and a queue can drain
  before the underlying pool/latency has actually settled. Recovery must
  require **both** queue depth at baseline **and** throughput within some
  band of pre-stall baseline for several consecutive samples, not either
  alone — the same "compare like with like" discipline `xy-observe` names for
  validation cases, applied to a stop condition instead.

- **The reused guards still apply unchanged**: oversubscription ≥ 2.0×
  (`MIN_OVERSUB`), and `pagesReadIntoCache` must be non-zero across the run —
  both already implemented in `drive.py`/`load()`; don't bypass them for this
  experiment.

If none of the above trip and retries fire cleanly, isolated from redelivery,
attributable to the server-side stall specifically — then "no measurable
difference between policies" is the real finding, not an artifact. That's
exactly the shape §2 already commits to accepting.

## 6. What lands in the corpus

**New parameters** (`data/parameters.yaml`):
- `queue.retry_amplification_factor` — dimension: ratio — attempts reaching
  the dependency per task originally enqueued, during a stall
- `queue.stall_recovery_seconds` — dimension: time — wall-clock from stall
  lifted to throughput + queue depth both back to baseline

**New coefficients** (`data/coefficients/celery.yaml` — does not exist yet;
`celery` is currently a documented-empty stub in `data/systems.yaml`), one
pair per policy, e.g. `celery.retry-amplification-none`,
`celery.retry-amplification-backoff`, `celery.retry-amplification-backoff-jitter`
and the matching `celery.retry-recovery-*` trio. Grade **`benchmark`** —
these are properties of a configuration, not of Celery in general, so
`applies_to` must spell out the whole configuration the same way
investigation 004's BRIEF already commits to: Celery version, exact
`retry_backoff`/`retry_backoff_max`/`retry_jitter` values, `max_retries`,
MongoDB 7.0.39, the exact throttle (8 MiB/s / 150 IOPS), worker
concurrency/prefetch, and the arrival rate used. **Only promote to
coefficients if the guard holds clean and results reproduce across the two
runs required in §4** — mirror the ticket-throughput-ceiling precedent
exactly: if reproduction is shaky, land observations only and say in
FINDINGS.md why no coefficient shipped yet, the same way
`docs/investigations/003.../FINDINGS.md` did for `predictedCeiling`.

**Observations + source**: `data/observations/<host>-retry-probe-<date>.yaml`
and `data/sources/<host>-retry-probe-<date>.yaml` (`source_type: benchmark`,
`notes:` naming the exact harness commands — required by
`test_benchmarks_say_how_to_reproduce_themselves`), mirroring
`swamplink-ticket-probe-2026-08-01.yaml`'s format.

**Docs**: a new `docs/investigations/00N-celery-retry-storms/BRIEF.md` +
`FINDINGS.md` (numbering — see §7 coordination note), and an addition to
`docs/telemetry/celery.md` (doesn't exist yet) naming
`wiredTiger.concurrentTransactions`, `probe:retries`,
`probe:retry_reason:*` and `probe:duplicates` as the series worth capturing
from a real incident.

## 7. Effort and dependencies

**Effort**: roughly a day, split — 2–4 hours to extend `tasks.py`/`drive.py`/
`compose.yml` and validate the live cgroup-tightening mechanism actually
works on the target host (this is the one piece with real technical risk;
budget time for the `docker pause`/`unpause` fallback if it doesn't), ~1 hour
for the calibration smoke run, ~1 hour of wall-clock for two full three-policy
sweeps (3 policies × 3 phases × ~2–3 min each × 2 runs ≈ 30–40 min actual
load, plus container/dataset setup), remainder for FINDINGS and landing the
corpus entries.

**Depends on / blocked by**: nothing hard. Helps to have investigation 004
(#1) run first, since its redelivery numbers are exactly what §4.2/§5 need to
rule out as a confound — not required, but running T8 before 004 means
building that isolation blind rather than against a known baseline.

**Collision risk worth flagging**: T6 (#14, prefetch/backlog) and T7 (#15,
Redis maxmemory policy) both plan to extend this same
`tools/bench/celery_probe/` harness — `tasks.py`, `drive.py`, `compose.yml`
are the shared surface all three touch. Three independent implementations
landing in the same files will conflict. Whoever executes first should check
whether a `00N-celery-*` investigation directory already exists before
creating a new number, and the three probably belong in adjacent or even
shared investigation numbering rather than each claiming their own — worth a
human decision before execution starts, not three agents guessing
independently.

## 8. What could make this not worth doing

The issue's own falsification already covers the main case: if backoff and
jitter turn out to make no measurable difference at one-box scale, that's
still worth publishing (it's a real, useful correction to reflexive advice),
so the experiment is worth running even if the null result is likely.

Where it *would* stop being worth doing: if the calibration step (§4.3) can't
find a `max_time_ms` where retries fire cleanly without either (a) also
firing during "healthy" baseline, or (b) requiring a stall so extreme it
falls outside what `ticket_probe.sh`'s throttle values represent — i.e., if
there's no clean window between "never times out" and "times out even when
healthy." If that happens, the honest output is "this task's latency
distribution doesn't have a workable timeout threshold at this scale," which
is itself worth one paragraph in FINDINGS, not a reason to force a number.

Also worth naming: `max_retries` bounds how much amplification any policy can
produce inside a single task's lifetime. At small `max_retries` (single
digits, needed to keep the recovery-phase wall-clock reasonable), the
*ceiling* on amplification may be too low for backoff-vs-no-backoff to show a
difference regardless of mechanism — a scale limitation, not a finding about
backoff, and worth saying explicitly rather than let a small-`max_retries`
artifact read as "backoff doesn't matter."
