# Batch 003 — Celery prefetch and worker sizing defaults

Feeds ROADMAP T6: a worker reserves `prefetch_multiplier × concurrency` tasks
that are off the queue but not running, so queue depth understates outstanding
work. Before the sweep runs, the corpus needs the documented defaults the
amplification is computed from.

## Wanted

- `worker_prefetch_multiplier` default and what a 0 value means
- Default worker concurrency rule
- `acks_late` default and its interaction with prefetch
- Rate limits, pool sizes, and any timeout that gates throughput

## Sources fetched (2026-08-01)

| doc | what | version pin |
|---|---|---|
| 01-celery-optimizing | Celery stable docs, Optimizing guide | "stable", unpinned |
| 02-celery-workers | Celery stable docs, Workers guide | "stable", unpinned |
| 03-celery-configuration | Celery stable docs, Configuration reference | "stable", unpinned |

## Notes for the human gate

"stable" resolved to Celery 5.x on the fetch date; the pages rarely name their
own version inline, so most rows will need a human to pin `applies_to` before
promotion. The fetch URL is recorded at the top of each document.
