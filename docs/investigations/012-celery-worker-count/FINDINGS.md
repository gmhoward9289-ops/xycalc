# Findings — Celery concurrency slots for an offered rate

**Investigated:** 2026-08-21 · **Model:** `celery.concurrency-slots` ·
**Validation:** unvalidated (n=0) for the slots formula on a dedicated
ladder; oversize constraint measured on investigation 004 run 9 (n=1 host).

---

## The short answer

**Slots ≈ offered_rate × mean_task_seconds** (Little's law on the fleet).

Example: 50 tasks/s × 0.10 s hold → **5 slots**. 200 tasks/s × 0.10 s →
**20 slots** — but on the 004 stall harness the fleet only *completes* ~82/s,
so offering 200/s grows backlog no matter how many slots you provision.

Worker *processes* = `ceil(slots / --concurrency)` after you pick a
per-process concurrency. The model answers slots, not process count.

**Unvalidated** as a general sizing formula. The constraint that oversizing
hurts on this harness is measured.

---

## Reframe

"How many Celery workers?" is usually the wrong layer. Tickets and device
ops set the ceiling; Celery slots set how hard you push into it. Investigation
003's thread pool self-limited; Celery does not — see
`celery.queue-amplification`.

---

## What 004 already showed (cited)

| Run | Knob | Fleet done/s | queueDepthMax | drain s |
|---|---|---|---|---|
| 2 baseline (c=8) | defaults | 82.4 | 3408 | 36.5 |
| 8 | concurrency=4 | 106.0 | 2774 | 25.5 |
| 9 | concurrency=16 | **48.6** | **4385** | **72.1** |

Coefficient `celery.concurrency-oversize-completion-at-16` records run 9.
More slots into a saturated Mongo can *lower* completion.

---

## Tickets vs slots (do not conflate)

| Quantity | What it is | Model |
|---|---|---|
| Mongo `totalTickets` | Admission seats inside mongod | `mongodb.ticket-throughput-ceiling` |
| Celery concurrency slots | Client tasks executing at once | `celery.concurrency-slots` |
| Prefetch reserved | Off-queue but not running | `celery.worker-prefetch` (T6 / inv 014 measured understatement ≈ prefetch×c at high prefetch) |

Ops the DB supports under stall ≈ `min(device, tickets/L)`. Celery should not
offer more than that completion rate for long.

---

## Weakest inference (named)

Mean task seconds is caller-supplied. Wrong duration (healthy vs stall) moves
the answer by the same orders of magnitude as ticket hold time. The model
cannot invent it.

---

## What would validate

`tools/bench/celery_probe/sweep_concurrency.sh` at fixed offered rate above
the completion ceiling, concurrency 1,2,4,8,16. Expect: done/s flat or falling
past the device bind; backlog rising with concurrency past the bind — same
shape as run 8→9.
