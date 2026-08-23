# Vuln history, fallback, and Celery concurrency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Advanced sizing describe live NVD + queried history + aggregation fallback (limit / allow-list / mixed) + Celery demand, without treating workers or `scan_fanout` as MongoDB capacity, and without minting a dimensions×COLSCAN coefficient.

**Architecture:** History is a third family on `mongodb.storage-from-doc-families` (same `add_product_of_inputs` shape as devices, or a measured total). Query regime and Celery fields are scenario extra inputs. Gated scenario steps reuse existing celery/ticket models. Display-only `in_flight = concurrency × (scan_fanout or 1)`. Ticket ceiling runs only when both `tickets` and `storage_latency_seconds` are present (`when_all_inputs`). Dedicated scenario `mongodb.nvd-query-concurrency` is the load-balancing page. Simple mode mapping stays as it is.

**Tech Stack:** YAML corpus (`data/models/storage.yaml`, `data/scenarios.yaml`, `data/lab.yaml`), `src/xycalc/model.py` + `static/evaluate.js` parity, pytest + Node export tests, Advanced UI in `static/app.js` / calculator HTML.

**Spec:** `docs/superpowers/specs/2026-08-23-vuln-history-celery-concurrency-design.md`

## Global Constraints

- No new numeric coefficients until a submitted observation with `applies_to`. Do not encode 6–12 in YAML.
- `scan_fanout` must not enter `mongodb.wt-cache` or rescale `celery.queue-completion-ceiling-baseline`.
- More `-c` must not change host RAM lo/mode/hi or the 004 completion-ceiling figure.
- History bytes must not inherit `nvd.cve-growth-3yr-multiplier`.
- Ticket step skipped unless **both** `tickets` and `storage_latency_seconds` are supplied (no default L, no default 128 tickets).
- Do not default `concurrency` to 8 on `mongodb.size-to-instance`.
- Simple inputs and first-paint chrome unchanged (no layout jump).
- `foreign_collections_size` stays cold/unusual; history is a separate family.
- Python 3.12, `.venv\Scripts\python.exe` / pytest on COOPER; PowerShell 5.1 (no `&&`).
- Commits only when George asks; do not `git commit` from plan steps unless he says so.

## File map

| File | Responsibility |
| --- | --- |
| `data/models/storage.yaml` | History inputs + terms on `mongodb.storage-from-doc-families` |
| `data/scenarios.yaml` | Sections, extra_inputs, gated steps, new scenario, see_also |
| `src/xycalc/model.py` | `when_all_inputs` on scenario steps; `in_flight_scans()`; optional summary concurrency block |
| `src/xycalc/static/evaluate.js` | Same `when_all_inputs` + in-flight helper |
| `src/xycalc/static/app.js` | Advanced copy, regime control, invite-to-measure blurb, in-flight display |
| `tests/test_model.py` | History family arithmetic |
| `tests/test_scenario.py` | Golden path unchanged; gated celery/ticket; fanout display isolation |
| `tests/test_export.py` | JS/Python parity for new steps |
| `data/lab.yaml` | Invitation: COLSCAN/relationship L measurement |
| `docs/telemetry/mongodb.md` | One paragraph: no `$lookup` multiplier; measure L under fallback |

---

### Task 1: History family on storage-from-doc-families

**Files:**
- Modify: `data/models/storage.yaml`
- Modify: `tests/test_model.py` (`TestDocFamiliesStorageModel`)
- Modify: `data/scenarios.yaml` (`mongodb.size-to-instance` `input_sections` + gp3 `sum_inputs`)

**Interfaces:**
- Consumes: existing `add_product_of_inputs`, `when_input` / `unless_input`
- Produces: inputs `history_copy_count`, `history_avg_storage_bytes`, `history_storage_size`; terms `history_copies`, `history_measured_total`

- [ ] **Step 1: Write the failing tests**

In `tests/test_model.py`, after `test_device_count_without_avg_is_an_error`:

