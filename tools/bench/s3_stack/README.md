# S3-backed colocation stack

Self-contained Docker Compose stack for exercising the four services named in
`docs/research/mongodb-vertical-scaling-r8.md` §7, with ClickHouse MergeTree
parts stored on S3-compatible object storage (MinIO locally).

Intended host: **Ubuntu 22.04** (or similar) with Docker Engine + Compose v2.
COOPER (Windows) has no Docker in PATH; run on a Linux box (swamplink / reef).

## Entrypoints

| script | purpose |
| --- | --- |
| `./run.sh` | Smoke: services answer; MergeTree parts on `disk_name=s3` |
| `./perf.sh` | **Performance:** idle → loaded → under_load RSS (+ CH scan timing) |

```bash
cd tools/bench/s3_stack && ./perf.sh
# optional: ./perf.sh --down
```

`perf.sh` is the ROADMAP T11 shape (colocation under real memory pressure)
with ClickHouse on S3 instead of local disk. It refuses a Mongo load below
`PROBE_MIN_OVERSUB` (default 2.0× WiredTiger cache) — same gate as
`celery_probe/drive.py` — and refuses to continue if ClickHouse parts are not
on the `s3` disk after load.

Writes `results.json`. That file is measurements, not a coefficient; feed a
real run through `/xy-observe` before anything lands in the corpus.

### Perf defaults (override via env)

| knob | default | why |
| --- | --- | --- |
| `MONGO_CACHE_GB` | `0.5` | Explicit WiredTiger size (container auto-detect lies) |
| `MONGO_MEM` | `1g` | Caps page cache + heap so oversub can bind |
| `PROBE_DOCS` | `1500000` | Targets ≥2× oversub against 0.5 GB cache |
| `PROBE_MIN_OVERSUB` | `2.0` | Refuse-to-run gate |
| `PROBE_RATES` / `PROBE_SECONDS` | `50` / `30` | Celery under_load window |
| `PROBE_CONCURRENCY` | `4` | Celery prefork workers |
| `CH_ROWS` | `5000000` | ClickHouse insert size for the loaded phase |

## What comes up

| service | image | role |
| --- | --- | --- |
| `mongo` | `mongo:7` | WiredTiger primary |
| `redis` | `redis:7-alpine` | Celery broker (`maxmemory` capped) |
| `worker` / `driver` | build from `../celery_probe` | Celery 5.4 + load generator |
| `minio` | MinIO (digest-pinned) | Local S3 API |
| `createbuckets` | `minio/mc` | One-shot bucket bootstrap |
| `clickhouse` | `clickhouse-server:24.8` | MergeTree with `storage_policy = 's3_main'` |

## Real S3 instead of MinIO

1. Copy `clickhouse/config.d/storage.aws.xml.example` → `storage.local.xml`
2. Edit endpoint / keys (do not commit keys; `storage.local.xml` is gitignored)
3. Run:

```bash
CLICKHOUSE_STORAGE_XML=./clickhouse/config.d/storage.local.xml \
SKIP_MINIO=1 ./perf.sh
```

## Relation to other harnesses

- `colocation_probe/` — same four services, ClickHouse on **local** disk; first
  small-n RSS observation. Use that for local-disk baseline; use **this** when
  the deployment under test stores ClickHouse on object storage.
- `celery_probe/` — Mongo I/O stall + Celery redelivery (blkio). Worker/driver
  image shared so pins stay aligned.
- Issue #18 / T10 (`clickhouse_probe`) — insert-frequency / parts ceiling; not
  this stack. Add later if you need parts-threshold numbers on S3 specifically.
