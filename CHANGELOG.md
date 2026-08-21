# Changelog

All notable changes to the **xycalc tool** (not the corpus) are recorded here.
Corpus identity is `corpus_digest` on the exported page. See `docs/VERSIONING.md`.

## 0.4.0

- Simple mode first paint shows the same weakest-chain validation banner as
  Advanced (label + `text`, including n= / in-band / MAE) and a hard
  “not a buy size / uncited path — open Advanced for sources” line with a
  control that opens Advanced Scenario with the math expanded. Host RAM is
  withheld if that banner cannot be rendered. Custom-sizing picks name the
  catalog SKU band they exceeded.
- Validation grade `reasonable` (calculator **Validated**) requires at least
  one observation inside the predicted band. n and MAE alone used to promote
  a 0-in-band model.
- Occupancy-tab total-cache tick labels: 90 sits below the bar, trigger 95
  above, so they do not overlap at desktop width.
- `xycalc ingest` accepts MongoDB `db.stats()` / `serverStatus` JSON (file or
  stdin), prints the fields it mapped versus ignored, and runs `mongodb.wt-cache`
  when `storage_size` is in the extracted inputs (including `storageSize: 0`).
  A paste is stats-shaped if it has any of `storageSize` / `dataSize` /
  `indexSize` — not only the pair `storageSize`+`dataSize`. `--emit-observation`
  writes candidate YAML; destinations under `data/` are refused unless
  `--force-corpus`. Default ingest writes nothing. MCP `ingest_dbstats` never
  writes files. Provenance that cannot be derived is `TODO` (including
  `source_type`); tag/slug do not stamp today's date when `observed_on` is
  unknown. An ingested paste is a candidate, not a cited or validated fact.
- MCP tool `import_metrics` plus `tools/import_metrics_export.py` — Grafana
  Explore CSV, Prometheus query JSON / OpenMetrics, and Coralogix metrics JSON
  → `local/` observations (history for validation)
- In-repo skill `skills/xycalc/SKILL.md` and `deploy/grafana/` source pack
  (estate boards live in the monitoring repo; this tree is the recipe source)
- `python -m xycalc` runs the stdio MCP server

## 0.3.0

- Optional MCP server (`pip install -e ".[mcp]"`, entry point `xycalc-mcp`)
  exposing `list_models`, `sizing`, `headroom`, `scenario`, and `why` over
  stdio. Payloads are the API serialisers plus `corpus_digest`; validation
  grade is never omitted.

## 0.2.0

- Export occupancy/cache-cliff tabs from corpus YAML guides rather than
  hardcoded Python (latency and cliff-shape figures are observation rows)

## 0.1.1

- Fix empty Celery (and other citation-only) scenario panels: hide the blank
  input form and surface the measured finding instead of an empty
  "What you need" box

## 0.1.0

Baseline release establishing normal package versioning.

- Single-source version from `pyproject.toml`
- Export provenance: `xycalc_version` + `xycalc_git` + `corpus_digest`
- Versioning policy documented in `docs/VERSIONING.md`
