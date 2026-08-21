# Investigation 014 — Celery prefetch vs broker-visible backlog

**Question:** If I alert on Redis queue depth, how far does that understate
true outstanding work as `worker_prefetch_multiplier` rises?

**Status:** Landed 2026-08-21 — see FINDINGS.md.

**Expected confidence ceiling:** `measured` on the celery_probe / swamplink
configuration; `documented` for the prefetch × concurrency reservation
formula itself.
