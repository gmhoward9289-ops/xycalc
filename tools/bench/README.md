# Benchmark harnesses

This directory holds the repo's benchmark harnesses: `ticket_probe.sh` (thread
concurrency against a throttled MongoDB) and `celery_probe/` (the same
workload driven by a Celery fleet instead of raw threads). See each for its
own README/comments.

## Before you believe a result

Both harnesses above have separately produced a clean, plausible table that
measured nothing. Run this checklist before trusting any benchmark's output,
new or old:

- **What does this print if the thing I am measuring never happened?** If the
  answer is "the same table," the harness can't tell success from failure.
- **Is the load generator actually concurrent, or does the client serialise
  it?** `mongosh` auto-awaits, so 64 "concurrent" calls issued through it ran
  serially — full table, plausible numbers, no concurrency. `ticket_probe.sh`
  now requires real OS threads for exactly this reason.
- **Did the constrained resource get touched at all?** Counter, not
  inference. 20k docs against a 250 MB WiredTiger cache still fit — `db.stats()`
  showed `pagesReadIntoCache: 0`, meaning no read ever reached the throttled
  device. `celery_probe/` now refuses to run below 2x working-set
  oversubscription and checks this counter directly.
- **Is the limit I set the limit that bound?** A cgroup limit doesn't bind if
  a layer above it absorbs the work. 605 MB at 2.3x oversubscription still
  passed the guard above and still touched nothing, because the host's page
  cache served it from RAM — the block-IO throttle only sees device traffic,
  and a container `mem_limit` bounds heap, not page cache, unless set wide
  enough to also cap it.
- **Would this produce a plausible table if the environment were healthy?**
  The general form of all of the above, and the one that catches the most.
  Ask it first.

Each guard above was added after the failure it describes, which only proves
the checklist was applied in arrears. Apply it in advance on the next
harness, not after it ships a clean table for the wrong reason.