```python
    def test_history_copies_add_after_vuln_growth(self, model):
        r = model.evaluate(
            {
                "baseline_vuln_count": 100_000,
                "baseline_storage_size": "500GB",
                "target_vuln_count": 100_000,
                "history_copy_count": 3,
                "history_avg_storage_bytes": "80GB",
            }
        )
        assert r.mode == pytest.approx(parse_bytes("500GB") + 3 * parse_bytes("80GB"))

    def test_history_does_not_inherit_nvd_compound_growth(self, model):
        r = model.evaluate(
            {
                "baseline_vuln_count": 100_000,
                "baseline_storage_size": "500GB",
                "history_copy_count": 2,
                "history_avg_storage_bytes": "10GB",
            }
        )
        # vuln mode 2.0 × 500GB + 20GB history
        assert r.mode == pytest.approx(1000 * 1000**3 + 20 * 1000**3)

    def test_history_measured_total_skips_copy_product(self, model):
        r = model.evaluate(
            {
                "baseline_vuln_count": 100_000,
                "baseline_storage_size": "500GB",
                "target_vuln_count": 100_000,
                "history_storage_size": "240GB",
            }
        )
        assert r.mode == pytest.approx(parse_bytes("740GB"))

    def test_history_copy_count_without_avg_is_an_error(self, model):
        with pytest.raises(ModelError, match="together"):
            model.evaluate(
                {
                    "baseline_vuln_count": 100_000,
                    "baseline_storage_size": "500GB",
                    "target_vuln_count": 100_000,
                    "history_copy_count": 3,
                }
            )
```

- [ ] **Step 2: Run tests to verify they fail**

```powershell
C:\Users\gmhow\dev\xycalc\.venv\Scripts\python.exe -m pytest tests/test_model.py::TestDocFamiliesStorageModel -q
```

Expected: new tests fail (unknown input `history_*`).

- [ ] **Step 3: Add inputs and terms in `data/models/storage.yaml`**

After `residual_storage_size`, add:

```yaml
      - key: history_copy_count
        label: Historical / backup copies that queries load
        unit: count
        required: false
        help: >-
          Optional. Number of retained copies you actually scan (not EBS
          snapshots sitting idle). Supply with history_avg_storage_bytes.
      - key: history_avg_storage_bytes
        label: Avg on-disk bytes per queried history copy
        unit: bytes
        required: false
        help: >-
          Optional. Measured storageSize of one copy. Required whenever
          history_copy_count is set. Do not use this field for cold
          foreign_collections_size.
      - key: history_storage_size
        label: Measured history-family storageSize (alternative to copies × avg)
        unit: bytes
        required: false
        help: >-
          Optional. Total compressed bytes of queried history. If set, the
          copy-count product is skipped so bytes are not double-counted.
```

After the `devices` term, before `residual`:

```yaml
      - key: history_copies
        label: History copies (count × avg on-disk bytes)
        role: floor
        apply: add_product_of_inputs
        input_key: history_copy_count
        input_key_b: history_avg_storage_bytes
        optional: true
        when_input: history_copy_count
        unless_input: history_storage_size
        rationale: >-
          Queried PIT / backup copies. Added after vuln growth so they do not
          inherit the NVD compound band. Not foreign_collections (those stay cold).

      - key: history_measured_total
        label: History family measured total
        role: floor
        apply: input
        input_key: history_storage_size
        optional: true
        when_input: history_storage_size
        rationale: >-
          Same family as history_copies when the caller measured the total
          instead of count × average.
```

Notes in the model header: residual still does not grow; history is working set if scanned.

On `mongodb.size-to-instance`, add keys to the MongoDB footprint section: `history_copy_count`, `history_avg_storage_bytes`, `history_storage_size`. Add those same keys (except the pair’s unused half is ok) to gp3 `sum_inputs` alongside `index_size` and `foreign_collections_size`.

- [ ] **Step 4: Rebuild and re-run tests**

```powershell
C:\Users\gmhow\dev\xycalc\.venv\Scripts\python.exe -m xycalc.build
C:\Users\gmhow\dev\xycalc\.venv\Scripts\python.exe -m pytest tests/test_model.py::TestDocFamiliesStorageModel tests/test_scenario.py::TestChainEvaluate::test_devices_and_residual_raise_projected_volume -q
```

Expected: PASS. Add a scenario test that history raises gp3 volume analogously to devices.

---

### Task 2: `when_all_inputs` and in-flight helper (no capacity leak)

**Files:**
- Modify: `src/xycalc/model.py` (`chain_evaluate` skip logic ~1352 and ~1361)
- Modify: `src/xycalc/static/evaluate.js` (same two loops)
- Modify: `src/xycalc/build.py` if scenario steps are schema-validated
- Create tests in `tests/test_scenario.py`

**Interfaces:**
- Produces: `when_all_inputs: list[str] | None` on scenario steps; `in_flight_scans(concurrency, scan_fanout) -> float | None`
- Consumes: existing `when_input` (keep working)

- [ ] **Step 1: Failing tests**

