---
name: xy-observe
description: Import real measurements into the xycalc corpus and re-score model validation. Use when George pastes or points at metrics output — db.stats(), serverStatus, mongostat, redis-cli INFO, CloudWatch JSON, iostat, a Grafana CSV or Coralogix export — or asks to check a model against real data, or says a model is unvalidated and he has numbers.
---

# Import an observation

This is the step that turns `unvalidated (n=0)` into a number. It is the whole
difference between this corpus and a well-cited blog post.

Work in `~/GitHub/xycalc`. Read `docs/telemetry/README.md` for the contract.

## MongoDB — there is a tool

```bash
mongosh --quiet --eval '
  print(JSON.stringify({
    stats: db.stats(),
    cache: db.serverStatus().wiredTiger.cache,
    version: db.version(),
    at: new Date()
  }))' > dump.json

.venv/bin/python tools/import_mongodb.py dump.json \
    --machine-class r6i.4xlarge --workload "read-heavy, steady state"
.venv/bin/xycalc audit
```

Writes to `local/` — gitignored, merged over `data/` at build time. Use
`--publish` only for a machine whose details are fine on the internet.

## Anything else — write the YAML

```yaml
observations:
  - slug: <machine>-<date>-<what>
    system: mongodb
    parameter: cache.size_bytes      # must exist in data/parameters.yaml
    value: 132000000000
    unit: bytes
    workload: read-heavy, 400 rps
    machine_class: r6i.4xlarge
    system_version: '7.0.14'
    observed_on: '2026-07-15'
    source: obs-<slug>               # a `measured` source you also add
```

`workload`, `machine_class` and `system_version` are not decoration. Without
them it is a number from an unknown machine doing unknown work, and it
validates nothing.

## Validating: compare like with like

**The mistake to avoid, because it has already been made here once.** A
validation case that compares a measurement against the wrong quantity reports
a confident error percentage that means nothing.

`mongodb.wt-cache` outputs the cache size to **configure**. `serverStatus`
reports the bytes currently **resident**. Those differ by exactly the eviction
headroom divisor, so validating one against the other scores a perfectly
correct model at 25% error, every time.

Use `at_term` to compare against the running total after a named term:

```yaml
validation:
  - model: mongodb.wt-cache
    case: <machine>-<date>
    observation: <observation slug>
    inputs: {storage_size: 500000000000, index_size: 40000000000}
    at_term: indexes        # predicted cache CONTENTS, not configured size
    actual: 1290000000000   # bytes currently in the cache
```

Before writing a case, ask: **what quantity did the instrument actually
measure, and which term of the model predicts that same quantity?** If none
does, the case does not belong — say so rather than forcing it.

`xycalc why <model>` lists the terms and their keys.

Predictions are never stored. The YAML holds inputs and a measured actual; the
build recomputes the prediction every time. A stored prediction would leave the
recorded error untouched when the model changed, and the corpus would report an
accuracy it no longer had.

## Read the result honestly

```bash
.venv/bin/xycalc audit
```

- **Within band, low error** — the model survived contact. Say which terms it
  actually tested; a single case usually tests two or three, not all of them.
- **Outside the band** — a finding, not a failure. Which term is wrong? Was the
  measurement taken under conditions the model assumes? Write it up rather than
  widening a band to make it fit.
- **A saturated cache** — if the cache was nearly full, resident bytes measure
  the *cache*, not the database, and the case tells you how much fits rather
  than how much is needed. The importer warns about this. Read the error as a
  floor.

One case is `n=1`. Do not describe a model as validated on the strength of one
observation from one machine; describe it as having survived one test, and say
which one.
