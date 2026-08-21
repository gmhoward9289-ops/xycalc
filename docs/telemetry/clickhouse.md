# Telemetry wanted — ClickHouse

Investigation 012 (T10 / issue #18) cares about insert backpressure: when
active parts per partition outrun merges, ClickHouse first delays inserts
(`parts_to_delay_insert`) and then rejects them (`parts_to_throw_insert`).
Defaults jumped ~10× at 23.6 — every series below is useless without the
version that produced it.

## Settings that decide whether ingestion works

| Series | Unit | Status | Why |
|---|---|---|---|
| `system.merge_tree_settings` → `parts_to_delay_insert` | count | `obtainable` | Delay threshold. Must be read from the live server — do not trust the image tag. |
| `system.merge_tree_settings` → `parts_to_throw_insert` | count | `obtainable` | Reject threshold. Same query as above; assert pre- vs post-23.6 images differ before any probe sweep. |
| `system.merge_tree_settings` → `max_delay_to_insert` | seconds | `obtainable` | Cap on artificial INSERT sleep under part pressure. |
| `system.settings` → `async_insert` | 0/1 | `obtainable` | If 1, client single-row inserts can coalesce into fewer parts and silently disable the mechanism under test. Probe requires 0. |

```sql
SELECT name, value
FROM system.merge_tree_settings
WHERE name IN (
  'parts_to_delay_insert',
  'parts_to_throw_insert',
  'max_delay_to_insert'
);

SELECT value FROM system.settings WHERE name = 'async_insert';
```

## Part pressure during a load

| Series | Unit | Agg | Window | Status | Why |
|---|---|---|---|---|---|
| `count() FROM system.parts WHERE table = … AND active` | count | last | 250 ms | `obtainable` | Central measurement for insert-batch-floor. Compare to the delay/throw thresholds above. |
| `count(DISTINCT partition) FROM system.parts WHERE table = …` | count | last | 250 ms | `obtainable` | Must stay 1 for the probe table (no PARTITION BY). >1 means the threshold is being spread and the experiment is void. |
| Client-caught `Too many parts` exception count | count | sum | run | `manufacturable` | Only valid reject signal — never infer reject from latency alone. |
| Achieved inserts/sec at each batch size | ops/s | mean | step | `manufacturable` | If batch=1 is implausibly slow, the harness was the bottleneck, not ClickHouse. |

## Events — names confirmed at experiment time

Do not hard-code a `system.events` counter name until a live version has been
queried. Versions differ in what they expose.

```sql
SELECT event, value
FROM system.events
WHERE event ILIKE '%insert%' OR event ILIKE '%part%'
ORDER BY event;
```

Whichever delay/reject counters the running version actually increments belong
here once confirmed — same posture as discovering that MongoDB 7.0.39 still
exposes tickets under `wiredTiger.concurrentTransactions` rather than the
documented `queues.execution` path.

## Memory / colocation (already sampled elsewhere)

Investigation 009 / `colocation_probe` and `s3_stack` already record ClickHouse
container RSS idle/loaded/under_load. Those observations validate neighbor RAM
pressure, not insert-part thresholds — different question, different series.
