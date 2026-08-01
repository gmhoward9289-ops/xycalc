# Plan: issue #2 — `mongodb.ticket-throughput-ceiling` assumes a pinned pool

Issue: https://github.com/gmhoward9289-ops/xycalc/issues/2 · labels `corpus`,
`known-limitation` · extends investigation 003
(`docs/investigations/003-storage-stall-query-collapse/`), does not open a new
investigation number (004 is already claimed by #1).

## 1. The question

*"My throughput-ceiling formula divides tickets by hold time — but MongoDB 7.0
doesn't hold tickets at a fixed count, so once the system has been under load
for a while, is that formula still telling me anything, or do I need a
different model for 7.0?"*

## 2. The premise is narrower than the issue states — say so before planning around it

The issue asserts, as settled fact, that the model "does not describe
steady-state 7.0 under sustained load." Reading `FINDINGS.md` closely, that
is **not what the 2026-08-01 run showed**. It showed that `totalTickets` was
*still climbing* when each 25-second window closed — at c=64, 29 → 68, peak
74, "still rising when the window ended." The run never watched a level long
enough to see whether steady state exists, let alone what it looks like. The
issue's framing quietly promotes "we didn't observe convergence in 25
seconds" to "convergence doesn't happen" — those are different claims, and
the FINDINGS write-up itself is more careful than the issue text: it calls
the next step "not yet done," not concluded.

This matters for which option is right. The issue offers three options and
says (3) is attractive regardless — true, and cheap, and this plan does it
immediately. But the choice between (1) (scope the model away from 7.0
steady-state) and (2) (add a device-bound model) depends on an empirical
question nobody has actually asked yet: **does N ever settle down, and if it
does, does tickets ÷ hold-time predict throughput once you feed it the real
N?** The existing data hints at the answer — flat throughput across an 18.5×
ticket-count range strongly suggests the device, not N, is doing the binding
— but "strongly suggests" is not the same as "measured," and the one probe
run to date varied concurrency only, never the device's own cap. Without that
second axis, "throughput didn't move when N moved" and "throughput is capped
by the device" are not yet distinguishable from "throughput is capped by
something else that happens to be invariant to both."

So: do (3) now, unconditionally. Do (1) now too, but write the reframe to
scope by *mechanism* ("valid where the pool doesn't grow past what the device
can drive"), not by version number, because a pinned-pool instance on 7.0+
(via `storageEngineConcurrentReadTransactions`) still matches the model. Treat
(2) as contingent on the experiment below, not decided in advance.

## 3. What would falsify it

The issue's implicit claim: *the tickets ÷ hold-time formula is the wrong
shape for MongoDB 7.0 under sustained load behind a slow device — no value of
N rescues it, because the device, not the pool, sets the ceiling.*

**Falsified if:** levels held long enough for `totalTickets` to visibly
plateau (a printed, numeric plateau check — see Guard) show a stable N, and
`plateaued_N ÷ corrected_hold_time` tracks measured throughput within ~25%
across concurrency levels. That result means the formula is fine at steady
state; the only defect was feeding it an assumed N instead of a measured one,
and (3) alone — never (2) — is the whole fix.

**Confirmed (the issue is right, and more strongly than it currently argues)
if either:**
- N never plateaus within a generous duration budget (see §4, escalation
  cap) — the pool genuinely never pins under this workload, so "pinned pool"
  is not a state 7.0 reaches under sustained throttled load, full stop; or
- N does plateau, but `plateaued_N ÷ corrected_hold_time` badly mispredicts
  throughput (off by multiples, not ~25%) at every level — meaning no choice
  of N fixes the formula, because the mechanism it encodes (tickets are the
  scarce resource) isn't what's actually happening (the device is).

A third, distinguishable outcome the issue doesn't consider: N plateaus at a
value that *tracks the device's configured rate* rather than settling at a
workload-independent constant — i.e., `throughputProbing` is implicitly
tuning N to whatever the device will bear. If the IOPS-cap sweep in §4 Phase 2
confirms this (throughput moves with the configured cap, and the plateaued N
moves with it too), the right corpus artifact is a device-bound model with N
demoted to a `constraint`/note, which is (2), and the falsification/
confirmation split above collapses into: (2) is correct, (1)'s reframe should
say so explicitly instead of leaving it as an open question.

## 4. Method

Reuse `tools/bench/ticket_probe.py` / `.sh` — do not build a new harness. Two
gaps in the current harness are why the open question is still open, and both
are small, targeted extensions.

**Phase 0 — cheap, no benchmark, do regardless (~30 min).**
- `data/models/mongodb-concurrency.yaml`: `tickets` input — drop
  `default: 128`, set `required: true`. This is issue option (3), and the
  issue is right that it's worth doing independent of everything else below:
  the current default is wrong by up to 32× in the dangerous direction on
  7.0+, and the help text already tells the reader to look it up instead of
  trusting it, which is a contradiction between prose and defaulting
  behaviour.
- Rewrite the model's `reframe` to state scope by mechanism: valid when the
  pool is pinned — pre-7.0 static 128, an instance with
  `storageEngineConcurrentReadTransactions` set by hand, or the transient
  window on 7.0+ before `throughputProbing` has ramped — and *not established*
  for 7.0+ once the algorithm has been running under sustained load, pending
  the result of Phase 1–2. Cite the 2026-08-01 fault-injection numbers
  already in `FINDINGS.md`; no new source needed for this part.
- `xycalc build && xycalc audit && pytest -q` to confirm the schema change
  doesn't break anything (existing CLI invocations that relied on the
  default — including the worked examples in `mongodb-concurrency.yaml`'s own
  docs and any README examples — need `--tickets` added; check for these
  before landing).

**Phase 1 — plateau detection (extends `ticket_probe.py`, ~1–2 hrs dev).**
The current script tracks `samples` internally (0.5s-resolution ticket
readings across the level) but only emits `ticketsStart` / `ticketsEnd` /
`ticketsMax` to the JSON — exactly the three numbers that let the existing
run's non-convergence go unnoticed until someone read the write-up closely.
Add:
- Emit the raw `(elapsed_s, readTotal)` series per level, not just the
  summary.
- A trailing-window convergence check: coefficient of variation of
  `readTotal` over the last third of the window, printed as `converged: bool`
  and the CV value. This is the number that turns "we ran it longer" into a
  checked claim.
- A corrected hold-time: `L = mean_latency_ms - (queuedMicrosDelta / ops /
  1000)` — the subtraction FINDINGS.md names as the "concrete next step, not
  yet done." `queuedMicrosDelta` is already collected; this is arithmetic on
  data the harness already has, not a new measurement.

Run the concurrency levels that hadn't converged — 16, 32, 64 — **each in its
own container**, not as a shared ladder:

```bash
PROBE_LEVELS=16 PROBE_SECONDS=300 PROBE_DOCS=1500000 ./tools/bench/ticket_probe.sh
PROBE_LEVELS=32 PROBE_SECONDS=300 PROBE_DOCS=1500000 ./tools/bench/ticket_probe.sh
PROBE_LEVELS=64 PROBE_SECONDS=300 PROBE_DOCS=1500000 ./tools/bench/ticket_probe.sh
```

One concurrency value per `PROBE_LEVELS` gives a fresh `mongod` per level for
free — `ticket_probe.sh` already names containers uniquely per invocation.
This also removes the carryover confound FINDINGS.md flagged: in the original
ladder, "ticket state carries forward between levels," so a level's starting
N already reflects the levels before it, which contaminates any plateau read
taken from the ladder as run. Isolation is what makes a plateau check mean
what it claims to mean.

Escalation rule, stated up front rather than decided after seeing the data:
if `converged` is false at 300s, double the duration (600s, then 1200s), cap
at 1200s per level. If still unconverged at the cap, that is the answer to
§3's first falsification branch — publish it as such, do not extend further
chasing a number that may not exist.

**Phase 2 — device-causality sweep (no code change, ~30 min wall clock).**
The existing run only ever tested one device cap (150 IOPS, 8 MiB/s) and
inferred "device-bound" from concurrency-invariance alone. That inference has
a hole: throughput being flat across a concurrency sweep is also what you'd
see if some *other* fixed thing were the ceiling — driver-side thread
scheduling, cgroup enforcement granularity, a single `mongod` I/O submission
path — anything invariant to client concurrency, not only the configured
device limit. Confirm causality by moving the thing itself, at the
concurrency level that already saturates:

```bash
PROBE_LEVELS=64 PROBE_SECONDS=180 PROBE_READ_IOPS=75  ./tools/bench/ticket_probe.sh
PROBE_LEVELS=64 PROBE_SECONDS=180 PROBE_READ_IOPS=150 ./tools/bench/ticket_probe.sh
PROBE_LEVELS=64 PROBE_SECONDS=180 PROBE_READ_IOPS=300 ./tools/bench/ticket_probe.sh
```

If throughput scales with the configured cap (roughly proportionally, not
exactly — device accounting overhead is real), the device-bound story is
supported and not just consistent-by-elimination. If it doesn't move, the
2026-08-01 finding's mechanism is wrong even though its headline number
(flat throughput) was real, and that is a correction worth publishing on its
own.

**Phase 3 — land it**, branching on Phase 1–2 results (see §5).

## 5. The guard

**What would this print if the thing being measured never happened?**

Two distinct "never happened"s, both real failure modes already seen twice in
this repo (`#8`):

1. *The plateau check never actually detects non-convergence, and prints a
   clean "converged: true" on a pool that's still moving.* A CV-over-trailing-
   window check with a loose threshold and a short trailing window (e.g. "CV
   over the last 10 seconds") would pass on a slowly, monotonically climbing
   series just as easily as on a genuinely flat one — a slow ramp looks locally
   flat if you only look at a short recent slice. Guard: the trailing window
   must be a meaningful fraction of the total run (last third, not last 10s),
   and the plateau claim must be checked against the *whole-series* slope too
   (linear fit over the full window; reject convergence if the fitted slope
   over the last third is not near zero in absolute ticket-count-per-second
   terms, not just in CV-relative terms — CV is scale-blind and a large slow
   climb from 60→70 has a deceptively small CV around a rising mean). Print
   both numbers, not just the boolean.

2. *The IOPS sweep prints a clean "throughput scales with the cap" table
   without the cap actually being what's binding — same class of failure as
   the two documented in `#8`.* Concretely: if `PROBE_MEMORY` or the
   oversubscription ratio isn't re-verified at each new IOPS setting (they
   don't change, but a copy-paste of Phase 2's commands that also changes
   `PROBE_DOCS` or `PROBE_CACHE_GB` could reintroduce the exact host-page-cache
   bug `ticket_probe.py`'s existing guard was written for). Guard: **do not
   relax the existing guards to run Phase 2 faster.** Every invocation must
   still clear `cacheOversubscription >= 2.0` and `totalPagesReadIntoCache >
   0` — both already enforced by the harness and inherited automatically as
   long as `PROBE_DOCS`/`PROBE_MEMORY`/`PROBE_CACHE_GB` are left at Phase-1
   values across the sweep. If a run at any IOPS setting reports
   `pagesReadIntoCache: 0`, throw the whole sweep out — a table where all
   three points read the same throughput because none of them ever reached
   the device would look identical to genuine device-invariance, and be
   worthless in the opposite direction (fails to distinguish "flat because
   device-bound" from "flat because page-cache-bound").

Both guards produce a **printed number that must be checked**, not a
subjective read of a chart — that's the standard `#8` sets, and this plan's
whole reason to extend the harness rather than eyeball the existing JSON is
to meet it.

## 6. What lands in the corpus

- **Model edit**, `mongodb.ticket-throughput-ceiling` (`data/models/mongodb-concurrency.yaml`):
  `tickets` input made `required: true`, no default. `reframe` rewritten to
  scope by mechanism (pinned pool), with the steady-state-7.0 claim resolved
  one way or the other per Phase 1–3, not left open. This is an edit to an
  existing entry, not a new one — no new `applies_to`/grade needed for the
  schema change itself.

- **If Phase 1 shows convergence and the corrected formula predicts
  throughput within tolerance:**
  - New coefficient `mongodb.tickets-probing-steady-state` (parameter
    `db.concurrency_tickets`), value = the plateaued N (or one row per
    concurrency level if it varies), `applies_to: "MongoDB 7.0.39,
    throughputProbing default, sustained random point-read load behind a
    rate-limited device"` — deliberately narrow; this is not a general 7.0
    steady-state constant, it's what one workload converges to on one box.
    Grade `benchmark` (own fault-injection harness, not vendor documentation
    or a production observation).
  - Model's `reframe` updated to say the formula *does* apply at 7.0 steady
    state given a measured N, closing the open question rather than
    perpetuating it.

- **If Phase 2 confirms device-causality (the more likely outcome given the
  existing 2026-08-01 data):**
  - New model `mongodb.device-bound-ceiling`, `system: mongodb`, `output:
    db.ops_per_second`. Floor term reuses the existing `io.iops` parameter
    (already defined for the EBS models, `data/parameters.yaml`) as a
    required input — the device's configured/observed IOPS ceiling — rather
    than inventing a new parameter for the same quantity. If the op-to-IO
    ratio for this workload isn't ~1:1, a new coefficient (tentatively
    `mongodb.point-read-ops-per-io`, grade `benchmark`, `applies_to` scoped
    to this harness's workload shape — small documents, random `_id` point
    lookups) captures the gap; do not invent this coefficient before Phase 2
    shows whether it's needed.
  - `mongodb.ticket-throughput-ceiling`'s `reframe` cross-references the new
    model for the device-bound case instead of leaving readers to work out
    which model applies to their situation.
  - Observation rows for the plateaued N values and the IOPS-sweep throughput
    figures, in the same style as
    `data/observations/swamplink-ticket-probe-2026-08-01.yaml` — one slug per
    (parameter, concurrency-or-IOPS-setting) pair, `source_type: benchmark`.

- **If neither converges nor a clean cap-correlation appears** (the
  falsification branch where N never settles within the 1200s cap): a
  documented negative result in `FINDINGS.md` — "the pool does not reach a
  steady value under this workload within N minutes" is itself a landed
  finding, not a failed experiment, and should be recorded as a `notes:`
  addition to `mongodb.tickets-probing-floor` so the next reader doesn't
  re-run the same 25-second probe and reach the same premature conclusion the
  original run did.

- `docs/telemetry/mongodb.md`'s "measurement that would settle the open
  question" paragraph is now stale (it predates the 2026-08-01 run) and
  should be rewritten to point at whichever of the above actually landed,
  plus the CV/slope plateau check as a new recommended series if Phase 1's
  code addition proves useful beyond this one investigation.

## 7. Effort and dependencies

- Phase 0: ~30 min, no infrastructure. Do this regardless of everything else.
- Phase 1 dev (raw-series emission + CV/slope check + corrected-L arithmetic):
  ~1–2 hrs.
- Phase 1 runs: 3 levels × 300s baseline = 15 min wall clock; budget up to
  3 × 1200s = 1 hr if escalation triggers on all three.
- Phase 2 runs: 3 IOPS settings × 180s = ~10 min wall clock.
- Landing corpus entries + `FINDINGS.md` update + rewriting
  `docs/telemetry/mongodb.md`: ~1–2 hrs.
- **Total: about one working day**, dominated by analysis and corpus writing,
  not run time. Wall-clock benchmark time is under 90 minutes even at the
  escalation cap.

**Blocked by:** nothing. The harness exists and needs extension, not
replacement; no other issue needs to land first.

**Blocks:** nothing directly. Loosely related to `#12` (T4: is investigation
003's flat throughput actually flat within a window, i.e. checkpoint
sawtooth) — both want "hold one concurrency level much longer than 25s and
watch it over time," and if `#12` runs first its per-second latency capture
at a sustained high-concurrency level could be reused as one of this issue's
Phase 1 levels instead of running it twice. Not a hard dependency either
direction; flag it to whoever picks up either issue so the runs can be
combined if timing allows.

**Interface note, not a blocker:** making `tickets` required is a breaking
change to the CLI flag surface for `mongodb.ticket-throughput-ceiling`. Any
existing script or doc example that calls `xycalc sizing
mongodb.ticket-throughput-ceiling` without `--tickets` will start failing.
Grep `docs/` and `README.md` for uncovered examples before landing Phase 0.

## 8. What could make this not worth doing

The operational guidance is already mostly right without this work. The
model's `tickets` input help text already says "LOOK IT UP rather than taking
the default," the README already states the ticket model's `n=0` "undersells
it," and `FINDINGS.md`'s "What this does not say" section already warns
against raising N against a saturated device. A practitioner reading the
current corpus is not meaningfully misled *in practice* even before this plan
runs — they're told to measure their own tickets and watch the exceeded-check
alongside the queue counters either way.

What this plan actually buys is corpus **honesty and closure**: turning an
acknowledged-but-unresolved gap (`FINDINGS.md`'s own words: "not yet done")
into either a validated formula or a second model, instead of leaving a
`reframe` that gestures at the question without answering it. That's real,
and it's exactly the kind of gap this corpus's design (source-cited,
version-scoped, honestly-graded) exists to not leave sitting — but it's fair
to say the marginal operational value to someone sizing a system today is
smaller than the marginal value of, say, `#5`'s real compression samples
(the largest error term in the corpus's one validated model) or `#4`'s EBS
band (a 6.7× guess). If effort is scarce, Phase 0 alone (30 minutes, fixes
the actually dangerous default) captures most of the practical value; Phases
1–2 are worth doing for corpus rigor and because the harness extension (raw
series + plateau check) is reusable for `#12` and any future "does this
metric ever settle" question, not because today's guidance is currently
misleading anyone.
