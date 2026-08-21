# Investigation 014 — How much does Celery prefetch hide the backlog?

**Question (as asked):** My alert fires on Redis queue depth. If depth says 20
but the fleet actually has 200 tasks it hasn't finished, how late does that
alert go off — and does it get worse the higher I set prefetch?

**Reframe:** At fixed concurrency, does `LLEN` understate true outstanding
(`enqueued − completed`) by roughly `prefetch_multiplier × concurrency`, and
does drain time after overload grow with that reservation?

**Status:** Landed 2026-08-21 from swamplink sweep
`t6-prefetch-20260821-192932` (prefetch 1/2/4/8/16, 400/s × 30s, c=8).
Expected confidence: `measured` on this harness; do not generalize the
absolute depths off this box.

**ROADMAP:** T6 · **Issue:** #14 · **Plan:**
`docs/plans/issue-14-celery-prefetch-backlog.md`

---

## Decomposition

| Role | Term |
|---|---|
| **floor** | Broker-visible depth (`LLEN`) |
| **amplifier** | Prefetch reservation off the LIST (`prefetch × concurrency`) |
| **headroom** | Drain seconds after arrivals stop while the dependency stays slow |
| **constraint** | Fleet completion ceiling under stall (investigation 004) — prefetch does not raise it |

## Do NOT do

- Do not treat LLEN as outstanding work when prefetch > 1.
- Do not claim a universal understatement ratio from one host.
- Do not confuse "queue looks smaller" with "less work" — higher prefetch
  pulled more tasks off Redis while understatement rose.

---

## Short answer

**Yes, on this harness, once the workers are fed.** At prefetch 8 and 16
(concurrency 8), end-of-load understatement was **64** and **121** tasks —
matching `prefetch × 8` (64 / 128) within a few tasks. Visible
`queueDepthMax` fell as prefetch rose (**9883 → 8196**) while completion
during the arrival window rose (**63 → 118** tasks/s). Alerts on LLEN alone
fire on a shrinking fraction of real outstanding work as prefetch climbs.

At prefetch 1–4 the relationship is noisier (end understatement 71 / 23 / 40
vs expected 8 / 16 / 32); prefetch=1 also failed to finish drain within the
timeout (`completedTotal` 11862/12000). Treat the linear reservation story as
confirmed for the high-prefetch regime this issue worried about, not as a
perfect fit at every low setting.

---

## Results (400/s offered, 30s, c=8)

| Prefetch | expected reserved | understatement @ end load | queueDepthMax | done/s during load | drain s |
|---|---|---|---|---|---|
| 1 | 8 | 71 | 9883 | 63.3 | timed out |
| 2 | 16 | 23 | 8996 | 94.6 | timed out |
| 4 | 32 | 40 | 8912 | 97.0 | timed out |
| 8 | 64 | **64** | 8291 | 116.9 | **70.6** |
| 16 | 128 | **121** | 8196 | 118.2 | **72.1** |

Understatement @ end load = `(enqueued − completedDuringLoad) − queueDepthAtEnd`
(exact counters). Mid-window means in
`artifacts/understatement-recon.json` reconstruct `enqueuedSoFar` linearly —
this run's `sampleSeries` lacked `enqueuedSoFar` (swamplink image lag vs
current `drive.py`); local `drive.py` now emits understatement fields for the
next sweep.

Guards: 12k enqueues in ~30s at every leg (achieved rate ≈ 400/s);
`pagesReadIntoCache` ≫ 0; throughput stayed below the arrival rate (backlog
real, not a vacuous flat comparison).

---

## What this changes

- **`celery.worker-prefetch`** stays the documented reservation formula; T6
  is the first measurement that the invisible gap tracks that formula under
  overload at high prefetch.
- **Queue-depth alerts** need a prefetch-aware correction (or alert on
  outstanding = enqueued − completed / unacked) when multiplier ≫ 1.
- **Drain** once fed (~70s after 30s @ 400/s) did not grow further from
  prefetch 8→16; the expensive part was getting the fleet fed at all.

## Weakest inference

Prefetch=1 understatement and drain timeout are not fully explained here —
possible causes include worker starvation under open-loop overload, drain
timeout budget, or reconstruction noise. Do not invent a coefficient from
that row alone.

## Artifacts

`docs/investigations/014-celery-prefetch-backlog/artifacts/` —
`combined.jsonl`, per-prefetch logs, `understatement-recon.json`, `sweep.log`.

Observations: `data/observations/swamplink-celery-prefetch-2026-08-21.yaml`.
