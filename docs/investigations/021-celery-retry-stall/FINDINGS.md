# Investigation 021 — Celery retry storms under dependency stall (T8)

**Short answer:** On reef with `PROBE_STALL_MODE=pause` (docker pause of
Mongo), offered load during the stall drops to **0 completed/s** and
**stall_retries=0** for `none`, `immediate`, and `exponential`. Pause freezes
in-flight DB calls so Celery soft timeouts / retries do not fire. **Cannot
compare amplification** under this stall mode. Recovery: `none` and
`immediate` timed out at 180s; `exponential` quieted in **52.8s** once —
treat as suggestive, not a policy ranking.

**Confidence:** `benchmark` for the pause/zero-throughput fact; amplification
comparison **unvalidated** (n=0 usable retry storms).

---

## Question as asked

When a dependency stalls, how much extra load do retry policies create, and
how fast does the fleet recover after the stall lifts?

## What we measured

Harness: `tools/bench/celery_probe/run_stall_recover.sh` (wave12-r9).
900k docs (~2.4× oversub), rate 200/s, baseline 15s / stall 20s / recovery
timeout 180s, pause mode.

| policy | stall tps | stall retries | amplification | recovered | recovery s |
|---|---|---|---|---|---|
| none | 0.0 | 0 | 0.0 | no | 180 (timeout) |
| immediate | 0.0 | 0 | 0.0 | no | 180 (timeout) |
| exponential | 0.0 | 0 | 0.0 | **yes** | **52.8** |

Artifacts: this FINDINGS table (pause-mode non-result). Raw reef logs
were unpublished lab scratch; no amplification coefficient is published.

## Falsification outcome

Docker pause is the wrong instrument for retry-storm amplification: it
proves total dependency loss, not "slow DB + task timeouts." Guards correctly
refused immediate/exponential conclusions (`stall retries 0 < min 50`).

## Corpus

No amplification coefficients published from this run. FINDINGS record the
pause-mode limitation so the next arm uses **cgroup tighten** (or a
dependency that returns errors / honors `maxTimeMS` without freezing the
TCP session).

## What would validate next

`PROBE_STALL_MODE=cgroup` with writable host cgroup from the stall-driver
(`pid: host`), or a sidecar that blackholes mongo ports briefly without
pausing the process.
