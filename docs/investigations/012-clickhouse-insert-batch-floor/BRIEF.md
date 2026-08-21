# Investigation 012 — ClickHouse: how few inserts per second is too many?

**Question (as asked):** At what insert frequency does part count outrun
merges, so inserts are first delayed and then rejected?

**Roadmap / issue:** T10 / #18. Plan:
`docs/plans/issue-18-clickhouse-insert-batch-floor.md`.

**Status:** started 2026-08-21. Documented threshold coefficients landed from
`MergeTreeSettings.h` at tags v23.5.1 / v23.6.1 (and confirmed still 1000/3000
at v24.3.1-lts). Probe harness (`tools/bench/clickhouse_probe.*`) written;
full dual-image sweep not yet run in this environment (Docker layer extract
blocked on the cloud agent VM). Model `clickhouse.parts-insert-ceiling` is
`unvalidated (n=0)`. The frequency-floor model
(`clickhouse.insert-batch-floor`) waits on benchmark output.

**Expected confidence ceiling:** `code` for the threshold defaults (read from
implementation headers). `benchmark` for any measured inserts/sec crossover,
scoped to the container CPU/memory allocation — merge throughput is local, so
that floor must never be presented as a portable constant.

---

## Is this the right question?

Partly. The failure people hit is caused by insert **frequency**, not row
volume — switching from hourly batches to row-at-a-time streaming at the same
data rate is the classic incident. But the *portable* numbers ClickHouse
actually publishes are active-part **counts** (`parts_to_delay_insert` /
`parts_to_throw_insert`), not inserts per second. Inserts/sec at which those
counts are crossed depends on merge throughput (CPU, disk, part size), so a
single "too many inserts/sec" figure does not travel.

Answer the frequency question with a hardware-scoped benchmark **and** land
the count thresholds as version-pinned coefficients. Do not substitute one for
the other.

---

## Decomposition

| Role | Term |
|---|---|
| **FLOOR** | Active-part count at which inserts are delayed (`parts_to_delay_insert`) |
| **FLOOR** | Active-part count at which inserts are rejected (`parts_to_throw_insert`) |
| **AMPLIFIER** | Insert statements per unit time (batch size at fixed row volume) — how fast parts are created |
| **HEADROOM** | Background merge throughput vs insert rate; `max_delay_to_insert` sleep budget |
| **CONSTRAINT** | Defaults jump ~10× at 23.6; `async_insert` can coalesce and hide the mechanism; partition key spreads the count |

---

## Do NOT do

- **Do not trust the image tag for the defaults.** Query
  `system.merge_tree_settings` on the live container and assert the two images
  differ before any sweep step.
- **Do not use `PARTITION BY` on the probe table.** One partition ("all") only;
  abort if `count(DISTINCT partition)` ever exceeds 1.
- **Do not leave `async_insert` on.** Coalescing turns many client inserts into
  fewer parts and silently disables the mechanism under test.
- **Do not spawn `clickhouse-client` per insert.** Persistent pooled connections
  only — client fork overhead is not ClickHouse's ceiling.
- **Do not grade a measured inserts/sec floor as `documented`, or leave its
  `applies_to` as just a ClickHouse version.** Name the container CPU/memory
  (and host class) — the number does not generalize.
- **Do not credit "delay" from latency alone.** Require part-count crossing the
  documented threshold *and* server-side evidence (confirmed `system.events`
  counter or exception / text_log). Credit reject only from caught exception
  text.
- **Do not conclude from a flat part-count table.** That is the "REFUSING TO
  CONCLUDE" case (pressure never applied), not proof that batching is
  irrelevant.

---

## Claims to falsify

**Claim A — frequency, not volume, is the driver.** Fixed total rows; if
batch=1 and batch=100_000 produce similar peak active-part counts once merges
have run, the folklore is wrong as stated.

**Claim B — the 23.6 default change moves the crossover ~10×.** If delay/reject
engagement does not shift with the documented thresholds between a pre-23.6 and
a 23.6+ image (after confirming the settings query differs), the effective
ceiling is something else (merge throughput), and that is itself a finding.
