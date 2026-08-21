# Inference sizing — prove scaling shapes on small iron

Branch program for `inference_sizing`. Three curve families, one rule:
**absolute capacity does not transfer; dimensionless shape often does.**
Nothing here needs a huge machine. Every claim below is falsifiable on Docker
plus a throttled device (or local NVMe for the storage arm).

Per-issue plans stay authoritative for harness detail. This file is the
**order, transfer test, and what each family is allowed to claim.**

---

## The rule (read before any run)

1. **Rewrite the claim so the x-axis is a ratio** (or a ratio-derived
   crossover). If you cannot, small→large inference is not on the table —
   measure capacity on the class of box you care about, or mark the model
   `unvalidated` for that class.
2. **Two absolute sizes, same ratios** when the product is an extrapolation.
   If the knee / slope sits at the same *ratio* on both sizes, transfer is an
   argument. If it moves, scale-invariance is falsified — write that down;
   do not widen a band to hide it.
3. **One cliff per sweep.** Cache shape at concurrency=1. Ticket / queue
   shape at fixed working-set ratio. Mixing them attributes the wrong knee.
4. **Guard first** (`tools/bench/README.md`). A clean table that would also
   print under a healthy environment measured nothing.
5. **Land via `xy-observe`.** `machine_class`, `workload`, `system_version`,
   `at_term`. One case is `n=1` — say which terms it tested.

Expected confidence: `measured` for shape on the hardware that ran;
`estimate` (named) for any absolute size not in the two-size pair.

---

## Ladder (run in this order)

Wall-clock on one reef/swamplink-class box. Families are independent after
smoke; order is about *shared failure modes*, not dependency of math.

| Phase | Family | Roadmap / issues | Harness | Why this order |
|---|---|---|---|---|
| **A** | Cache / oversubscription | T1 · #9 · inv 006 | `cache_cliff_probe.sh` | **Done through 50×** (FINDINGS closed; 100× out of scope) |
| **B** | IOPS ↔ throughput crossover | T9 · #17 | `io_crossover_probe.sh` | No MongoDB; pure ratio math; cheapest falsification |
| **C** | Concurrency / tickets / queue | T4-ish · #2 · #3 · inv 003–004 | `ticket_probe.sh`, `celery_probe/` | Needs A’s “device actually binds” discipline; separate cliff |

