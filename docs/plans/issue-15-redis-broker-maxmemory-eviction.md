# Plan — issue #15 (roadmap T7): Redis as a Celery broker at `maxmemory`

**Issue:** [#15](https://github.com/gmhoward9289-ops/xycalc/issues/15) — "Redis as a
broker: lose the tasks, or deadlock the workers?"
**Roadmap entry:** T7, `docs/investigations/ROADMAP.md`
**Systems touched:** `redis` (stub), `celery` (stub) — this is the first work
either one gets.

---

## 1. The question

*"If I use Redis as my Celery broker and it fills up, do I lose queued jobs, or
does the whole fleet seize up — and which of Celery's own two recommended
`maxmemory-policy` settings is actually the safe one?"*

## 2. What would falsify it

The issue frames this as "both documented options fail, report the conflict."
That is falsifiable in *either* direction, and the plan should not quietly
assume the premise before running anything:

- **`allkeys-lru` loses nothing** (queue drains to the same task-id set that
  was enqueued, `evicted_keys` stays at the queue's expense only within noise) →
  Celery's docs are vindicated and the practitioner claim is folklore.
- **`noeviction` never blocks consumption** — producers get clean, documented
  `OOM command not allowed` errors (expected backpressure) but workers keep
  consuming and the queue drains once producers back off → celery#5716 is
  stale. It was filed against **Celery 4.3.0 in 2019**, is still open with a
  `6.1` milestone that has not shipped, and current stable is 5.6.x. "Both
  documented options fail" may describe 2019, not now, and the honest thing to
  do is check on today's version before repeating the claim as current.

Either result is worth reporting. The one result that is *not* an answer is a
clean table with no counter proving eviction, OOM, or a stall actually
happened — see §4.

## 3. Method

### Reuse, don't rebuild

Reuse `tools/bench/celery_probe/` — it already has the Docker image, a Redis
service, a Celery worker service, and the driver-pattern (`drive.py`) this
needs. It does **not** need `celery_probe`'s MongoDB stall at all; T7 is about
the broker's own memory, not a slow downstream. Concretely:

- **Don't touch `mongo` service** in `compose.yml` — leave it out of this run
  entirely (T7 doesn't need it, and starting it just adds guard surface that
  is irrelevant here).
- **Add a second Redis service, `bookkeeping`**, generous memory, default
  `noeviction`, used for nothing except ground truth (§4 explains why this is
  load-bearing, not incidental).
- **Reuse the Dockerfile** (`celery[redis]`, `pymongo`, `redis` already
  installed; drop the `pymongo` dependency for this task or just leave it
  unused).
- **New task**, sibling to `probe.lookup` in `tasks.py`: `probe.noop`, which
  does nothing but write its ground-truth completion record to `bookkeeping`
  (see below). No MongoDB call.
- **New driver**, `tools/bench/celery_probe/evict_probe.py`, sibling to
  `drive.py`, implementing the two-phase run below.
- **`compose.yml` gets one new knob**: the `redis` service's `command:` grows
  `--maxmemory ${PROBE_MAXMEMORY:-16mb} --maxmemory-policy ${PROBE_MAXMEMORY_POLICY:-noeviction}`.

### The run, per policy

Two phases per policy, matching the issue's method exactly ("backlog driven
past it," then "count tasks enqueued versus tasks ever executed").

**Phase 1 — build backlog with no consumer running.** Don't start the
`worker` service yet. `evict_probe.py` calls `app.send_task("probe.noop",
kwargs={"pad": "x" * PROBE_PAYLOAD_BYTES})` in a tight loop, recording for
every call: success/fail, and on failure the exception class and message
verbatim (this is where `OperationalError: ... OOM command not allowed`
would show up, matching celery#5716's trace). Stop when either
`PROBE_ENQUEUE_ATTEMPTS` attempts have been made, or `used_memory` has sat
within 1% of `maxmemory` for 3 consecutive 0.5s samples (whichever comes
first) — the run should spend its time *at* the ceiling, not endlessly
retrying past it.

**Phase 2 — start the worker while Redis is still over the line.** This is
the detail celery#5716 turns on: the failure it reports is not (only) "new
tasks get rejected," it's that `queue_declare` — which runs at **worker
startup**, before any actual draining — pipelines a `LLEN` inside a Redis
transaction, and Redis's OOM check can reject the whole pipeline including the
read. So: start `worker` immediately, with the broker still at/over
`maxmemory`, and watch whether it starts consuming at all before doing
anything to relieve memory pressure. Poll every 0.5s for
`PROBE_DRAIN_TIMEOUT` seconds (default 120s, mirroring `celery_probe`'s
existing `drainSeconds`/`drainTimedOut` pattern): broker `LLEN <queue>`,
broker `INFO memory` (`used_memory`, `evicted_keys`), worker container state
(`docker compose ps`, restart count), and the bookkeeping store's distinct
executed-task-id count and per-id execution count (duplicates).

### Sweep

Three arms, one variable at a time — `PROBE_MAXMEMORY_POLICY` ∈
`{noeviction, allkeys-lru, volatile-lru}` — everything else fixed:

```bash
cd tools/bench/celery_probe
for policy in noeviction allkeys-lru volatile-lru; do
  PROBE_MAXMEMORY_POLICY=$policy \
  PROBE_MAXMEMORY=16mb \
  PROBE_PAYLOAD_BYTES=2048 \
  PROBE_ENQUEUE_ATTEMPTS=20000 \
  PROBE_DRAIN_TIMEOUT=120 \
  ./run_evict.sh   # new script, run.sh's cleanup/trap pattern, driver=evict_probe.py
done
```

`16mb` / `2048`-byte payload is a starting guess, not a claim — Celery's
message envelope (headers, task id, routing key) adds overhead on top of the
payload, so the actual bytes-per-message figure comes out of the run, not
before it. Tune `PROBE_MAXMEMORY` up or down in a throwaway smoke run
(`PROBE_ENQUEUE_ATTEMPTS=500`) until Phase 1 reaches the ceiling in a few
seconds — the number that matters is "did we actually get *to* `maxmemory`",
not any particular round figure.

**Run each arm twice**, back to back but not overlapping (investigation 003's
overlap caveat applies here too — two Redis containers contending for the
same host would confound `used_memory` timing). Two independent runs turned
"a finding" into something harder to argue with in 003; the same discipline
applies here for a result this consequential.

**One additional, smaller run**, `volatile-lru` with `task_ignore_result=True`
kept (the `celery_probe` default) vs. flipped to `False` with
`result_expires=60` — see §4, this decides whether the `volatile-lru` arm
tests anything at all.

**Secondary, time-permitting:** the same three-arm sweep with
`task_acks_late=1`. A comment on celery#5716 (claytondaley, 2020-08-16)
attributes a still-stuck deadlock to a Redis-backed **lock** (`SET NX PX`)
used somewhere in the redelivery/retry path — a write op, and a different
failure surface than the plain `RPUSH`/`LLEN` ops the primary sweep exercises.
Worth one run if the primary sweep leaves time; not worth blocking on.

### What versions this runs on, and why that's not decided yet

Pin nothing in advance beyond what's already in the Dockerfile
(`celery[redis]>=5.3`). Print `celery --version`, `python -c "import redis;
print(redis.__version__)"`, and `redis-server --version` into the run's JSON
output (same pattern as `ticket_probe.py` capturing `buildInfo.version`) and
use *that* for `applies_to` after the fact. Celery's current docs (fetched
2026-08-01, `https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html`,
"Caveats → Key eviction", Celery 5.6.3) still say the same "`noeviction` or
`allkeys-lru`" line the issue quotes — so the documented half of the
contradiction is current. Whether the *practitioner* half (celery#5716's
deadlock) still reproduces on 5.6.x is exactly the open question this
experiment answers; don't assume either way going in.

## 4. The guard

**What would this print if the thing being measured never happened?** A clean
table with `duplicates: 0`, `lost: 0`, `drainTimedOut: false` for every arm —
indistinguishable from "all three policies are fine." Four concrete ways that
could happen without anything real being measured, and the counter that makes
each one loud:

1. **Backlog never reaches `maxmemory`.** If Phase 1 stops on
   `PROBE_ENQUEUE_ATTEMPTS` before `used_memory` gets near the ceiling, none
   of the three policies ever activate and all three arms report identical,
   meaningless results. **Guard:** assert `used_memory / maxmemory >= 0.95` at
   the end of Phase 1 from Redis's own `INFO memory`, and refuse to report the
   arm if it isn't — same shape as `ticket_probe.py`'s oversubscription
   refusal.

2. **Eviction is asserted, not observed.** Redis exposes the ground truth
   directly: `INFO stats` → `evicted_keys`. **Guard:** for `allkeys-lru` and
   `volatile-lru`, `evicted_keys` (delta over the run) must be > 0 or the run
   proves nothing about eviction, whatever the loss count says. For
   `noeviction`, `evicted_keys` must stay at 0 — if it moves, `maxmemory-policy`
   didn't actually apply (a stale container, a typo'd flag), and the arm is
   not testing what its label says.

3. **The counter measuring "did a task run" is itself inside the blast
   radius.** This is the sharpest trap here and it's specific to this
   experiment: the natural thing to do is `INCR` a completion counter in the
   *same* Redis that's under eviction pressure. If that counter key gets
   evicted, "tasks ever executed" silently undercounts — a task ran, its
   record didn't survive, and the report says "lost" for a task that actually
   executed. That's not a measurement of task loss, it's a measurement of
   counter loss wearing task loss's clothes. **Guard:** ground truth
   (executed-task-id set, per-id execution count) lives in the separate
   `bookkeeping` Redis service, which is never subject to `maxmemory` in this
   experiment. The broker under test is used for transport only.

4. **`volatile-lru` is a silent stand-in for `noeviction`.** `volatile-lru`
   only evicts keys that carry a TTL. Celery's Redis transport does not set a
   TTL on its queue list keys. If nothing else in the broker DB has a TTL
   either, `volatile-lru` has zero eligible eviction candidates and behaves
   exactly like `noeviction` — not because it's protecting the queue, but
   because there's nothing else to sacrifice. A three-column table where the
   `volatile-lru` column is numerically identical to `noeviction`'s would look
   like a finding ("volatile-lru is as safe as noeviction!") and would
   actually be an artifact of an empty keyspace. **Guard:** report
   `evicted_keys` delta and `used_memory` at end-of-Phase-1 for the
   `volatile-lru` arm specifically, and run it once with `task_ignore_result`
   left `True` (no TTL keys exist — expect it to match `noeviction`, and say
   so, not present it as a result) and once with results enabled and
   `result_expires` set (TTL keys exist to be sacrificed — this is the
   configuration that actually tests the policy's documented behavior). The
   first run is the guard; the second is the measurement.

## 5. What lands in the corpus

Everything below is `benchmark` grade and scoped to the harness's exact
configuration — this is not a universal Celery constant any more than
investigation 004's drain time is. `applies_to` must name Celery/kombu/redis-py/
Redis versions (read from the run's own output, not assumed), plus payload
size and `maxmemory`, because the loss point is a function of message size
against a memory ceiling, not a fixed number.

**Sources** (`data/sources.yaml`):
- `celery-docs-redis-caveats-5.6` — `docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html`,
  "Caveats → Key eviction," Celery 5.6.3, retrieved 2026-08-01. `documented`.
  Quote: "configure the redis-server to not evict keys by setting in the redis
  configuration file: the `maxmemory` option [and] the `maxmemory-policy`
  option to `noeviction` or `allkeys-lru`."
- `celery-issue-5716` — `github.com/celery/celery/issues/5716`, filed against
  Celery 4.3.0, open, milestone `6.1` (unreleased as of this plan). This is a
  bug report, not vendor documentation — grade any figure sourced from it
  `practitioner`, never `documented`, and say explicitly in FINDINGS that the
  report predates the version this experiment runs against.

**Coefficients** (`data/coefficients/celery.yaml`, new file):
- `celery.redis-broker-noeviction-producer-error-rate` — fraction of Phase-1
  `send_task` calls that raise once `used_memory` reaches `maxmemory`.
  `applies_to`: exact versions + `maxmemory=16mb`, `payload=2048B` (or
  whatever the tuned run used). `benchmark`.
- `celery.redis-broker-noeviction-worker-starts` — whether the worker process
  begins consuming at all while the broker is still over `maxmemory` (yes/no,
  plus time-to-first-consumption if yes). This is the number that actually
  answers "deadlock or not," not the error rate above. `benchmark`.
- `celery.redis-broker-allkeys-lru-task-loss-rate` — (enqueued successfully −
  ever executed, per bookkeeping ground truth) / enqueued successfully.
  `benchmark`. This is the headline number the issue is chasing.
- `celery.redis-broker-volatile-lru-task-loss-rate` — same, **only** from the
  arm with TTL keys present (see §4.4); `applies_to` must say so explicitly,
  not just name the policy.
- `celery.redis-broker-duplicate-execution-rate` per policy, using the same
  per-task-id counting pattern `celery_probe/tasks.py` already has for
  redelivery — worth capturing even though it's not what the issue asked for,
  because a task redelivered *and* later evicted is a different failure than
  either alone.

**Model:** none proposed yet, deliberately. The issue's instruction is
"report the conflict, do not declare a winner" — forcing this into a
`sizing`/`headroom` formula before the numbers justify one would manufacture
false precision. If the `allkeys-lru` loss rate turns out to be a clean
function of backlog-bytes-over-`maxmemory`, a follow-up model
(`celery.broker-headroom` — "how much `maxmemory` headroom to survive a burst
of size X without loss") is a reasonable next step, but that's a second
investigation, not this one.

**FINDINGS.md** (new: `docs/investigations/005-redis-broker-eviction/FINDINGS.md`
or fold into the existing `004-celery-queue-amplification` directory if that
lands first — decide based on which merges first, not in advance) must carry,
per the SKILL.md discipline: the disagreement side by side with no winner
declared unless one arm genuinely loses nothing (§2); the weakest inference
named (almost certainly the `volatile-lru` arm's dependence on the
result-backend TTL configuration, §4.4); and whether celery#5716 reproduced on
the version actually tested.

## 6. Effort and dependencies

**Effort:** roughly a day, one Linux box with Docker (swamplink), no
production access, no AWS:
- ~2–3h: extend `celery_probe` (bookkeeping service, `probe.noop` task,
  `evict_probe.py`, `run_evict.sh`, `compose.yml` maxmemory knobs)
- ~1–2h: smoke-tune `PROBE_MAXMEMORY`/`PROBE_PAYLOAD_BYTES` until Phase 1
  reliably reaches the ceiling in a reasonable time, and confirm all four
  guards in §4 actually fire when they should (deliberately break one — e.g.
  set `maxmemory-policy` wrong — and confirm the guard catches it, the same
  discipline `ticket_probe.py`'s refusal path implies)
- ~1h container time: three arms × 2 runs + the `volatile-lru` TTL variant +
  optional `acks_late` arm
- ~1–2h: corpus entries + FINDINGS

**Dependencies:** none. `redis` and `celery` are both empty stubs in
`data/systems.yaml` — this is the first work on either, so nothing upstream
has to land first. Not blocked by, and does not block, investigation 004
(#1, "run the Celery sweep and land the coefficients") or T6/T8 — those ask
what a queue does to a *slow downstream* (MongoDB); T7 asks what happens when
the *broker itself* runs out of memory, and shares only the Docker image and
driver pattern, not the question. Building both in the same
`tools/bench/celery_probe/` directory (rather than a fourth new harness) is
the direct application of issue #8's lesson: reuse a guarded harness instead
of inventing a new way to fail quietly.

## 7. What could make this not worth doing

Two honest risks, not hedges:

- **The operational answer might just be "don't get near `maxmemory`."** If
  the real-world mitigation is alerting on `used_memory/maxmemory` well before
  either policy's failure mode engages, then the choice between `noeviction`
  and `allkeys-lru` only matters in the already-on-fire case, and the
  practical takeaway is a monitoring threshold, not a policy pick. That's
  still worth reporting — it's a legitimate, useful answer — but it means the
  corpus payoff is a documented industry disagreement plus a "here's the
  number to alert on," not a sizing model anyone will plug numbers into.
- **This has no live consumer right now.** Per the household infra notes,
  Celery+Redis was evaluated for this workload and rejected in the last
  review (2026-07-31) — nothing here currently runs a Celery/Redis broker in
  production that this result would change a decision for. That doesn't touch
  xycalc's value as a public, standalone corpus (the whole point of T7 is that
  the vendor-vs-practitioner conflict is real and undocumented anywhere else
  with numbers attached), but it does mean the honest urgency here is "this is
  a good corpus entry," not "this unblocks something."

Neither is a reason to skip it — the roadmap ranks T7 as one of the three
most likely to overturn something already published (a *vendor* claim, this
time, not one of xycalc's own) — but both belong in the record rather than
discovered later.
