# Vuln history, aggregation fallback, and Celery concurrency

**Date:** 2026-08-23  
**Status:** approved 2026-08-23 (George: suggestions + dimensions×COLSCAN as a measurement invite; allow-list fallback)  

**Scope:** represent NVD/live + historical copies + v1/v2 aggregation failure → COLSCAN/Celery, and a first-class concurrency section that teaches load balancing without treating workers as MongoDB capacity  
**Out of scope:** inventing a `$lookup` / COLSCAN / “6–12 dimensions” latency multiplier; Simple-mode inputs; renaming `foreign_collections` back to snapshot search; writing a new stall-ceiling coefficient that rises with worker count

## Problem (as asked)

As the vulnerability database grows, backups and historical snapshots grow with it. Resource need feels exponential. Relationship queries are multi-dimension (often 6–12), segmented by name, against NVD and associated records. v1 and v2 aggregation paths often fail (16 MiB / pipeline memory / mixed, **or the query type is not on the aggregation allow list**), then work retries as scans. High concurrent Celery tasks do COLLSCAN. We need to **represent** that, and to get better at **balancing heavy load and managing concurrency** — a Celery section (or a section around these issues) is worth it even though more workers do not raise MongoDB’s stall ceiling.

## Reframe

Live CVE growth is **compound** (cited ~1.5–2.5× over three years). History is a **product** (copies × size of what you retain). Cache is another product (decompress, then ÷ eviction target). Aggregation failure is a **regime change**. Two distinct triggers, same Celery/COLSCAN aftermath:

1. **Limit** — 16 MiB / pipeline memory. Grows more likely as live + history + `$lookup` payloads grow.
2. **Allow list** — query type not permitted on v1/v2. Independent of database size. Multi-dimension / name-segmented queries are exactly the shapes that miss a finite list.

Both send traffic down COLLSCAN/relationship Celery. Ticket hold time L grows with scanned bytes; ceiling is N/L; Celery does not self-limit. That stack is a cliff, not a workers×throughput line.

There is **no vendor coefficient** in this corpus for “query type not in allow list.” Treat it as **application policy** (honest `none` until a cited product error/quote is landed). Do not encode a percent-of-types-allowed.

Investigation 004 already measured the teaching point: under a storage stall, raising Celery concurrency is **not** a cited way to lift the completion ceiling. Prefetch (014) hides broker-visible depth; it also does not raise that ceiling. The new section exists to make those knobs usable for **demand shaping**, not to sell a larger fleet as more mongod capacity.

## Locked decisions

| Decision | Choice |
|---|---|
| Planning query regime | **Fallback** (aggregation unavailable → scans). Always show an aggregation-ok band beside it when both can be evaluated. |
| Fallback reason | Optional `fallback_reason`: `limit` (16 MiB / stage memory) \| `allowlist` (query type not allowed) \| `mixed` (default). Same RAM/IOPS/Celery arithmetic; copy and “can we ever use the aggregation band?” differ. |
| History | First-class **document family**, not residual, not `foreign_collections_size`. 008’s “leave PIT out of Mongo” is reversed **only** for copies that queries actually load. |
| `foreign_collections_size` | Unchanged: cold / unusual load that is not the history family. |
| Dimensions × COLSCAN | **Invitation, not a coefficient.** Optional `scan_fanout` (user-typed; omit ≡ 1) multiplies **displayed in-flight scans only**. Encode neither 6–12 nor a COLSCAN latency factor in YAML. Invite others to measure ticket hold time L (and/or in-flight vs `totalTickets`) under their fallback workload and submit an observation (`xycalc ingest` / `data/observations/` with `applies_to`). A measured L band may land later; until then L is pasted. |
| Celery workers vs Mongo capacity | Copy and arithmetic must not imply more `-c` raises ops/s under stall. Workers, prefetch, and fan-out size **in-flight demand**. |
| Surface | Advanced **Concurrency and Celery** section on `mongodb.size-to-instance`, plus a dedicated scenario for the concurrency instrument. Simple unchanged (no layout jump). |
| New coefficients | None until measured. Reuse 004 drain/ceiling, 014 prefetch understatement, documented prefetch formula, PyMongo pool defaults, BSON 16 MiB, ticket Little’s law. |
| Ticket step | Only when the user supplies **measured** ticket hold time L (and ticket count). No default L for COLSCAN. |

## Honesty / validation (say aloud)