```python
def in_flight_scans(concurrency, scan_fanout=None):
    ...

def test_in_flight_omitted_fanout_is_concurrency():
    from xycalc.model import in_flight_scans
    assert in_flight_scans(8, None) == 8
    assert in_flight_scans(8, 12) == 96
    assert in_flight_scans(None, 12) is None

def test_concurrency_alone_does_not_change_host_ram(conn, scenario):
    base = chain_evaluate(conn, scenario, INSTANCE_INPUTS)
    with_c = chain_evaluate(conn, scenario, {**INSTANCE_INPUTS, "concurrency": "32"})
    ram = lambda steps: next(s for s in steps if s.slug == "mongodb.host-ram").result.mode
    assert ram(base) == pytest.approx(ram(with_c))
```

The second test fails until Task 3 adds optional concurrency (today unknown input may already error — if extra_inputs are not declared, `chain_evaluate` may reject unknown keys). Check `chain_evaluate` unknown-input policy; if unknown keys error, this test waits until Task 3 declares `concurrency` as extra_input. Write it in Task 3 if so.

- [ ] **Step 2: Implement skip**

In both Python and JS, treat a step as skipped when `when_all_inputs` is set and any named key is missing/empty. Existing `when_input` remains OR-of-one.

```python
def in_flight_scans(
    concurrency: float | None, scan_fanout: float | None
) -> float | None:
    if concurrency is None:
        return None
    fan = 1.0 if scan_fanout is None else float(scan_fanout)
    return float(concurrency) * fan
```

Mirror in `evaluate.js` as `XY.inFlightScans`.

- [ ] **Step 3: Audit / build**

```powershell
C:\Users\gmhow\dev\xycalc\.venv\Scripts\python.exe -m xycalc.build
C:\Users\gmhow\dev\xycalc\.venv\Scripts\python.exe -m xycalc.audit
```

If audit rejects unknown scenario keys, add them in Task 3 in the same change set.

---

### Task 3: Wire Celery / tickets on size-to-instance + dedicated scenario

**Files:**
- Modify: `data/scenarios.yaml`
- Modify: `tests/test_scenario.py`
- Modify: `src/xycalc/model.py` `build_instance_sizing_summary` — optional `concurrency` block only

**Interfaces:**
- Extra inputs: `query_regime` (string), `fallback_reason`, `concurrency`, `worker_processes`, `scan_fanout`, `tickets`, `storage_latency_seconds`
- Steps on `mongodb.size-to-instance` **after** `ebs.iops-to-provision` (summary is slug-keyed; RAM/SKU unchanged):

```yaml
      - kind: model
        model: celery.worker-prefetch
        when_input: concurrency
      - kind: model
        model: mongodb.ticket-throughput-ceiling
        when_all_inputs:
          - tickets
          - storage_latency_seconds
      - kind: model
        model: celery.queue-amplification
        when_input: concurrency
```

`celery.queue-amplification` has no `concurrency` input today — it still evaluates 004 drain as a **constraint model** (output drain seconds). That is intentional: do not feed `-c` into it. Prefetch model **does** take concurrency.

New scenario:

```yaml
  - slug: mongodb.nvd-query-concurrency
    label: NVD / relationship query concurrency
    summary: >-
      Celery in-flight demand vs MongoDB tickets and the 004 stall ceiling.
      More workers are demand, not mongod capacity. scan_fanout is a typed
      count (invite a measured L); it is not a COLSCAN coefficient.
    input_sections:
      - title: Query regime
        keys: [query_regime, fallback_reason, scan_fanout]
      - title: Celery demand
        keys: [concurrency, worker_processes]
      - title: Tickets (optional, both or neither)
        keys: [tickets, storage_latency_seconds]
    extra_inputs:
      # declare units/help; query_regime is not evaluated by a model
    steps:
      - kind: model
        model: celery.worker-prefetch
        when_input: concurrency
      - kind: model
        model: mongodb.ticket-throughput-ceiling
        when_all_inputs: [tickets, storage_latency_seconds]
      - kind: model
        model: celery.queue-amplification
        when_input: concurrency
    see_also:
      - scenario: mongodb.size-to-instance
      - scenario: celery.queue-amplification
      - scenario: redis.celery-broker
```

`see_also` on size-to-instance: the three scenarios above.

Help text on `scan_fanout` must say: omit = 1 query per task; 6–12 only if you measured fan-out; **submit an observation** rather than treating this as a cited amplifier.

- [ ] **Tests**

