# Investigation 004 — what a queue does to a storage stall

**Question:** Investigation 003 characterised the failure with raw threads. What
changes when the load arrives through Celery instead?

**Status:** full sweep complete on swamplink 2026-08-20; coefficients and
FINDINGS.md landed. See `docs/investigations/004-celery-queue-amplification/FINDINGS.md`.

**Expected confidence ceiling:** `benchmark` for anything measured here,
`documented` for Celery and Redis broker defaults. The interesting quantities —
how long a backlog takes to drain, how much duplication a stall causes — are
properties of a configuration, not of Celery, so they will never be better than
`benchmark` and must not be presented as general.

---

## Why this is a different question

Threads are self-limiting: a thread waiting on a query cannot issue another, so
offered concurrency is bounded and the system settles into an equilibrium —
throughput flat, latency rising linearly. That is exactly what 003 measured, and
it is a *stable* failure. Ugly, but stable.

A queue removes the bound. Three consequences, none visible in 003:

1. **Backlog grows without limit** when arrivals exceed the ceiling. The smoke
   run showed 200 tasks/s against a fleet managing 158/s reaching depth 478 in
   twelve seconds, with nothing to stop it.
2. **Drain time outlives the arrival window under sustained throttle.** The
   harness never lifts the blkio cap mid-run, so `drainSeconds` is how long the
   backlog takes to clear while the disk stays bad — not recovery after a stall
   ends. Still the term that turns a short overload into a long outage on that
   disk; post-recovery drain would need a mid-run throttle lift (see FINDINGS).
3. **The broker can add load during the stall — only with late ack.** With
   `task_acks_late` (this probe's default), Redis redelivers anything
   unacknowledged within `visibility_timeout`. A stall makes tasks slow, which
   is precisely when they cross that threshold — so duplication arrives at the
   worst possible moment. Same shape as the eviction feedback loop in 001.
   Ack-before-execute makes redelivery structurally impossible; a zero
   duplicate count in that mode proves nothing about load.

If (3) is significant under late ack, it is the most important thing in this
investigation: a system that responds to overload by generating more load.

---

## Decomposition

- **FLOOR** — the completion rate the database can sustain. Investigation 003
  already established this is set by the device, not by Celery.
- **AMPLIFIER** — duplication from redelivery; retries; prefork connection
  multiplication (workers × pool size).
- **HEADROOM** — the tail: drain time, and peak backlog against whatever the
  broker can hold.
- **CONSTRAINT** — `visibility_timeout` versus actual task duration under
  stall; prefetch hiding backlog from queue depth.

---

## Do NOT do

- **Do not present drain time or duplication rate as properties of Celery.**
  They are properties of a configuration against a particular database on
  particular hardware. `applies_to` must name the configuration.
- **Do not lower `PROBE_DOCS` to make runs faster.** Two earlier harnesses in
  this repo produced clean tables that measured nothing because the working set
  fit in cache. The guards exist; do not route around them.
- **Do not run it concurrently with the thread probe on the same host.** They
  contend for one device, and 003's data already carries that caveat.
- **Do not conclude "Celery is bad".** The question is what a queue does to a
  stalled dependency. Every queue does some of this; the useful output is which
  knobs change it and by how much.