| Claim | Grade |
|---|---|
| BSON document ceiling 16 MiB | `documented` (`mongodb.bson-max-document-bytes`) |
| Aggregation stage RAM / `allowDiskUse` fail curve vs GB | `none` — constraint note only |
| Query type not on aggregation allow list | `none` — app policy, not a MongoDB sizing constant |
| `$lookup` / `$graphLookup` latency factor | `none` |
| 004 completion ceiling ~82 tasks/s, drain at 200/s | `measured` on that harness; not a fleet-size formula |
| Prefetch × concurrency reservation | `documented`; 014 measured understatement on one harness |
| COLSCAN hold time L vs point-lookup L | **not in corpus** — user must paste L or the ticket model stays skipped |
| NVD 3-year storage multiplier | `practitioner` (existing) |

## Decomposition

| Role | Term |
|---|---|
| **floor** | Live vuln family on disk (measured); history family on disk (measured copies × avg bytes, or measured total); devices; residual (flat) |
| **amplifier** | NVD compound or target count (vuln family only); snappy decompress; optional `scan_fanout` on **in-flight scans only** |
| **headroom** | Eviction ÷0.8; Celery prefetch reservation off Redis LLEN; gp3 microburst on IOPS |
| **constraint** | 16 MiB; allow-list miss (those types never take the pipeline); scan-heavy / fallback “one instance step or more IOPS above mode”; 004 completion ceiling does not rise with `-c`; 7.0+ tickets are a moving N; workers are demand |

## Surfaces

### 1. Advanced section on `mongodb.size-to-instance`

New `input_sections` (Advanced only; Simple mapping unchanged):

**History (queried copies)**

- `history_copy_count` + `history_avg_storage_bytes` (same pairing rule as devices), **or**
- `history_storage_size` (measured total)

These bytes join the storage projection **after** NVD growth (same as devices: do not inherit CVE compound unless the copies are more CVE docs). They **do** enter `mongodb.wt-cache` and gp3 `sum_inputs` when present — they are working set if fallback scans them.

**Query regime**

- `query_regime`: `aggregation` \| `fallback` (default `fallback` for this product path)
- Optional `fallback_reason`: `limit` \| `allowlist` \| `mixed` (default `mixed`)
- Constraint notes:
  - `limit`: BSON 16 MiB (cited); pipeline memory uncited
  - `allowlist`: this query type never uses v1/v2 — expanding the list (or mapping the type onto an allowed pipeline) is the product fix; xycalc still sizes **fallback** for traffic that remains off-list
  - fallback treats RAM/gp3 instance pick as a floor (existing `scan_heavy_workload` note, made selectable instead of always-on prose)
- If `fallback_reason` is `allowlist`, do **not** present the aggregation-ok SKU as “what you get if the DB stays small.” That band is only for types that are actually allowed.

**Concurrency and Celery** (the new section — worth it)

Inputs (all optional; omit = skip extra steps):

| Key | Unit | Role |
|---|---|---|
| `concurrency` | count | Celery prefork slots (`-c`). Demand, not Mongo capacity. |
| `worker_processes` | count | Distinct processes each with a pool (default 1 if concurrency set). |
| `scan_fanout` | count | Queries issued per task when known (1 or 6–12). Omit = 1. |
| `tickets` | tickets | `totalTickets` for the instance (no default; 7.0+ idle 4 ≠ busy 70). |
| `storage_latency_seconds` | seconds | Ticket **hold** time L, measured under the fallback workload. |

Copy that must appear in the section (not a tooltip-only footnote):

- More Celery workers increase **in-flight scans and broker occupancy**. They do not raise the 004 stall **completion ceiling**.
- Bound concurrency so in-flight work stays near what tickets × (1/L) can admit; extra `-c` grows Redis backlog (004) and hides work from LLEN (014).
- `maxPoolSize` default 100 × processes is connection **demand** toward mongod, distinct from WiredTiger tickets.

Gated scenario steps (`when_input: concurrency` / tickets+L):

1. `celery.worker-prefetch` — reserved tasks = multiplier × slots (documented).
2. Optional note/step: connection demand = `worker_processes × mongodb.pymongo-max-pool-size` (documented default; not a RAM coefficient).
3. `mongodb.ticket-throughput-ceiling` — only if both `tickets` and `storage_latency_seconds` supplied.
4. `celery.queue-amplification` — constraint/drain context when concurrency is set (004 numbers stay harness-scoped; do not rescale them by `-c`).

`see_also` on size-to-instance: `celery.queue-amplification`, `redis.celery-broker`, and the dedicated scenario below.

**In-flight scans (display, not a new coefficient):**  
`in_flight = concurrency × (scan_fanout or 1)`  
Compare to ticket pool when tickets are supplied. If in-flight ≫ tickets, say the queue grows; do not invent ops/s.

