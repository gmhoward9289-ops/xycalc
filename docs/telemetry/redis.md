# Redis telemetry — broker memory and eviction

What we want when Redis backs a Celery broker, especially near `maxmemory`.

## Obtainable from a running instance

| Series | Command / field | Use |
|---|---|---|
| Memory use vs ceiling | `INFO memory` → `used_memory`, `maxmemory`, `maxmemory_human` | Alert before policy failure; T7 guard ratio |
| Eviction activity | `INFO stats` → `evicted_keys` | Prove LRU policies actually evicted; guard for noeviction |
| Policy in effect | `INFO memory` → `maxmemory_policy` | Confirm arm label matches runtime |
| Queue depth | `LLEN celery` (or configured queue name) | Backlog while broker is at ceiling |

## Manufacturable locally

Investigation 005 (`tools/bench/celery_probe/run_evict.sh`) samples the above
every 0.5s during Phase 2 and records Phase-1 end state. Execution ground
truth is **not** taken from the broker under test — see separate bookkeeping
Redis in the harness.

## Not obtainable from Redis alone

Whether a "lost" task ever executed requires an out-of-band record (bookkeeping
store or application idempotency keys). Broker counters alone under eviction
undercount executions — that gap is why T7 uses two Redis instances.

## Vendor figures already in the corpus (no live instance needed)

| Quantity | Source | Corpus slug |
|---|---|---|
| Empty key overhead 56 B | MEMORY USAGE docs (Redis 7.2.0 jemalloc) | `redis.empty-string-key-overhead-bytes` |
| `maxmemory-samples` default 5 | Key eviction / redis.conf | `redis.maxmemory-samples-default` |
| `list-max-listpack-size` -2 (8 KB) | redis.conf 7.2 | `redis.list-max-listpack-size-default` |
| volatile-* ≡ noeviction without TTLs | Key eviction docs | `redis.volatile-no-ttl-equals-noeviction` |
| 20% free RAM beyond maxmemory | Redis FAQ | `redis.maxmemory-free-ram-headroom-pct` |

## Alerting (vital subset)

Full board + Celery backlog notes: [`recommendations.md`](recommendations.md)
(Simple / Advanced / Evidence).

| Priority | Signal | Suggested start |
|---|---|---|
| Page | `used_memory/maxmemory` ≥ **0.95** | 2 min — both documented policies fail at ceiling (investigation 005) |
| Page | `evicted_keys` rate > 0 on `allkeys-*` Celery broker | 2 min — silent task loss |
| Ticket | Ratio ≥ **0.70** / **0.85** | 10 min / 5 min — tune to growth rate |
| Ticket / page | Rising **outstanding** work (not `LLEN` alone) | Prefetch understates broker depth (issue #14) |

Host: keep ~20% free RAM beyond maxmemory (`redis.maxmemory-free-ram-headroom-pct`).

## Still wanted for a broker-bytes model

| Series | How | Status |
|---|---|---|
| Bytes per queued Celery task | `MEMORY USAGE <queue>` delta / `LLEN` under known payload | manufacturable (probe) |
| Binding-key footprint | `MEMORY USAGE _kombu.binding.*` | obtainable |
| Peak RSS vs `used_memory` after fill+drain | `INFO memory` | obtainable; vendor warns RSS tracks peak |
