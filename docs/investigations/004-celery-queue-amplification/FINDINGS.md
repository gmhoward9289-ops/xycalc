# Findings — what a queue does to a storage stall

**Investigated:** 2026-08-20, investigation 004 full sweep on swamplink. ·
**Model:** `celery.queue-amplification` · **Validation:** none (n=1 benchmark)

> When MongoDB is stalled behind a throttled disk, does putting Celery in front
> of it turn a failure that holds steady (investigation 003) into one that gets
> worse the longer it runs?

---

## Premise notes (read before the numbers)

**A. Redelivery requires `task_acks_late=True`.** Celery's default acks before
execution. Run 1 (`acks_late=0`) produced `duplicateRatePct: 0.00` regardless
of backlog depth — that zero is **guaranteed by configuration**, not evidence
that redelivery was tested and found absent. Every duplicate-rate number in this
document comes from runs with `acks_late=1` unless labelled "control."

**B. Drain time here is under sustained throttle, not after recovery.** The
harness never lifts the blkio cap mid-run. "Drain seconds" means: arrivals stop
while MongoDB stays on the throttled device, then how long the Redis backlog
takes to clear. That is a real operational number (you are still on a bad disk),
but it is **not** "the storage blip ended and the backlog persisted" — testing
that would need a mid-run throttle lift (out of scope; flagged for follow-up).

---

## What the sweep confirmed

### 1. Backlog grows without bound above the completion ceiling

Run 2 (`acks_late=1`, default prefetch/concurrency/timeout) at sub-ceiling rates
(25, 50/s) held `queueDepthMax=0`. At 100/s the fleet completed ~74/s and
backlog appeared (792). At 200/s offered against ~82/s completed, peak depth
reached **3408 in 30 seconds**. At 400/s offered against ~97/s completed, peak
depth reached **8885**.

That is not surprising queue theory — but the **cost** is: on this configuration,
every second above ~82 tasks/s offered adds roughly `(offered − 82)` tasks to a
backlog with nothing to stop it except broker memory.

### 2. Drain time outlasts the arrival window — while the disk stays bad

| Offered rate | Arrival window | Peak backlog | Drain after stop | Device during drain |
|---|---|---|---|---|
| 200/s | 30s | 3408 | **36.5s** | still throttled |
| 400/s | 30s | 8885 | **99.1s** | still throttled |

A 30-second overload at 400/s became **99 seconds** of continued queue drain on
a database that was still I/O-starved. That is the headroom term the model cites.

### 3. Redelivery did not show up — even with late ack and short visibility timeout

With `acks_late=1`, visibility timeout swept to **10s, 5s, and 2s** (runs 3–5,
60s windows at 200/s), `duplicateRatePct` remained **0.00** on every row. Guard 1
passed on each run.

This **falsifies the flashiest sub-claim in BRIEF.md for this configuration**:
the broker did not add duplicate load during the stall. Tasks were slow and
backlogs were large, but no task id executed twice per the probe's Redis
counters.

Possible explanations (not settled here): visibility timeout may count from
broker delivery to worker ack, and prefetched tasks may not exceed the window;
average in-flight time may have stayed below even the 2s setting once work
started; or kombu/redis transport behaviour differs from the naive reading. The
honest finding is the measurement: **zero duplicates, acks_late=1, timeout 2–30s,
this harness.**

Do not read that as "Celery redelivery is folklore." Read it as "this exact
docker-compose file on swamplink on 2026-08-20 did not exhibit redelivery-driven
amplification." Deployments with longer handler times or different prefetch may
still cross the threshold.

### 4. Knobs moved the ceiling and backlog — one factor at a time

At fixed 200/s offered (`acks_late=1`):

| Run | Knob | Fleet done/s | queueDepthMax | drain s |
|---|---|---|---|---|
| 2 baseline | defaults | 82.4 | 3408 | 36.5 |
| 6 | prefetch=1 | 104.5 | 2817 | 24.5 |
| 7 | prefetch=16 | 117.2 | 2316 | 20.5 |
| 8 | concurrency=4 | 106.0 | 2774 | 25.5 |
| 9 | concurrency=16 | 48.6 | 4385 | 72.1 |

Higher prefetch/concurrency is not free: it changes how hard the fleet hammers
the already-stalled MongoDB. Issue #14 owns the prefetch-vs-backlog question;
these rows are baseline observations, not a generalized prefetch coefficient.

---

## Guards (all runs)

- `oversubscription` ≥ 2.0 (4.05× cache) on every run.
- `pagesReadIntoCache` > 0 on every rate row — reads reached the throttled device.
- Producer achieved target rate within tolerance on all reported rows (including
  400/s).
- Run 1 control printed `acksLateVacuousZeroDuplicates` as expected.

---

## What landed in the corpus

- **Observations:** `data/observations/swamplink-celery-probe-2026-08-20.yaml`
  (51 rows from nine runs).
- **Coefficients:** `data/coefficients/celery-queue-amplification-2026-08-20.yaml`
  (headline benchmark figures + duplicate-rate controls).
- **Documented/code defaults:** `data/coefficients/celery.yaml`, `redis.yaml`.
- **Model:** `data/models/celery.yaml` — `celery.queue-amplification`.
- **Import:** `tools/import_celery_probe.py` from sweep JSON logs.

---

## What would falsify this write-up

- Re-running with a producer that cannot reach 400/s would invalidate the top
  rate rows (Guard 2 — did not trigger here).
- Lifting blkio mid-run and measuring post-recovery drain would change the drain
  story; numbers here do not claim that scenario.
- A deployment where tasks routinely hold tickets longer than `visibility_timeout`
  with `acks_late=1` should show non-zero duplicates — this sweep did not.
