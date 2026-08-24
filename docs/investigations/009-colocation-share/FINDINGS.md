# Findings — Colocated WiredTiger share sweep (T11)

**Investigated:** 2026-08-21 · **Harness:**
`tools/bench/colocation_probe/share_sweep.sh` · **Host:** AWS EC2
`r6i.2xlarge` (64 GiB), us-east-2, MongoDB 7 Docker stack · **Instance
torn down** after pull (`i-0ab88434f24464a3e`).

**Config:** `MONGO_MEM_GB=8`, `OVERSUB=2.5`, `SHARE_PCTS=50,60,70,80`,
`REDIS_MEM=4g`, `CLICKHOUSE_MEM=8g`, `WORKER_MEM=2g` (sum of limits ≈ 22 GiB
on a 64 GiB host).

**Artifacts:** `artifacts/summary.jsonl`, `share{50,60,70,80}-*.json`,
`sweep.log`. Pulled 2026-08-21T03:17:53-04:00.

**Prior n=1 (below pressure):** reef 2026-08-19 small probe
(`data/observations/reef-colocation-probe-2026-08-19.yaml`) — 200k docs,
145 MB dataSize vs 1 GB cache; mongod RSS nearly doubled loaded→under_load
(265→453 MiB).

---

## The short answer

1. **Mongo RSS tracks the WiredTiger share**, not host RAM. Under load,
   mongod sat at ~81–82% of the configured cache across all four shares
   (eviction-target occupancy, not a surprise).
2. **Neighbors did not move** when Mongo's share rose from 50% → 80%.
   Redis stayed ~4–5 MiB, Celery worker ~115–123 MiB, ClickHouse ~340–365 MiB
   loaded/under_load. Raising WT past the vendor's 50–70% band did **not**
   register as measurable neighbor RSS pressure in this harness.
3. **The reef idle→under_load ~2× jump did not reproduce** once the dataset
   filled the cache. Loaded ≈ under_load for mongo at every share
   (e.g. 50%: 3.274 → 3.275 GiB). That earlier jump was connection /
   working-memory overhead on a tiny resident set — noise at scale, not the
   colocation story.

| Claim | Verdict |
|---|---|
| Cap WT at 50–70% of Mongo's own share or neighbors suffer | **Not supported by this measurement.** Neighbor RSS flat through 80%. |
| Mongod RSS under load ≈ configured cache × occupancy | **Supported** (~0.81× cache_gb). |
| Loaded→under_load RSS doubles even below cache | **Falsified at this scale** (artifact of the small reef probe). |

---

## Backing data — share sweep

| share | cache_gb | docs | mongo idle | mongo loaded | mongo under_load | CH under_load | redis under_load | worker under_load |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 50% | 4.0 | 14.2 M | 71 MiB | 3.274 GiB | 3.275 GiB | 352 MiB | 4.7 MiB | 119 MiB |
| 60% | 4.8 | 17.0 M | 71 MiB | 3.895 GiB | 3.896 GiB | 353 MiB | 4.9 MiB | 119 MiB |
| 70% | 5.6 | 19.9 M | 72 MiB | 4.582 GiB | 4.582 GiB | 341 MiB | 4.6 MiB | 123 MiB |
| 80% | 6.4 | 22.7 M | 72 MiB | 5.155 GiB | 5.156 GiB | 355 MiB | 5.1 MiB | 116 MiB |

Mongo under_load / cache_gb ≈ **0.819, 0.812, 0.818, 0.806**.

---

## Weakest inference (named)

Mem_limits summed to ~22 GiB on a **64 GiB** instance. The OS page cache
was not contested at the host ceiling — this measures "does changing
Mongo's *own* WT share starve capped neighbors?" not "four services
fighting the last free gigabyte." A follow-up that sets the sum of
`mem_limit`s near host RAM (or uses a smaller instance) is required before
calling the 50–70% guidance dead when the host or parent is actually tight.
That follow-up is `file` vs `anon` reclaim, not neighbor RSS. Recipe:
`docs/telemetry/cgroup.md`.

Also n=1 host / one sweep. Direction is clear; do not invent a precise
`mongodb.colocation-share-pct` coefficient from four points that did not
move neighbors.

---

## What the corpus gets / does not get

- **Gets:** observations for mongo RSS at each share × phase
  (`data/observations/aws-colocation-share-2026-08-21.yaml`), citing this
  write-up. Coefficient notes on
  `mongodb.cache-size-pct-ceiling` / research §7 updated to point here.
- **Does not get:** a numeric `mongodb.colocation-share-pct` replacement
  for the vendor 50–70% band. Finding is a **documented absence of
  neighbor RSS effect** under this harness, not a new recommended %.

---

## Contrast with reef 2026-08-19

| | reef (small) | AWS T11 (this run) |
|---|---|---|
| dataSize vs cache | ≪ cache (145 MB / 1 GB) | 2.5× oversub |
| mongo loaded → under_load | ~265 → 453 MiB (~1.7×) | flat within 1 MiB |
| question answered | per-service RSS *shape* | share sweep under fill |
