# Investigation 009 — Colocated WT cache share (T11)

**Question as asked:** When MongoDB, Redis, ClickHouse, and Celery share one
box, should WiredTiger be capped to 50–70% of Mongo's *own* RAM share (vendor
narrative), and what does "competing for RAM" look like when the dataset
actually approaches the configured cache?

**Status:** first scaled sweep complete (2026-08-21) on AWS `r6i.2xlarge`
(us-east-2). See `FINDINGS.md`. Reef small-scale probe (2026-08-19) remains
the idle/loaded/under_load shape reference at below-cache size.

**Expected confidence ceiling:** `measured` for mongo RSS vs share and for
neighbor RSS flatness under this harness. Not a host-ceiling stress test —
mem_limits sum (~22 GiB) on a 64 GiB instance left substantial free RAM.

**Falsifies (from ROADMAP):** if neighbors' RSS is flat regardless of Mongo
cache share up to and past 80%, the "cap at 50–70%" guidance is not doing
anything this colocated deployment can measure.
