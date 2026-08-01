# Issue #3 — Does `throughputProbing` walk the pool back down, and how fast?

Continuation of investigation 003, not a new roadmap slot. Extends
`tools/bench/ticket_probe.py` / `ticket_probe.sh`, the harness that already
produced the 4→74 climb this issue starts from.

## 1. The question

If MongoDB 7.0's ticket pool climbs under a sustained storage stall, does it
settle at a steady value (and is that value close to the documented max of
128), and once the load that drove it up goes away, does the pool come back
down toward the resting floor of 4 quickly, slowly, or not at all?

## 2. What would falsify it

Two sub-questions, each with its own falsifiable claim.

**Convergence.** The working hypothesis coming out of 003 is "still climbing,
would likely reach something near 128 given enough time and enough offered
demand." That is falsified if a long hold at high, unconstrained concurrency
plateaus *below* 128 with a visibly flat tail — that would mean the algorithm
has an equilibrium point set by something other than "how much concurrent
demand exists," which is a materially different and more interesting story
than "it just needed more time."

**Decay.** The issue's framing assumes decay is a background process — leave
the pool alone and it drifts back to 4 on some timescale. That is falsified in
either direction: fast return (pool back near 4 within roughly the time it
took to climb, e.g. a few minutes) means investigation 003's "resting value of
4" is a fair default between incidents, and the concern in the issue's "why it
matters" section is unfounded. No measurable movement over a long, truly idle
window falsifies the *premise* rather than just the number — see §7. Either
outcome is worth having; the second is worth more, because it would mean every
place in this corpus that treats 4 as "what you get after things calm down"
(including the `tickets` input note in `data/models/mongodb-concurrency.yaml`
and the framing of issue #2) is wrong about the calm-down part specifically.

## 3. Where the issue's premise is shaky

`throughputProbing` is a hill-climbing controller: it nudges concurrency and
watches whether throughput moved, on a timer, using recently observed
throughput as its signal. That mechanism has no input to react to if there is
no traffic. "Load drops away" as the issue phrases it most naturally means
*zero* concurrent client operations — and at zero operations there may be no
throughput sample for the algorithm to act on at all, in which case the pool
would not decay on a clock, it would simply freeze at whatever value the last
adjustment left it at, indefinitely, until an operation arrives to give the
controller something to measure again.

If that is what actually happens, "how fast does it come back down" isn't a
single number — decay speed (if any) is conditional on how much subsequent
traffic exists to drive the controller, not a property of elapsed idle time.
This plan treats that as an open branch rather than assuming either the
issue's framing (idle time decays it) or the opposite (idle time never decays
it): run both a true-zero-load cooldown and a low-trickle cooldown, and let
the comparison answer which framing is right. That comparison is itself the
finding if the two diverge.

## 4. Method

Reuse `tools/bench/ticket_probe.py` and `ticket_probe.sh` unchanged in their
throttling/loading logic (device cgroup limits, page-cache-bounding container
memory, the ≥2× oversubscription refusal, the zero-`pagesReadIntoCache`
warning — all of it is exactly the guard this experiment also needs). Extend
the Python harness in three ways; nothing about the shell wrapper's
container/network/cleanup logic needs to change.

**4a. Emit the raw sampled series, not just start/end/max.**
`run_level()` currently discards the `samples` list after computing
`ticketsMax`/`ticketsStart`/`ticketsEnd` and only reports the count. Change it
to include `[{"t": elapsed_seconds, "readTotal": ..., "readOut": ...,
"queueLength": ...}, ...]` in the level's result dict. Without the series, a
600-second hold can only be summarized by a human eyeballing "start" and
"end," which is exactly the kind of plausible-but-unchecked table this repo
has already been burned by twice.

