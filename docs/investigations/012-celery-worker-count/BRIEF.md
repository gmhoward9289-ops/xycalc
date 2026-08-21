# Investigation 012 — how many Celery concurrency slots do I need?

**Question (as asked):** optimal concurrency settings, Celery worker count,
how many transaction tickets might be needed, or ops to the DB supported.

**Reframe:** the answerable piece that is not already `mongodb.ticket-throughput-ceiling`
or `celery.queue-amplification` is: **how many Celery concurrency slots does
my offered rate require, and what caps that number when Mongo is stalled?**

**Status:** model landed from Little's law + investigation 004 concurrency
knob rows. Dedicated concurrency ladder (1…16 at fixed offered rate) still
optional validation — harness `sweep_concurrency.sh`. Expected confidence
ceiling: `estimate` for the slots formula applied to a new workload;
`measured` for the oversize-hurts constraint on the 004 configuration.

---

## Is this the right question?

Often no. People ask for worker count when the binding constraint is:

1. device ops/s (investigation 003), or
2. tickets ÷ hold time (`mongodb.ticket-throughput-ceiling`), or
3. open-loop backlog growth once offered > completion
   (`celery.queue-amplification`).

Worker count only sets how hard the fleet hammers those ceilings. Sizing
workers *above* the stall completion rate grew backlog and *lowered*
throughput in 004 run 9 (concurrency 16 → 48.6 done/s vs ~82 baseline).

Answer the slots question anyway, and say so.

---

## Decomposition

| Role | Term |
|---|---|
| **floor** | Offered task rate (tasks/s) |
| **amplifier** | Mean task duration while occupying a slot (seconds) → Little's law `slots = rate × duration` |
| **headroom** | Prefetch reserves `prefetch × concurrency` off-queue (documented); T6 owns how that hides backlog |
| **constraint** | Stall completion ceiling (~82/s on 004 harness); oversizing past device capacity can reduce done/s; Mongo tickets / device bind independently |

Workers (process count) = `ceil(slots / --concurrency)` once you pick a
per-process concurrency. The model answers **slots**; converting to process
count is arithmetic outside the band.

---

## Do NOT do

- Do not present the slots number as "how many tickets Mongo needs." Tickets
  are admission seats inside mongod; Celery slots are client demand.
- Do not size for the healthy-path rate without stating the stall ceiling
  constraint — that is how fleets look fine until the disk blips.
- Do not treat run 9's concurrency=16 result as a universal "16 is bad"
  coefficient; it is a constraint on *this* harness: more demand into a
  saturated device.
- Do not wait for a new bench to land the Little's-law model — 004 already
  measured the oversize branch; the formula is standard.
