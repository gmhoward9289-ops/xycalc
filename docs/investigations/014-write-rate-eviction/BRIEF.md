# Investigation 014 — Write rate vs `eviction_dirty_trigger` (T3)

**Question as asked:** At what sustained write rate does WiredTiger conscript
application threads for eviction, and is that really at 20% dirty cache like
the docs say?

**Status:** complete. Findings in `FINDINGS.md`. Documented dirty_trigger 20%
unchanged; no app-thread eviction onset under the cgroup-throttle harness.

**Expected confidence ceiling:** `benchmark` for the onset observation;
`documented` for checkpoint interval. Mechanism check on a synthetic cgroup
write throttle — not an operational "how close am I" answer.

---

## Why this subject

Investigation 001 cites `eviction_dirty_trigger` (20%) as a write-path
constraint with **zero** observations. Bulk loads are said to hit it first;
nobody here has measured the onset.

## Decomposition

| Role | Term |
|---|---|
| **constraint** | Dirty budget ≈ 20% of configured cache before app-thread eviction |
| **amplifier** | Write rate relative to device write throughput |
| **headroom** | Checkpoint / background eviction grace (~60s `syncPeriodSecs`) |
| **floor** | Device sustained write throughput (deployment input) |

## Do NOT do

- Publish a dirty-trigger onset when overall occupancy ≥80% at the same time
  (wrong trigger).
- Treat a vacuous run (throttle never engaged; dirty% stuck under 1%) as
  confirmation of the documented 20%.
- Edit the `documented` 20% coefficient if 7.0 onset disagrees — record
  disagreement in notes / FINDINGS.
- Claim the result generalizes to EBS/NVMe without saying it was cgroup-
  throttled.

## Prior smokes (2026-08-21, reef)

With `PROBE_WRITE_BPS=32MiB/s`, journal off, 45s levels: dirty peak ~3–4%,
`evictedByAppDelta=0`, attribution `unclear`. Achieved insert rate (~6 MiB/s)
stayed **below** the throttle — device never bound, dirty never accumulated.
Need a soak with write throttle **below** achievable insert rate and
≥3× checkpoint interval per level.
