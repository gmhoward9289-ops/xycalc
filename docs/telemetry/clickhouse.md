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
| `system.merge_tree_settings` → `max_avg_part_size_for_too_many_parts` | bytes | `obtainable` | **Scale gate.** If average active part size exceeds this (1 GiB on ≥23.6, 10 GiB before), delay/throw part-count checks do not bind. Total table size is irrelevant; average part size is. |
| `system.settings` → `async_insert` | 0/1 | `obtainable` | If 1, client single-row inserts can coalesce into fewer parts and silently disable the mechanism under test. Probe requires 0. |

```sql
SELECT name, value
FROM system.merge_tree_settings
WHERE name IN (
  'parts_to_delay_insert',
  'parts_to_throw_insert',
  'max_delay_to_insert',
  'max_avg_part_size_for_too_many_parts'
);
```

## 1 GB vs 500 GB vs 1 TB — what actually changes

| Quantity | Scales with total table bytes? | Notes |
|---|---|---|
| `parts_to_delay_insert` / `parts_to_throw_insert` | **No** | Same counts at 5 GB and 5 TB while the check is active. |
| Whether those counts bind at all | **Indirectly** | Via *average part size* vs `max_avg_part_size_for_too_many_parts`. Mature large tables with merged ~GiB parts often skip the check; tiny streaming parts always face it. |
| Inserts/sec at which parts outrun merges | **Yes (hardware + part size)** | Merge cost grows with part bytes; probe floors are hardware-scoped, not portable. |
| Point-lookup / scan latency under load | **Yes** | Working set, marks, filesystem cache. A 300k-row probe does **not** predict 1 TB read latency — compare only like-with-like (same harness keys: `meanLatencyMs` / `p95LatencyMs` / `opsPerSecond` as Mongo `ticket_probe` / `occupancy_band_probe`). |

`mongodb.wt-cache` takes `--storage-size` because cache need scales with uncompressed data. `clickhouse.parts-insert-ceiling` does **not** take a TB input — that would be the wrong model of this failure.

```sql
SELECT value FROM system.settings WHERE name = 'async_insert';
```

## Part pressure during a load

| Series | Unit | Agg | Window | Status | Why |
|---|---|---|---|---|---|
| `count() FROM system.parts WHERE table = … AND active` | count | last | 250 ms | `obtainable` | Central measurement for insert-batch-floor. Compare to the delay/throw thresholds above. |
| `count(DISTINCT partition) FROM system.parts WHERE table = …` | count | last | 250 ms | `obtainable` | Must stay 1 for the probe table (no PARTITION BY). >1 means the threshold is being spread and the experiment is void. |
| Client-caught `Too many parts` exception count | count | sum | run | `manufacturable` | Only valid reject signal — never infer reject from latency alone. |
| `avg(bytes_on_disk)` of active parts | bytes | last | 250 ms | `obtainable` | Compared to `max_avg_part_size_for_too_many_parts`; probe refuses if the check is no longer active. |
| Write latency under insert load (`write.{mean,p50,p95,p99}LatencyMs`, `opsPerSecond`) | ms / ops/s | — | step | `manufacturable` | Same key names as Mongo `ticket_probe`. During delay, write p99 ≈ `max_delay_to_insert` (1s sleep) — not storage latency. |
| Read latency under concurrent inserts (`read.*` same keys) | ms / ops/s | — | step | `manufacturable` | Point lookups (`SELECT … WHERE id =`) while writers run — the fair Mongo compare for load latency. `PROBE_READERS` (default 4). |
| Achieved inserts/sec at each batch size | ops/s | mean | step | `manufacturable` | Alias of write `opsPerSecond`. If batch=1 is implausibly slow, the harness was the bottleneck, not ClickHouse. |

## Events — confirmed live (2026-08-21 dual probe)

Queried via diffs of `system.events` during `clickhouse_probe` steps on
`clickhouse/clickhouse-server:23.3` and `:24.8`:

| Event | Role |
|---|---|
| `DelayedInserts` | Count of inserts that slept under part pressure |
| `DelayedInsertsMilliseconds` | Artificial sleep budget consumed |
| `RejectedInserts` | Server-side rejects (pairs with client `Too many parts`) |
| `FailedInsertQuery` | Failed insert queries (includes rejects) |
| `InsertedCompactParts` / `InsertedRows` | Part/row creation progress |

Credit delay only when `DelayedInserts` rises **and** active parts crossed
`parts_to_delay_insert`. Credit reject from client exception text and/or
`RejectedInserts`.

```sql
SELECT event, value
FROM system.events
WHERE event IN (
  'DelayedInserts',
  'DelayedInsertsMilliseconds',
  'RejectedInserts',
  'FailedInsertQuery'
)
ORDER BY event;
```

## Memory / colocation (already sampled elsewhere)

Investigation 009 / `colocation_probe` and `s3_stack` already record ClickHouse
container RSS idle/loaded/under_load. Those observations validate neighbor RAM
pressure, not insert-part thresholds — different question, different series.

## Merges-on / slow-disk probe knobs

`tools/bench/clickhouse_probe.sh` can starve merges without a permanent
`SYSTEM STOP MERGES`:

| Env | Role |
|---|---|
| `PROBE_STOP_MERGES=0` | Leave merge machinery enabled |
| `PROBE_MERGE_DUTY_CYCLE` | Fraction of each period merges are STARTed (e.g. `0.05`) |
| `PROBE_BACKGROUND_POOL_SIZE` | Cap server merge pool via config.d |
| `PROBE_DEV` / `PROBE_*_BPS` / `PROBE_*_IOPS` | Container-scoped block-IO cgroup throttle |
| `PROBE_DATA_DIR` | Bind-mount `/var/lib/clickhouse` onto the throttled device |
| `PROBE_FSYNC_INSERTS=1` | `fsync_after_insert` so tiny parts hit the device |

Continuous merges on the Cursor cloud agent VM never crossed delay (peak ≪
150). Duty-cycle `0.05` did — see investigation 012 FINDINGS and
`artifacts/clickhouse_probe_merges_on_duty.json`.
