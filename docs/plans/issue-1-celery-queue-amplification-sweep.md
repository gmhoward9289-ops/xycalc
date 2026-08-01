# Plan — issue #1: run the celery_probe sweep, land investigation 004

**Issue:** [#1](https://github.com/gmhoward9289-ops/xycalc/issues/1) — "Investigation 004: run
the Celery sweep and land the coefficients." Harness built and smoke-tested at
`tools/bench/celery_probe/`; full sweep not run, no coefficients exist.

---

## 1. The question, as a person would ask it

**When MongoDB is stalled behind a throttled disk, does putting Celery in front of it turn a
failure that holds steady (investigation 003, raw threads) into one that gets worse the longer
it runs — because the backlog keeps growing, draining it takes far longer than the stall did,
and the broker starts redelivering slow tasks as duplicate load on the thing that's already
struggling?**

Three separable sub-claims, all from the issue and `BRIEF.md`:

1. **Backlog** grows without bound once arrival exceeds the completion ceiling.
2. **Drain time** is disproportionate — small stall, long recovery.
3. **Redelivery** adds duplicate load specifically *during* the stall (positive feedback: the
   thing that's overloaded generates more load on itself).

---

## 2. What would falsify it

- **Backlog:** falsified if, at arrival rates sustained above the measured completion ceiling,
  `queueDepthMax` stabilizes rather than climbing across the 30-second window, or if it climbs
  but proportionally to time elapsed rather than accelerating — i.e. it's just an M/M/1 queue
  doing exactly what queueing theory predicts, no surprise, no "unbounded" framing earned.
- **Drain time:** falsified if `drainSeconds` scales roughly linearly with backlog size at
  arrival stop (`drainSeconds ≈ queueDepthAtEnd / completion_ceiling`) rather than blowing up
  disproportionately. That would mean "drain time outlives the stall" is true only in the
  trivial sense that any backlog takes time to clear, not in the sense BRIEF.md claims — that a
  short stall becomes a long outage.
- **Redelivery:** falsified if `duplicateRatePct` stays at or near zero even with
  `PROBE_ACKS_LATE=1` and `PROBE_VISIBILITY_TIMEOUT` pushed low enough that task latency
  (already known from investigation 003's ladder to reach several hundred ms at high
  concurrency) should cross the threshold. See the premise note below — this sub-claim needs a
  config change from the issue's own default to be tested at all.

An issue whose premise can't be wrong isn't an experiment: (1) is close to definitionally true
given this architecture (nothing bounds a Redis list but memory), so the interesting output is
never "does it grow" but **how fast, and what that costs** — the numbers, not the yes/no.

---

## Premise notes — two things in the issue/BRIEF that don't match the harness as built

**A. Redelivery cannot happen under the issue's own default config.** `tasks.py` sets
`task_acks_late=os.environ.get("PROBE_ACKS_LATE", "0") == "1"` — default `acks_late=False`,
Celery's documented default, which acks a message when the worker *receives* it, before
execution starts. Redis's `visibility_timeout` redelivers only what's unacked; with early ack,
the message is off the unacked set before the slow MongoDB call even begins, so no amount of
storage-induced slowness can make it cross the timeout. The issue's "What to run" section says
`./run.sh` with no override, and explains the smoke run's zero duplicates as "tasks still
completed inside the window" — but with `acks_late=0`, zero duplicates is what you get
**regardless of how long tasks take or how short the timeout is.** Provoking redelivery needs
`PROBE_ACKS_LATE=1` as a precondition, not "higher arrival rates or a shorter timeout" as the
issue states. This is factored into Method and the Guard below.

**B. The harness measures drain under *sustained* throttle, not drain after the stall *ends*.**
`compose.yml`'s `blkio_config` is a static limit for the container's whole lifetime; nothing in
`run.sh` or `drive.py` ever lifts it mid-run. BRIEF.md's language — "the storage blip ends; the
backlog does not" — describes a transient stall that resolves while the backlog it created
persists. What the harness actually measures is: arrivals stop while the device *stays*
throttled, then how long the backlog takes to clear under continued degraded I/O. That's still a
real and useful number, but it is not "drain time after the stall recovers" — it's "drain time
while the stall never ends." FINDINGS.md needs to name this precisely rather than inherit
BRIEF's framing unchanged. Testing genuine post-recovery drain would need `run.sh` or
`compose.yml` extended to lift the blkio limit mid-run (e.g. `docker update` on the mongo
container), which is out of scope for this sweep — flag it as a follow-up, don't build it now.

---

## 3. Method

Extend `tools/bench/celery_probe/` — no new harness needed, and BRIEF.md's own "Do NOT do"
already forbids inventing a new one. `PROBE_DOCS` stays at the default `1500000` for every run
(≈4–5x the 0.25 GB WiredTiger cache configured in `compose.yml`, matching the ≥2.0x guard with
room to spare) — the harness's own README says explicitly not to lower it to save time, and this
plan doesn't either.

Run on **swamplink** (the only host with `/dev/sda` as the throttleable block device `run.sh`
checks for), **sequentially, one `run.sh` invocation at a time** — see Guard below for why
concurrent invocations are unsafe. Do not run this alongside `tools/bench/ticket_probe.sh` on the
same host (BRIEF.md's own rule — they'd contend for the one throttled device).

### Runs

All runs add `PROBE_ACKS_LATE=1` except the explicit control. All use `PROBE_CONCURRENCY=8`,
`PROBE_PREFETCH=4` unless swept.

| # | Purpose | Env overrides | Rates | Seconds |
|---|---|---|---|---|
| 1 | **Control** — confirms Premise Note A | `PROBE_ACKS_LATE=0` (default) | `200` | 30 |
| 2 | **Baseline ladder** — backlog + drain curve | `PROBE_ACKS_LATE=1` | `25,50,100,200,400` (default) | 30 |
| 3 | Visibility-timeout sweep | `PROBE_ACKS_LATE=1 PROBE_VISIBILITY_TIMEOUT=10` | `200` | 60 |
| 4 | Visibility-timeout sweep | `PROBE_ACKS_LATE=1 PROBE_VISIBILITY_TIMEOUT=5` | `200` | 60 |
| 5 | Visibility-timeout sweep | `PROBE_ACKS_LATE=1 PROBE_VISIBILITY_TIMEOUT=2` | `200` | 60 |
| 6 | Prefetch sweep | `PROBE_ACKS_LATE=1 PROBE_PREFETCH=1` | `200` | 30 |
| 7 | Prefetch sweep | `PROBE_ACKS_LATE=1 PROBE_PREFETCH=16` | `200` | 30 |
| 8 | Concurrency sweep | `PROBE_ACKS_LATE=1 PROBE_CONCURRENCY=4` | `200` | 30 |
| 9 | Concurrency sweep | `PROBE_ACKS_LATE=1 PROBE_CONCURRENCY=16` | `200` | 30 |

Run 2's `rate=200` row at `visibility_timeout=30` (harness default) is the fourth point on the
timeout sweep — runs 3–5 don't need to repeat it.

Rate `200` is used as the fixed point for runs 3–9 because the issue's own smoke data puts it
comfortably above the fleet's completion ceiling (~158/s cited in the issue) without being so far
above it that the backlog swamps the 30–60s window before the knob's effect is visible.

Example invocation for run 3:

```bash
cd tools/bench/celery_probe
PROBE_ACKS_LATE=1 PROBE_VISIBILITY_TIMEOUT=10 PROBE_RATES=200 PROBE_SECONDS=60 \
  ./run.sh > /tmp/celery-sweep/run3.log 2>&1
```

Capture every run's full stderr (has the per-rate table) and the `===JSON===` block to a file;
distill into corpus YAML afterward (§5). Run 1 first and time the `load()` phase from its
stderr — it calibrates how long the remaining 8 invocations will take, since every invocation
pays the full 1.5M-document load again (`run.sh`'s cleanup trap does `docker compose down -v`,
which drops the data).

### What Method deliberately does not do

- Does not attempt a full factorial over all four knobs — combinatorially that's
  5 rates × several timeouts × several prefetch × several concurrency values, which is not
  "executable tomorrow." One-factor-at-a-time from the baseline, at a fixed above-ceiling rate,
  is enough to answer "does this knob move the number and by roughly how much" — which is what
  the issue asks for.
- Does not sweep prefetch/concurrency across multiple rates. That's T6's job
  ([#14](https://github.com/gmhoward9289-ops/xycalc/issues/14)), which asks specifically how
  prefetch changes the relationship between queue depth and true outstanding work — a sharper,
  dedicated question this plan shouldn't half-answer in passing.
- Does not modify `drive.py`/`tasks.py`. Two small additions would make interpretation safer
  (see Guard) but are optional, not required to produce a valid result, and touching code is out
  of scope for a plan.

---

## 4. The guard

Two guards already exist in this harness and must be checked, not assumed, on every run:
`oversubscription >= 2.0` in the JSON `meta`, and `pagesReadIntoCache` summed across all rates
`> 0` (the driver already prints a loud `WARNING` if not — treat that warning as a hard stop, not
a caveat to mention in passing).

Two more are specific to this sweep and neither is checked by the harness today:

**Guard 1 — a zero duplicate rate is only evidence when `acksLate: true`.** Per Premise Note A,
`duplicateRatePct: 0.00` with `acksLate: false` in the same JSON is not a finding about
redelivery; it's the ack-before-execution default making redelivery structurally unreachable.
Before citing any duplicate-rate number, check the top-level `acksLate` field in the same JSON
blob it came from. Run 1 in the table above exists specifically to print this in black and white:
expect `duplicateRatePct: 0.00` there **regardless of backlog depth**, and don't let that get
filed as "redelivery didn't happen under load" — it's "redelivery couldn't have happened."

**Guard 2 — the producer must actually reach the target rate.** `drive.py`'s `run_rate()` is a
single Python thread doing blocking `app.send_task()` calls in a tight loop to hit
`targetRatePerSecond`. At 400 tasks/s that's a 2.5ms budget per call including the Redis round
trip. If the producer can't keep up, the fleet never sees the offered load, and a "flat" or
"stable" result would just describe the producer's own ceiling — a plausible table measuring the
wrong thing, same shape as the two failures in #8. Check `enqueued / seconds` against
`targetRatePerSecond` for every result row; if achieved rate is more than ~10-15% short of
target at the higher rates (200, 400), that row is invalid and needs a faster producer (e.g.
multiple driver processes) before it's trusted, not just noted as a caveat.

**Guard 3 — never overlap `run.sh` invocations.** `cleanup()` runs `docker compose down -v
--remove-orphans` both on exit *and* at the top of every invocation. A second `run.sh` starting
while a first is still going will tear down the first's containers mid-run — this is the exact
bug that killed a run and was briefly misdiagnosed in investigation 003 (see its FINDINGS.md,
"a defect in the harness itself"). Run the 9 invocations from one sequential shell loop; confirm
each one's exit code and `===JSON===` block before starting the next. Do not background them.

**What each guard would print if the thing being measured never happened, stated plainly:** a
clean 9-row set of tables, every `duplicateRatePct` at 0.00, every `drainSeconds` small and every
`queueDepthMax` modest — indistinguishable, on casual reading, from "queues don't actually make
this worse." The four checks above (2 inherited + 2 new) are what turns that into a loud, specific
diagnosis (wrong ack mode / producer capped / guard tripped / run corrupted) instead of a silent
wrong conclusion.

---

## 5. What lands in the corpus

**Land now, independent of the sweep** (pure documentation lookups, `documented` grade, zero
risk):

- `celery.prefetch-reserved-count` — parameter `queue.prefetch_reserved_tasks`, the documented
  formula `worker_prefetch_multiplier × concurrency` for how many tasks a prefork worker holds
  off-queue-but-not-running. Source: Celery's optimizing-guide docs, version-pinned to whatever
  `celery[redis]>=5.3` resolves to in `Dockerfile` at run time (`pip freeze` it, don't assume).
  `applies_to` must name that resolved version, not "Celery."
- `redis.broker-visibility-timeout-default-seconds` — Redis transport's documented default
  `visibility_timeout`. Commonly cited as 3600s (1 hour) but **do not carry that number into the
  corpus without pulling the actual sentence from `docs.celeryq.dev` for the resolved version** —
  this plan doesn't have the citation, so treat the number as unconfirmed until Method's research
  step gets it.

**Land from the sweep, `benchmark` grade, `applies_to` naming the full configuration** (per
BRIEF.md's own DoD item — not "Celery," the exact worker/broker/device config):

- `queue.completion_rate_ceiling_ops_s` — the fleet-level throughput ceiling from the sub-ceiling
  rows of run 2 (25, 50, 100/s). Worth landing even though investigation 003 already has a
  device-side number, because this one includes Celery's own overhead (serialization, broker
  round trip, prefork dispatch) on top of it — if it's materially below 003's raw-thread ceiling,
  that's Celery's own tax on the number, which is new information.
- `queue.backlog_depth_max_by_rate` and `queue.drain_seconds_by_rate` — from run 2's full ladder.
  This is the headroom term BRIEF.md calls the interesting quantity.
- `queue.duplicate_rate_pct_by_visibility_timeout` — from runs 3–5 (and run 2's `rate=200`,
  `timeout=30` row as the fourth point), **only** if Guard 1 passes for each row.
- Whatever runs 6–9 show for `queueDepthMax`/`drainSeconds` at the prefetch/concurrency extremes
  — likely as `notes` on the above rather than new named parameters, since this plan deliberately
  doesn't run enough of a matrix to claim a coefficient for "prefetch's effect," only "prefetch
  moved the number by about this much at one rate" (T6 owns the real version of that claim).

**Model:** `data/models/celery.yaml`, `celery.queue-amplification` — same shape as
`mongodb.ticket-throughput-ceiling` (floor + headroom, `n=0`/`n=1` honestly stated, not a
generalized formula users plug arbitrary numbers into). Floor = completion ceiling; headroom =
drain time as a function of backlog at arrival-stop; constraint (non-computing) = redelivery
requires `acks_late=1`, named explicitly so nobody reads the duplicate-rate figures as describing
Celery's default behavior.

**File-level dependency, not optional:** `tests/test_corpus.py::TestStubs` currently parametrizes
`celery` as a system required to have **zero** coefficients (`test_deferred_systems_exist_and_are_honestly_empty`).
Landing any coefficient above means deleting `"celery"` from that parametrize list, or the build's
own test suite fails on the first coefficient this issue adds. This is a one-line test edit,
called out here so it isn't a surprise mid-PR.

`data/systems.yaml`'s celery stub notes ("Stub. Has a head start...") should be rewritten once
real coefficients land — not this plan's job to write, but worth flagging so the stub language
doesn't linger next to real data.

---

## 6. Effort and dependencies

- **Research** (2 documented figures — prefetch formula, visibility_timeout default): 30–45 min.
- **Sweep execution**: 9 sequential `run.sh` invocations, each paying a full 1.5M-doc load. Run 1
  calibrates load time; budget 1.5–3 hours unattended wall-clock for the rest, mostly waiting on
  `SECONDS` + `DRAIN_TIMEOUT` (up to 120s per rate if a backlog doesn't clear). This is the one
  number in this plan that's a real unknown until run 1 finishes — if load time turns out to
  dominate, cut runs 6–9 to a single knob value each rather than two.
- **Landing corpus files** (2 new YAML files, `tests/test_corpus.py` edit, `xycalc build && xycalc
  audit && pytest -q`): 1–1.5 hours.
- **FINDINGS.md**, matching investigation 003's rigor including the two premise notes above: 1–2
  hours.

Total: roughly a working day, most of the middle unattended. Not blocked by any other open issue
— the harness is built and this plan requires no code changes to run. Softly informs, in
sequence order:

- **[#14](https://github.com/gmhoward9289-ops/xycalc/issues/14) (T6, prefetch hides backlog)** —
  wants this issue's baseline backlog/drain numbers as the thing prefetch is measured against,
  and reuses this same compose stack.
- **[#15](https://github.com/gmhoward9289-ops/xycalc/issues/15) (T7, Redis eviction policy)** —
  reuses the broker; benefits from this issue having already exercised `visibility_timeout` and
  `acks_late` semantics.
- **[#16](https://github.com/gmhoward9289-ops/xycalc/issues/16) (T8, retry storms)** — explicitly
  named in ROADMAP.md as the third instance of "a system that responds to overload by generating
  more load," after investigation 001's eviction loop and this issue's redelivery loop. Wants
  004's redelivery finding as the established baseline before layering retries on top of it.

None of these are hard blocks — each is independently runnable — but running this one first means
T6/T7/T8 inherit the acks_late precondition (Premise Note A) instead of rediscovering it.

---

## 7. What could make this not worth doing

**The flashiest claim in BRIEF.md is conditional on a non-default setting, and the write-up has
to say so or it overclaims.** Premise Note A means redelivery-driven duplicate load — "a system
that responds to overload by generating more load," the line BRIEF.md calls the most important
possible result here — only happens when a Celery deployment has explicitly opted into
`task_acks_late=True`. Early ack (the default, and Celery's own recommendation unless a task is
written to be safely re-run) makes this structurally unreachable. If the sweep confirms that
(Guard 1's whole purpose), the honest finding is narrower than the issue implies: not "queues
cause duplicate load under a stall," but "queues configured for at-least-once delivery do." That
narrower claim is still worth having — plenty of real deployments do run late-ack for exactly the
durability reasons that make this failure mode relevant to them — but it's a materially smaller
result than "Celery," unqualified, and FINDINGS.md needs to lead with the qualification rather
than bury it.

**Backlog growth itself isn't news.** An unbounded queue backing up when arrivals exceed service
rate is M/M/1 queueing theory, not a finding — the value here is entirely in the *numbers*
(how much backlog, how long to drain, at what duplicate rate, for this specific configuration),
which is exactly why BRIEF.md caps every one of them at `benchmark` grade and forbids presenting
them as general. If the resulting FINDINGS.md can't say more than "yes, a queue backs up, here
are some numbers tied to one Docker Compose file on one host," that's a thin corpus contribution
relative to the multi-hour cost above. What would make it worth doing regardless: landing the two
`documented` Celery/Redis facts (real value, cheap, true independent of any run) and a clean
write-up of the acks_late precondition (corrects a premise in an issue and a roadmap doc other
open issues build on). What would make it *not* worth doing on schedule: if swamplink is needed
for something else this week, this can slip a few days without cost — nothing else in the roadmap
is currently blocked on it, only informed by it.