### 2. Dedicated scenario `mongodb.nvd-query-concurrency`

Label: **NVD / relationship query concurrency** (or equivalent).  
Job: the Celery + tickets + prefetch instrument without forcing a full instance pick. Same extra inputs as the section above; storage inputs optional if the user only wants demand vs ceiling.

Steps: prefetch → (optional) ticket ceiling → queue-amplification constraints → `see_also` size-to-instance for RAM/SKU.

This is the page for “balancing heavy loads.” Keep `redis.celery-broker` as the maxmemory/policy sibling (005); do not merge Redis eviction into this scenario.

## What more `-c` is allowed to change

| May change | Must not change |
|---|---|
| Prefetch reservation (documented product) | 004 completion-ceiling coefficient |
| Displayed in-flight scans | `mongodb.wt-cache` band (except via history/live bytes) |
| Broker backlog **risk** copy (qualitative + 004 citations) | Instance SKU via a workers×RAM formula |
| Connection demand (processes × pool) | A claimed ops/s lift |

## UI / layout

- Concurrency and history sections: **Advanced only**. Reserved subnav slot already exists; do not show/hide claim copy (no layout jump on Simple).
- Segmented `query_regime` control: same box model selected vs not (pressed = accent, not a taller CTA fill).
- Default `fallback` must not hide the aggregation band: two labeled result groups or a persistent second card with min-height so toggling regime does not shove chrome.

## Tests (when implementing)

- History bytes add to cache/gp3; they do not multiply by `nvd.cve-growth-3yr-multiplier`.
- Omitting concurrency skips prefetch/ticket/queue extra steps; size-to-instance RAM band matches today’s golden path.
- Supplying only concurrency does **not** change wt-cache lo/mode/hi.
- `scan_fanout` omitted ≡ 1; supplied 12 multiplies in-flight display only.
- Ticket step refused unless both tickets and L present.
- Export/JS `evaluate.js` agrees with `model.py` for the new optional steps.
- Copy assertion or snapshot: section states workers do not lift the stall ceiling.

## Follow-up investigation (not this spec’s ship)

**Open invitation (dimensions × COLSCAN):** measure ticket hold time L for a representative **fallback** query (COLLSCAN / relationship / post-16 MiB retry / allow-list miss) vs indexed aggregation on the same collection family, at two collection sizes, and optionally at `scan_fanout` 1 vs 6–12 if the task actually fans out. Submit as an observation with `applies_to` (Mongo version, harness, query shape). Corpus may then grow a **measured** L band. Until then L stays a pasted input.

Do **not** land a “failure rate vs GB” for v1/v2 without profiler samples.

`data/lab.yaml` `still_needs` on `mongodb.ticket-throughput-ceiling` (or a short callout on the concurrency scenario): a COLSCAN / relationship-query hold-time case, not another point-lookup stall.

## Where Fable fits (estate model ladder)

Fable is the top of the Claude ladder (hardest reasoning only). It is **not** the implementer for YAML/UI/tests.

Use Fable (Claude Code session, then switch back to Sonnet) only for an adversarial honesty pass: could `scan_fanout` or `in_flight` leak into `wt-cache` or rescale 004’s completion ceiling? Cursor Task slugs in this workspace do **not** include Fable — do not substitute Opus/Sonnet and call it Fable.

## Do NOT do

- Default concurrency to 8 on the instance sizer (that is the 004 harness, not this fleet).
- Feed dashboard client latency as L (ticket model already forbids that).
- Treat LLEN as outstanding work when prefetch > 1.
- Reintroduce “snapshot search” as a synonym for foreign collections.
- Encode 6–12 as a constant in YAML.
- Invent a “% of query types on the allow list” coefficient. Coverage is a product count the user can type later (`allowed_query_types` / `total_query_types`) only if they want a **display** split; it must not rescale cache bytes.

## Acceptance

- [ ] Advanced form can describe live + history + fallback + Celery demand without a fake exponent
- [ ] Dedicated concurrency scenario exists and cites 004/014/tickets/pool/16 MiB honestly
- [ ] More `-c` never raises the stall ops/s number
- [ ] Simple calculator inputs and first-paint chrome unchanged
- [ ] Gates: build, audit, pytest (including Node `evaluate.js` parity)

## Approval

Approved in-session 2026-08-23. Implementation plan: `docs/superpowers/plans/2026-08-23-vuln-history-celery-concurrency.md`. Changes to locked decisions belong here first.