**4b. An explicit, printed convergence verdict — not eyeballing.**
After a level's series is collected, compute the mean of `readTotal` over the
last 90 seconds of the window and the mean over the 90 seconds before that.
Print `CONVERGED` if they differ by less than 5%, else `STILL MOVING` with the
actual delta. This number goes in the JSON output next to the level's
results. Pick 90s/5% as a starting point, not a magic constant to trust
blindly — if the level is one that plausibly needs more than 10 minutes to
settle, the run should say `STILL MOVING` honestly rather than let a human
read a wide, slowly-rising 600-second table as "roughly flat at the end."

**4c. A cooldown phase after the load stops.**
New function, run once after the last configured level's `ThreadPoolExecutor`
has shut down and returned (not per-level — only the final, highest level's
descent matters for this question):

- `PROBE_COOLDOWN_SECONDS` (new env var, default 900 = 15 min): how long to
  keep sampling after load stops.
- `PROBE_COOLDOWN_HEARTBEAT_HZ` (new env var, default 0): if 0, issue no
  operations at all during cooldown beyond the sampler's own `serverStatus()`
  polling. If nonzero, one dedicated thread issues `find_one` at that rate
  throughout cooldown, on the same collection, so the controller has a
  throughput signal to react to.
- Sample every `SAMPLE_S` (existing 0.5s) throughout, same fields as the
  series in 4a, plus a `sinceLoadStoppedSeconds` field.
- Stop early if `readTotal` reaches the resting floor (4) and holds there for
  60 continuous seconds — no need to burn the full 15 minutes once it's
  clearly back down. Otherwise run the full window and report "did not reach
  floor within Ns" rather than silently truncating.
- Use a **separate, single-connection `MongoClient`** for the cooldown
  sampler, distinct from the load-generating client's pool (`maxPoolSize=
  max(LEVELS)+8`). Driver connection pools send periodic heartbeat pings per
  pooled socket; leaving a 70-plus-connection pool alive and idle during
  cooldown risks that traffic being mistaken for "zero load." Close or don't
  reuse the load client for cooldown sampling.

**Commands**, one session, one container spin-up (reload avoided across the
two cooldown variants by running them back to back against the same loaded
collection — do not re-run `load()` between them):

```bash
# Phase 1: climb, with more offered concurrency than the documented max so a
# plateau below 128 isn't just an artifact of demand never exceeding supply
# (see 4d below for why 64 alone is not enough for the convergence half).
PROBE_LEVELS=64,150 PROBE_SECONDS=600 \
PROBE_COOLDOWN_SECONDS=900 PROBE_COOLDOWN_HEARTBEAT_HZ=0 \
  ./tools/bench/ticket_probe.sh > /tmp/ticket-probe-convergence-decay-idle.json

# Phase 2: same climb, trickle cooldown instead of true idle, to separate
# "decay needs elapsed time" from "decay needs a throughput signal to react
# to." Requires a second harness invocation (own container), same PROBE_DOCS
# so the two runs are comparable.
PROBE_LEVELS=64,150 PROBE_SECONDS=600 \
PROBE_COOLDOWN_SECONDS=900 PROBE_COOLDOWN_HEARTBEAT_HZ=1 \
  ./tools/bench/ticket_probe.sh > /tmp/ticket-probe-convergence-decay-trickle.json
