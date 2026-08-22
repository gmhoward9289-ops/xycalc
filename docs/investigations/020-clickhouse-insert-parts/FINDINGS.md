# Investigation 020 — ClickHouse insert-frequency / parts ceiling (T10)

**Short answer:** Dual-image settings trap confirmed:
**23.3** = delay **150** / throw **300**; **24.8** = **1000** / **3000**.
On reef Docker, **batch=10** on 23.3 reached **peakActiveParts=192**
(crossed delay); the same knobs on 24.8 peaked at **22** (no delay).
**batch=1** stayed ~18–19 on both — streaming single-row inserts alone did
not trip either threshold under default merges at 500k rows / 16 writers.

**Confidence:** `benchmark` for settings + peaks; `documented` for the 23.6
threshold change.

---

## Question as asked

At what insert frequency does part count outrun merges and delay/reject?

## What we measured

Harness: `tools/bench/clickhouse_probe.{sh,py}` on reef (wave12-r9).

| Image | delay / throw | batch=1 peak | batch=10 peak | crossed delay? |
|---|---|---|---|---|
| `23.3` | **150 / 300** | 19 | **192** | **yes (batch=10)** |
| `24.8` | **1000 / 3000** | 18 | 22 | no |

`PROBE_ROWS=500000`, `PROBE_WRITERS=16`, `async_insert=0`, single partition.
Artifact: `docs/investigations/020-clickhouse-insert-parts/artifacts/reef-ch-dual-20260821.json`.

Guard still refuses a *floor coefficient* because batch=1 never crossed
(the folklore streaming case). The version trap + batch=10 onset on 23.3
are the publishable results.

## Falsification outcome

Insert **frequency** (batch size at fixed row budget) governs peak parts
enough to cross 23.3's delay on batch=10 while missing it on batch=1.
24.8's tenfold higher defaults absorb the same workload.

## Corpus

- Live settings observations use main's
  `clickhouse.active_parts_delay_threshold` /
  `clickhouse.active_parts_throw_threshold` (same 150/300 vs 1000/3000
  already cited as `code` on investigation 012). Peak parts use
  `clickhouse.peak_active_parts`.
- Observations: `data/observations/reef-clickhouse-parts-2026-08-21.yaml`
- **No portable inserts/sec floor.** Issue #18 stays open.
  `clickhouse.parts-insert-ceiling` remains unvalidated (n=0) vs production.

## Weakest inference

2 CPU / 2 GB Docker CH merge behavior ≠ production cluster. Peak 192 on
batch=10 may not generalize; the **relative** 23.3 vs 24.8 story does.