```python
def test_size_to_instance_step_list_without_concurrency_unchanged(self, conn, scenario):
    steps = chain_evaluate(conn, scenario, INSTANCE_INPUTS)
    assert [s.slug for s in steps] == [
        "mongodb.storage-from-doc-families",
        "mongodb.wt-cache",
        "mongodb.host-ram",
        "aws-ec2.instance-select",
        "aws-ec2.instance-select",
        "azure-vm.instance-select",
        "ebs.gp3-spec",
        "ebs.iops-to-provision",
    ]

def test_ticket_step_skipped_without_L(self, conn, scenario):
    steps = chain_evaluate(
        conn, scenario, {**INSTANCE_INPUTS, "concurrency": "16", "tickets": "64"}
    )
    slugs = [s.slug for s in steps]
    assert "celery.worker-prefetch" in slugs
    assert "mongodb.ticket-throughput-ceiling" not in slugs

def test_scan_fanout_does_not_change_wt_cache(self, conn, scenario):
    a = chain_evaluate(conn, scenario, INSTANCE_INPUTS)
    b = chain_evaluate(
        conn, scenario, {**INSTANCE_INPUTS, "concurrency": "8", "scan_fanout": "12"}
    )
    cache = lambda st: next(s for s in st if s.slug == "mongodb.wt-cache").result.mode
    assert cache(a) == pytest.approx(cache(b))
```

Fix `test_size_to_instance_includes_models_and_lookups` if it asserts exact tuple list — keep it the no-concurrency path.

- [ ] **Summary block** (optional keys only when concurrency present):

```python
summary["concurrency"] = {
    "slots": concurrency,
    "fanout": scan_fanout or 1,
    "in_flight": in_flight_scans(concurrency, scan_fanout),
}
```

Must not overwrite `ram` / `cpu`.

---

### Task 4: Advanced UI — regime, Celery section, invite copy, no Simple jump

**Files:**
- Modify: `src/xycalc/static/app.js` (scenario form renderer, summary panel)
- Modify: `src/xycalc/static/calculator.html` only if Advanced needs a reserved slot; **do not** hide `.claim` in Simple
- Modify: `tests/check_simple_first_paint.js` if it snapshots section titles — must still pass
- Modify: `tests/test_export.py` if it pins scenario step counts

**Copy that must appear** in the Concurrency section (visible, not tooltip-only):

- More Celery workers increase in-flight scans and broker occupancy. They do not raise the stall completion ceiling (investigation 004).
- `scan_fanout` is how many queries one task issues. It is not a COLSCAN latency multiplier. Measure ticket hold time L under your fallback workload and submit an observation.
- Allow-list misses never use v1/v2 aggregation; size fallback for that traffic.

`query_regime` segmented control: same padding/border/line-height selected vs not (no-layout-jump). Default `fallback`. When `fallback_reason` is `allowlist`, do not caption the aggregation-ok SKU as “if the DB stays small.”

In-flight line: `in_flight` vs tickets when both known: “in-flight scans exceed the ticket pool — arrivals queue; this is not extra ops/s.”

- [ ] **Verify Simple first paint**

```powershell
node tests/check_simple_first_paint.js
```

Expected: PASS, same chrome height contract as today.

---

### Task 5: Invitation surfaces (lab + telemetry)

**Files:**
- Modify: `data/lab.yaml` — `mongodb.ticket-throughput-ceiling.still_needs` add: COLSCAN / relationship-query hold time L vs point-lookup, optional `scan_fanout` 1 vs N, submitted as observation (not a YAML 6–12).
- Modify: `docs/telemetry/mongodb.md` Foreign fields section: invite measured `$lookup`/COLLSCAN stage latency and ticket hold time; still no vendor multiplier.

Keep under 400 characters per lab field.

---

### Task 6: Gates

```powershell
C:\Users\gmhow\dev\xycalc\.venv\Scripts\python.exe -m xycalc.build
C:\Users\gmhow\dev\xycalc\.venv\Scripts\python.exe -m xycalc.audit
C:\Users\gmhow\dev\xycalc\.venv\Scripts\python.exe -m pytest -q
```

Expected: all green. Node `evaluate.js` tests inside pytest must run, not skip.

---

## Spec coverage

| Spec item | Task |
| --- | --- |
| History family | 1 |
| Fallback / allow-list copy | 4 |
| Celery section + workers ≠ capacity | 3, 4 |
| Dedicated scenario | 3 |
| scan_fanout invite, not coefficient | 2, 3, 4, 5 |
| Ticket both-or-neither | 2, 3 |
| Simple unchanged | 4, 6 |
| No new coefficients | all |

## Fable (not this plan’s implementer)

Adversarial honesty pass only: confirm `scan_fanout` / `in_flight` cannot change `wt-cache` or 004 ceiling. Cursor Task model slugs here do not include Fable — run that pass in Claude Code on Fable 5, then switch back to Sonnet. Do not implement YAML on Fable.
