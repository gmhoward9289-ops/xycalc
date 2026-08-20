# Investigation 005 — Redis as a Celery broker at maxmemory

**Question:** If I use Redis as my Celery broker and it fills up, do I lose
queued jobs, or does the whole fleet seize up — and which of Celery's two
recommended `maxmemory-policy` settings is actually the safe one?

**Status:** harness built (`tools/bench/celery_probe/run_evict.sh`).
Documented conflict landed in the corpus; benchmark coefficients import via
`tools/import_evict_probe.py` after the sweep runs on swamplink.

**Expected confidence ceiling:** `documented` for vendor figures,
`practitioner` for celery#5716, `measured` for anything from evict_probe.

---

## Decomposition

- **FLOOR** — broker memory ceiling (`maxmemory`) and message size set how
  many tasks fit before a policy activates.
- **AMPLIFIER** — eviction under `allkeys-lru` can drop queue list keys;
  redelivery under `noeviction` OOM can block even read-side broker ops
  (celery#5716).
- **HEADROOM** — alert on `used_memory/maxmemory` before either failure mode
  engages; the operational answer may be "don't get near the ceiling."
- **CONSTRAINT** — Celery docs name only `noeviction` or `allkeys-lru`; they
  do not mention `volatile-lru` for broker queues.

---

## Do NOT do

- **Do not declare a policy winner before the sweep.** Report the conflict;
  falsify either side if the numbers support it.
- **Do not count executions on the broker under test.** Ground truth lives in
  the separate `bookkeeping` Redis service.
- **Do not treat `volatile-lru` with `task_ignore_result=True` as a policy
  measurement** — with no TTL keys it degenerates to `noeviction`.
- **Do not grade celery#5716 as `documented`.** It is a 2019 bug report against
  Celery 4.3.0, not vendor specification.