```

**4d. Why 150, not the issue's suggested 64, for the climb.** `totalTickets`
is a capacity the controller adjusts; `out` (active) can never exceed the
number of concurrently outstanding client requests. At 64 offered threads the
pool cannot be observed climbing past 64 for the simple reason that there is
never demand to fill a 65th ticket, regardless of what the controller would
otherwise do — a plateau at or below 64 would be indistinguishable between
"the algorithm's steady state is ≤64" and "demand was capped at 64." The
issue's own text speculates the pool "would likely" reach 128; testing that
claim requires offering more concurrent demand than 128 (150 gives headroom).
Run the literal `PROBE_LEVELS=64` case too — it's cheap and it's what the
issue asked for — but the number that actually answers "where does it
converge" is the 150 level, not the 64 one. `maxPoolSize` in the harness
already scales with `max(LEVELS)`, so this needs no other change.

## 5. The guard

**What would this print if the thing being measured never happened, at each
phase?**

- **Climb phase.** Already guarded by the existing harness: the ≥2×
  oversubscription refusal and the zero-`pagesReadIntoCache` warning catch
  "the working set fit in cache" and "the host page cache absorbed it," the
  two failure modes that already cost this repo two runs (issue #8). Keep
  both checks; they apply exactly as before.
- **"Converged" could mean "capped by offered concurrency," not "found the
  algorithm's ceiling."** Guarded by §4d — run a level above 128, and compare
  the plateau value against `outMax` versus `ticketsMax`. If `ticketsMax`
  plateaus at a value equal to (or within a few of) the level's thread count
  rather than below it and below 128, that is the demand-capped signature,
  not convergence, and the verdict should say so rather than report a clean
  "CONVERGED" number that is actually just "ran out of client threads."
- **"Decayed" could be a sampling-frequency illusion.** A pool that actually
  oscillates on a period shorter than `SAMPLE_S` (0.5s) would show a
  misleading smooth curve. Unlikely at these timescales (the controller's
  documented adjustment period is on the order of seconds, not milliseconds)
  but cheap to rule out — the cooldown series is retained in full (4a/4c), so
  a reviewer can check inter-sample deltas rather than trusting a summary
  statistic.
- **"Decayed" could be an artifact of the sampler's own traffic.** `admin.
  command("serverStatus")` runs every 0.5s throughout, including cooldown.
  Whether that command consumes a WiredTiger read ticket is not established
  anywhere in this corpus and should not be assumed either way — check it
  empirically before trusting a cooldown result: during a true-idle (HZ=0)
  cooldown, watch whether `readOut` is ever nonzero. If it is, the sampler
  itself is the load, the "zero load" condition was never met, and the
  cooldown measured something other than what it claims to.
- **"Didn't decay" could be a connection-pool artifact, not the controller's
  real behavior.** Guarded by the dedicated single-connection cooldown client
  in 4c — a 70-plus-socket idle pool sending periodic heartbeats is a
  plausible source of just enough background traffic to either keep the
  controller pinned or nudge it in a way that has nothing to do with the
  question. If this weren't guarded, a cooldown run would produce a clean,
  plausible-looking "held at 74 for 15 minutes" table whether or not the
  controller actually saw zero traffic.
- **The two cooldown runs (idle vs. trickle) not being comparable.** Both must
  load the same `PROBE_DOCS` count, hit the same device throttle settings, and
  ideally run on an unshared window of the host (see the reproduction caveat
  in `docs/investigations/003.../FINDINGS.md` about overlapping runs on a
  shared two-vCPU box depressing both results — confirm nothing else is
  running via `docker ps` before starting, note timestamps in the observation
  either way).

## 6. What lands in the corpus

- **Observations** (`data/observations/<host>-ticket-decay-<date>.yaml`,
  same shape as `swamplink-ticket-probe-2026-08-01.yaml`): the climb-phase
  series for the 150-thread level (peak, whether `CONVERGED` fired, and at
  what value), and the cooldown series for both the idle and trickle runs —
  at minimum peak-before-cooldown, value at fixed checkpoints (30s, 60s, 120s,
  300s, 600s, 900s post-stop), and either time-to-floor or "did not reach
  floor in 900s." Grade `measured`. `applies_to: MongoDB 7.0.39
  (throughputProbing defaults)` — pin the patch version the way the existing
  003 observations do; do not write `MongoDB >=7.0`, this is one instance's
  behavior, not a documented guarantee.
- **Source** (`data/sources/<host>-ticket-decay-<date>.yaml`): harness
  description, machine class, both env-var configurations, and the
  overlap/isolation caveat, matching the existing `obs-mongodb-ticket-probe-
  swamplink-2026-08-01` entry's level of detail.
- **A qualitative constraint, if the zero-load branch shows no movement.** If
  cooldown-HZ=0 genuinely never moves `totalTickets` while cooldown-HZ=1
  does, that is worth a named coefficient of its own —
  `mongodb.tickets-probing-requires-traffic` or similar, `role: constraint`,
  grade `measured`, `applies_to` pinned to this MongoDB patch version — because
  it changes how every future reader should think about "resting value,"
  including the existing `mongodb.tickets-probing-floor` coefficient's notes.
  Do not propose this coefficient if the result comes back ambiguous; report
  the ambiguity in FINDINGS instead.
- **A decay-rate figure only if the curve is clean enough to name one.**
  Given n=1 (or n=2 at most, both on the same shared-tenancy Hetzner box used
  before), do not grade a decay-time-constant `measured` with a tight band —
  follow investigation 003's own precedent (its Little's-law hold-time
  correction stayed unpublished as a `validation:` case for exactly this
  reason) and report it as a directional finding in FINDINGS.md rather than a
  corpus coefficient, unless the effect is large and clean enough that a wide,
  honest band still says something.
- **FINDINGS.md.** Append a new section to
  `docs/investigations/003-storage-stall-query-collapse/FINDINGS.md` — this is
  investigation 003's own open question closing, not a new investigation
  number. Update the `db.concurrency_tickets` input note and `reframe:` text in
  `data/models/mongodb-concurrency.yaml` to reflect whichever of "resting value
  applies between incidents" or "resting value may not apply if incidents
  cluster" turns out to be true.
- **Feeds issue #2 directly.** Whether the pool converges near 128 and how it
  behaves post-load are exactly the facts issue #2 needs to choose between its
  three options (scope the model to a pinned pool, add a second device-bound
  model, or make `tickets` a required input). This plan doesn't resolve #2,
  it supplies the missing measurement #2 is currently guessing around.

## 7. Effort and dependencies

- **Harness changes** (4a–4c): additive, no change to existing behavior when
  the new env vars are left at their defaults. Roughly 1–2 hours including
  testing the convergence-verdict math against the existing 2026-08-01 data
  (the T=25s levels in that run should all report `STILL MOVING` — that's
  the regression check that the verdict logic isn't vacuous).
- **Execution**: one host, one session. Load (~3–5 min for 1.5M docs) + climb
  (64 then 150, 600s each = 20 min) + cooldown (up to 900s, likely shorter
  once it hits the floor-and-stop condition) ≈ 35–45 minutes for the idle
  run. The trickle run needs its own container and its own load — reusing a
  container across both would contaminate the comparison with leftover state
  from the first cooldown — so budget roughly the same again. **Total
  wall-clock: ~1.5–2 hours**, same swamplink box as investigation 003, no new
  infrastructure.
- **Depends on**: nothing beyond what investigation 003 already stood up.
- **Blocks / feeds**: issue #2 (directly — see §6). Loosely related to T4
  (checkpoint sawtooth) in that both are about whether a summary statistic
  over a window is hiding real movement inside it, but T4 is about latency
  during load, not ticket count after load — no dependency either direction.

## 8. What could make this not worth doing

If the zero-load branch shows the pool frozen (no movement at all, per §3's
premise concern) and the trickle branch shows it decaying at whatever rate is
proportional to that trickle's own throughput — i.e., "decay" turns out to
just be the same probing algorithm running in reverse once there's a lower
throughput signal to react to, with no separate "idle timeout" behavior at
all — then there is no single decay-rate number to ship, and the honest output
is a one-paragraph correction to how the issue is framed rather than a
coefficient. That is still worth writing down (it directly changes how #2
should be resolved and it corrects a "resting value" assumption used
elsewhere in the corpus), but budget for the answer being qualitative, not a
number to plug into a formula. The one scenario where this genuinely isn't
worth running: if the trickle rate turns out to matter continuously (decay
speed is a smooth function of subsequent load rather than a fast/slow
dichotomy), a single trickle rate of ~1 op/s won't characterize that function
and a real answer would need a rate sweep during cooldown — worth flagging
before running, not discovering after, since that would turn a 1.5–2 hour
experiment into a multi-point sweep of unknown size.
