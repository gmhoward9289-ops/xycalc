# Telemetry contracts

What we want to measure, why, and whether we can currently get it.

These files are written **before** any code exists to fetch the series they
describe. That is deliberate. Naming the measurement you need is the part that
requires thinking about the problem; acquiring it is engineering, and it is
easier once the target is written down. Several entries here are marked *not
obtainable* — those are findings, not gaps in the document.

## Status vocabulary

| Status | Meaning |
|---|---|
| `obtainable` | reachable from a laptop with a running instance |
| `work only` | exists in Coralogix/Grafana at work; not reachable from here |
| `not obtainable` | the data source does not produce it at the fidelity needed |
| `manufacturable` | not available, but reproducible with a local benchmark |

`not obtainable` is the interesting one. If the metric that would settle a
question cannot be had from the obvious place, that is worth knowing before
building a dashboard around the obvious place.

## Vital metrics and alerts

Living recommendations — **Product core** (instance sizing), **Simple** /
**Advanced** / **Evidence** (ops + catalogs) — live in
[`recommendations.md`](recommendations.md). Per-system series contracts remain
in `mongodb.md`, `ebs.md`, and `redis.md`.

## The import contract

Whatever can be exported becomes `observation` rows. One row is one number, its
unit, and enough context to know whether it applies to your situation.

```yaml
observations:
  - slug: mongo-prod-cache-resident-2026-07
    system: mongodb
    parameter: cache.size_bytes
    value: 132000000000
    unit: bytes
    workload: read-heavy, 400 rps
    machine_class: r6i.4xlarge
    system_version: '7.0.14'
    observed_on: '2026-07-15'
    source: obs-mongo-prod-2026-07
    notes: cache had been warm 6 days; steady state
```

`workload`, `machine_class` and `system_version` are what make an observation
reusable. Without them it is a number from an unknown machine doing unknown
work, which validates nothing.

Supported shapes, in rough order of how much work they save:

| Format | Command |
|---|---|
| MongoDB serverStatus | `db.serverStatus()` → JSON |
| MongoDB dbStats | `db.stats()` → JSON |
| mongostat | `mongostat --json` |
| Redis | `redis-cli INFO` |
| ClickHouse | `SELECT * FROM system.asynchronous_metrics` |
| CloudWatch | `aws cloudwatch get-metric-statistics` → JSON |
| Linux block I/O | `iostat -x 1` |
| Grafana | CSV export from Explore |
| Coralogix | DataPrime query results as JSON |

## Where observations live

- **`data/observations/`** — published. Local benchmarks, vendor-published
  measurements, anything that can be shared.
- **`local/observations/`** — gitignored, merged at build time, never
  distributed. Production telemetry belongs here.

Rows record which they came from, and the build prints the count. A model
validated only by `local/` rows shows as validated to whoever holds those rows
and unvalidated to everyone else, which is the honest reading in both
directions.

## A note on work

At work this runs in the company's own Claude environment against the company's
own data, so `local/` holding real production telemetry is normal and fine
there. The published repo carries none of it, and there is no path by which it
could: `local/` is gitignored, and the pre-commit hook scans staged content for
identifiers regardless.
