# Investigation 019 — Celery prefetch hides backlog (T6)

**Short answer:** At a fixed arrival rate **above** the completion ceiling
(200/s offered, ~100–120/s completed on reef), broker queue depth
**understates** outstanding work, and the gap grows with
`worker_prefetch_multiplier`: peak understatement **9 → 62 → 80** at
prefetch **1 / 4 / 8**.

**Confidence:** `benchmark` (reef Docker Celery 5.4.0 / Redis 7.4.10 /
MongoDB 7.0.40). Drain timed out in all three arms — shed-time comparison
is incomplete; the understatement claim does not need drain.

---

## Question as asked

How far does Redis list depth understate outstanding work when workers
prefetch, and does that matter for alerts?

## What we measured

Harness: `tools/bench/celery_probe/sweep_prefetch.sh` on reef (wave12-r7).

| prefetch | underMax | underMean | queueDepthMax | achieved | completed/s |
|---|---|---|---|---|---|
| 1 | **9** | 8.73 | 1915 | 200.0 | 120.4 |
| 4 | **62** | 48.02 | 2398 | 200.0 | 99.0 |
| 8 | **80** | 72.58 | 2157 | 200.0 | 109.1 |

Conditions: `PROBE_DOCS=900000` (~2.43× WT oversub), `PROBE_RATES=200`,
`PROBE_SECONDS=25`, acks_late=true, visibilityTimeout=30.

Artifacts: `tmp-reef-status/r7/t6/prefetch-{1,4,8}.log`.

Earlier rate=50/s smoke was vacuous (underMax=0, queue stayed empty).

## Falsification outcome

Plan expected: if depth tracked outstanding regardless of prefetch, concern
is unfounded. **Falsified** — understatement rises with prefetch under the
same offered load.

## Corpus

- Parameter `queue.depth_understatement_max`
- Observations: `data/observations/reef-celery-prefetch-2026-08-21.yaml`
- Source: `data/sources/reef-celery-prefetch-2026-08-21.yaml`

## Weakest inference

25s windows with drain timeout: do not treat as steady-state shed curves.
Concurrency is the harness default (not swept); absolute understatement
magnitudes are configuration-bound, the **ordering** with prefetch is the
result.

## What would validate next

Longer rate windows + explicit drain timeout large enough to quiet; optional
prefetch=16; pair with T8 recovery times once stall/recover lands.
