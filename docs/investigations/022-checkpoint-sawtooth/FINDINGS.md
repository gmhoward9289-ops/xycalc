# Investigation 022 — Checkpoint sawtooth soak (T4)

**Short answer:** At concurrency **8** for **480s** on reef (MongoDB
**7.0.40**, 2.25× cache oversub, cgroup read throttle), checkpoint-conditioned
p99 latency ratio was **1.016** across **7** observed checkpoints.
Guards passed. Investigation 003's "flat within 8%" is **not** contradicted
by a periodic checkpoint stall on this harness.

**Confidence:** `benchmark`.

---

## Question as asked

Is the flat throughput of investigation 003 actually flat at 1-second
resolution around checkpoints?

## What we measured

| metric | value |
|---|---|
| ops/s | 109.0 |
| mean latency | 73.4 ms |
| p95 latency | 298.23 ms |
| checkpointsObserved | **7** |
| ckptP99RatioDuring/Outside | **1.016** |
| guards_ok | true |

120s smoke (r8) had only 3 checkpoints (refused) with a noisy ratio 2.287 —
insufficient cycles; superseded by this 480s run.

Artifact:
`docs/investigations/022-checkpoint-sawtooth/artifacts/reef-t4-480s-20260821.json`

## Falsification outcome

A visible periodic spike would have falsified "flat." Ratio ≈ 1.0 with
writes during checkpoint seconds does not.

## Corpus

- Parameter `storage.checkpoint_p99_latency_ratio`
- Observations:
  `data/observations/reef-checkpoint-timeseries-2026-08-21.yaml`

## Weakest inference

One concurrency, one throttle profile, Docker Desktop cgroup — not every
003 level. A heavier write mix or real NVMe might still show a tooth;
this run closes the "concealed by 25s means" question for the ticket-probe
read path as configured.
