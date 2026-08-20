# Findings — Redis broker maxmemory (T7)

**Investigated:** 2026-08-20 · **Harness:** `tools/bench/celery_probe/run_evict.sh`
· **Validation:** `benchmark` on swamplink, Celery **5.4.0**, Redis **7.4.10**,
`maxmemory=16mb`, payload **2048 B**, n=1 run per policy (2026-08-20).

---

## The short answer

**Both documented options fail on Celery 5.4.0 — in different ways.** Neither
is safe once the broker hits `maxmemory` under this harness.

| Policy | Worker consumes at OOM? | Task loss rate | Producer errors at ceiling |
|---|---|---|---|
| `noeviction` | **No** (0/1) | **100%** (3010/3010 lost) | **4.9%** of send attempts |
| `allkeys-lru` | **Yes** (1/1, first task ~1s) | **68.7%** (3010/4380 lost) | **0%** |

Corpus slugs: `celery.redis-broker-noeviction-*` and
`celery.redis-broker-allkeys-lru-*` in
`data/coefficients/celery-redis-evict-2026-08-20.yaml`.

**Operational takeaway:** alert on `used_memory/maxmemory` long before either
policy's failure mode — the choice between them only matters once you are
already on fire, and both lose tasks.

---

## The disagreement (measured on 5.4.0, not declared a winner)

**Celery 5.4.0 documentation** says to configure Redis so keys are not evicted
unexpectedly:

> the `maxmemory-policy` option to `noeviction` or `allkeys-lru`
> — [Using Redis — Celery 5.4.0](https://docs.celeryq.dev/en/v5.4.0/getting-started/backends-and-brokers/redis.html)

That presents two policies as equally valid responses to broker memory pressure.

**Practitioner report (celery#5716)** described the opposite failure under
`noeviction`: once Redis hits `maxmemory`, workers cannot even declare the
queue. **This reproduced on Celery 5.4.0:** worker-starts=0, 100% task loss,
154/3164 producer `send_task` failures once at the ceiling.

**Practitioner guidance (not in Celery docs)** warns that `allkeys-lru` silently
drops queued tasks. **Also confirmed:** 68.7% task loss (3010 of 4380 enqueued
never executed per bookkeeping ground truth), with eviction active during Phase 1.

Celery's docs present both policies as equally valid. **The numbers say neither
is safe at the ceiling** — pick your failure mode (stall vs silent loss), or
stay far below `maxmemory`.

---

## What the corpus carries today

| Figure | Grade | What it means |
|---|---|---|
| `redis.broker-visibility-timeout-default-seconds` | documented | Celery transport default (1 hour), not Redis's |
| `celery.redis-broker-maxmemory-policy-documented-count` | documented | Celery names **two** policies: `noeviction`, `allkeys-lru` |
| `redis.maxmemory-default-bytes` | documented | Shipped Redis 7.2 default: **0** (no limit until configured) |
| `redis.maxmemory-policy-default` | code | Shipped redis.conf default comment: `noeviction` |
| `celery.redis-broker-noeviction-oom-stall-reported` | practitioner | celery#5716 deadlock report at OOM (Celery 4.3.0) |
| `redis.volatile-no-ttl-equals-noeviction` | documented | Vendor: volatile-* ≡ noeviction when no TTLs exist |
| `redis.noeviction-reads-still-work` | documented | Vendor: reads (e.g. GET) still served under noeviction OOM |
| `redis.empty-string-key-overhead-bytes` | documented | MEMORY USAGE empty key = **56 B** (Redis 7.2.0 jemalloc) |
| `redis.maxmemory-samples-default` | documented | Approximated LRU samples **5** keys by default |
| `redis.list-max-listpack-size-default` | code | Quicklist nodes default **-2** (8 KB) |
| `redis.small-aggregate-avg-memory-saving-factor` | documented | Compact encodings average **5×** less memory |
| `redis.hash-max-listpack-entries-default` | documented | Hash listpack threshold **512** fields (Redis ≥ 7.0) |
| `redis.maxmemory-free-ram-headroom-pct` | documented | FAQ rule of thumb: **20%** free host RAM beyond maxmemory |

| `celery.redis-broker-noeviction-task-loss-rate` | measured | **1.0** at 16mb / 2048B |
| `celery.redis-broker-allkeys-lru-task-loss-rate` | measured | **0.687** at 16mb / 2048B |
| `celery.redis-broker-noeviction-worker-starts` | measured | **0** (deadlock reproduced) |

---

## Enrichment note (2026-08-20)

Primary Redis vendor pages filled the sizing floor the policy conflict does
not: empty-key overhead, LRU sample size, listpack/quicklist defaults, compact
encoding savings, and host headroom. **Still missing from public docs** (and
therefore still for the probe / MEMORY USAGE under load): bytes per Celery
task message in a LIST, and measured loss rates under each policy.

A second disagreement is now explicit: Redis docs say read-only commands keep
working under `noeviction` at maxmemory, while celery#5716 reports `LLEN`
failing with OOM inside a Celery pipeline. Unresolved until the noeviction
sweep arm lands.

---

## Weakest inference

The **`volatile-lru` arm** is only meaningful when TTL-bearing keys exist to
evict (e.g. result backend with `task_ignore_result=False` and
`result_expires` set). With Celery's default `task_ignore_result=True`, the
arm is expected to match `noeviction` — an empty keyspace artifact, not a
policy result. Redis Key eviction docs now back this in the corpus
(`redis.volatile-no-ttl-equals-noeviction`). The harness flags it explicitly;
do not read the arm as "volatile-lru is safe."

---

## What would validate the model

1. Three-arm sweep on swamplink with guards passing (`used_memory/maxmemory ≥
   0.95`, eviction observed on LRU arms, `evicted_keys` stable on `noeviction`).
2. Import with `import_evict_probe.py --publish`.
3. Optional second pass: `volatile-lru` with `PROBE_IGNORE_RESULT=0` and
   `PROBE_RESULT_EXPIRES=60` — the configuration that actually tests volatile
   eviction candidates.

---

## Operational note

If the honest mitigation is alerting on `used_memory/maxmemory` well before
either policy's failure mode, the practical takeaway is a **monitoring
threshold**, not a policy pick. That is a legitimate answer and still worth
reporting once the sweep shows where each policy actually breaks.