Compression shape (T2 / #10 / inv 010) is a fourth curve family — **done
2026-08-21** (`wider-than-band`; snappy 0.99–9.17). See
`docs/investigations/010-compression-shape/FINDINGS.md`.

---

## A — Cache cliff vs oversubscription

**Claim (shape).** As `dataSize / maxCache` grows, throughput either (i) falls
smoothly or (ii) has a steep segment; if (ii), the steep segment starts at
ratio R\* (candidate folklore: R\* = 1.0).

**What transfers.** The curve of relative throughput (or pages-read-per-op)
vs oversubscription ratio — *if* two absolute cache sizes agree on R\* and
on whether the steep segment exists.

**What does not.** Absolute ops/s, latency floors, “this working set fits
on r6i.4xlarge.”

**Falsifies.**

- Smooth log–log slope across 0.5…8× (and 50×/100×) → no cliff; working-set
  sizing folklore weakens.
- Steep segment starts below 1.0× → “cache-resident means cache == data” dies.
- Two cache sizes disagree on R\* → ratio curve is **not** scale-invariant;
  `applies_to` must name the absolute size class, not pretend transfer.

**Transfer test (required before any 200 GB claim).**

| Leg | WT cache | Ratios | Notes |
|---|---|---|---|
| A1 | 0.25 GB (harness default) | 0.5…8, 50, 100 | Full cliff map |
| A2 | 1.0 GB (same throttle, concurrency=1) | same ratios, drop 50/100 if wall-clock binds | Same *ratios*, larger absolute |

Both: fresh `mongod` per ratio; device-byte guard above 1.0×; two sequential
repeats of A1 before declaring a knee. Plan: `issue-9-wt-cache-cliff.md`.
BRIEF: `docs/investigations/006-cache-cliff/BRIEF.md`.

```bash
# A1 — full sweep (run twice, sequential)
./tools/bench/cache_cliff_probe.sh > /tmp/cache-cliff-a1-r1.json
./tools/bench/cache_cliff_probe.sh > /tmp/cache-cliff-a1-r2.json

# Smoke
PROBE_RATIOS=1.0,2.0 PROBE_SECONDS=6 ./tools/bench/cache_cliff_probe.sh
```

**Corpus lands.** `cache.hit_ratio_by_oversubscription` (or documented
absence of a knee), feeds `mongodb.wt-cache`. Weakest inference to name:
transfer of R\* beyond the A1/A2 absolute pair.

---

## B — IOPS ↔ throughput crossover

**Claim (shape).** Device throughput is approximately
`min(iops_ceiling, throughput_ceiling_bytes / io_size)`: flat IOPS below the
crossover, flat MiB/s above, knee at
`io_size* ≈ throughput_ceiling_bytes / iops_ceiling`.

**What transfers.** The *ratio* that predicts the knee. A scaled-down
throttle pair with the same ratio is an equally valid test (issue-17 §3).

**What does not.** Absolute NVMe ceilings as “EBS will do this”; EBS
accounting quirks beyond what the throttle emulates.

**Falsifies.**

- Measured knee not at the arithmetic prediction for that throttle pair
  (baseline ≈42.7 KiB; throughput-cap ≈195 KiB; max/max ≈25.6 KiB) →
  rewrite `ebs.ssd-max-io-size` / `throughput_wall` as a curve, not a
  threshold — or fix the accounting story.
- Knee stuck at 256 KiB regardless of pair → “256 KiB *is* the crossover”
  folklore wins; issue-17’s correction loses.
- Local NVMe arm: no knee inside 4 KiB–1 MiB → document absence; do not
  invent one.

**Transfer test.** Three Arm A pairs (or scaled-down equivalents with the
same predicted KiB). Same fio shape; only the throttle ratio changes. Arm B
unthrottled local disk for `nvme-ssd` baseline — absolute numbers, not a
transfer claim.

```bash
# Local smoke (parser / fio path)
./tools/bench/io_crossover_smoke.sh

# Full Arm A/B — Linux + Docker (see issue-17)
./tools/bench/io_crossover_probe.sh
```

Plan: `issue-17-io-crossover-nvme-baseline.md`. Arithmetic correction to
“256 KiB crossover” belongs in FINDINGS before or with the first run — it
is already implied by documented gp3 coefficients.

**Corpus lands.** Crossover coefficient(s), `nvme-ssd` first measured
ceilings or documented absence of a knee in-range.

---

## C — Concurrency / tickets / queue amplification

Three related shape claims; keep them as separate sweeps.

### C1 — Ticket pool under storage stall (inv 003 / #2 / #3)

**Claim.** When the device binds, offered concurrency above the sustainable
rate grows a queue; throughput stays flat (device-bound), latency rises;
tickets÷hold-time predicts rate only when N is known and pinned (or has
converged).

**What transfers.** Flat-throughput-vs-concurrency *shape* under a binding
device throttle; the qualitative “queue does not drain” cliff. Ratio form:
`offered_rate / device_sustainable_rate`.

**What does not.** Absolute ticket counts from a 25 s window that never
converged; “7.0 never settles” without a long soak (#2/#3 still open).

**Falsifies.**

- Throughput rises with concurrency while the device is proven binding →
  003’s device-bound reading is wrong.
- N converges and tickets÷hold-time matches measured rate → pinned-pool
  model still useful in steady state; scope by mechanism, not version.
- N never settles on a practical timescale → model must say so; do not
  pretend a ceiling formula with a moving N.

**Transfer test.** Same concurrency ladder at two throttle pairs whose
*sustainable ops/s* differ by a known factor (e.g. 2× IOPS, same IO size).
If the flat region’s rate tracks the throttle ratio and the cliff onset
tracks `offered / sustainable`, the shape transfers; absolute ticket
trajectories may not.

```bash
# Smoke
PROBE_SECONDS=6 PROBE_DOCS=30000 ./tools/bench/ticket_probe.sh

# Full ladder — see ticket_probe.sh header / issue-2 / issue-3 plans
./tools/bench/ticket_probe.sh
```

### C2 — Celery backlog drain / amplification (inv 004 / #1)

**Claim.** Drain time scales roughly linearly with backlog when the
completion ceiling is fixed; redelivery/acks_late can amplify load.

**What transfers.** Drain-time slope vs backlog *depth* at a fixed worker
fleet and fixed dependency ceiling — dimensionless if plotted as
`backlog / (workers × rate)`.

**What does not.** Absolute drain seconds on production fleet sizes;
prefetch interaction (T6) without its own sweep.

**Falsifies.** Non-linear drain (or flat) across the backlog ladder at
fixed config → linearity assumption dies for that configuration;
`applies_to` must name it.

```bash
# See tools/bench/celery_probe/README.md — refuse-to-run below 2× oversub
cd tools/bench/celery_probe && ./sweep.sh
```

### C3 — Optional soak (T4)

One concurrency level, minutes-long, 1 s resolution — falsifies “flat mean”
if checkpoint sawtooth hides in the 25 s window. Does not need a large box;
needs patience. Run after C1’s short ladder so you know which level to soak.

---

## Shared landing checklist

For every family, before promoting figures out of `local/`:

- [ ] Guard passed (device bytes / pagesRead / fio queue depth — whichever
      the harness names)
- [ ] Shape claim written as falsifiable *before* looking at the table
- [ ] Two-size or two-ratio transfer leg done, or weakest inference named
      as “single absolute size, transfer untested”
- [ ] `xycalc build && xycalc audit` with observation + validation case
- [ ] FINDINGS: disagreements unresolved; weakest inference named

---

## What this program deliberately skips

- Vertical HW “2× cores → 2× throughput” without a ratio form — different
  research track (`docs/research/mongodb-vertical-scaling-r8.md`).
- Production Coralogix/Grafana series — telemetry docs still list them;
  this ladder is `manufacturable` only.
- Cost / RI pricing — stale prices are confident wrong answers.
- Declaring a model “validated” on n=1 from one machine class.

---

## Suggested first week on reef / swamplink

| Day | Do |
|---|---|
| 1 | A smoke + A1 run 1; B smoke |
| 2 | A1 run 2; start B Arm A baseline + throughput-cap pairs |
| 3 | A2 transfer (subset of ratios); finish B Arm A/B |
| 4 | C1 smoke + short concurrency ladder; import A/B into `local/` |
| 5 | C2 celery backlog ladder *or* C1 soak (T4); write FINDINGS stubs |

If only one thing ships: **A1+A2** — it underwrites every working-set
sizing sentence in the corpus. B is the cheapest pure-math win. C is the
one that matches the production symptom George actually saw.
