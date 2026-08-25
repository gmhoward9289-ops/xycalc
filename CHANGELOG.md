# Changelog

All notable changes to the **xycalc tool** (not the corpus) are recorded here.
Corpus identity is `corpus_digest` on the exported page. See `docs/VERSIONING.md`.

## Unreleased

- Records keeps DB size (type it or carry it from the other control).
  Document count is MongoDB docs — part of the weight, not a second
  storage multiplier. Avg defaults to 0.5 MB with a slider, hard-capped
  at 16 MB. The 14 MB vuln checkbox is gone.

## 0.6.2

- Basic size slider defaults to 2.5 GB and tops out at 12 TB (was 500 GB
  default, 10 GB–32 TB scale).

## 0.6.1

- Calculator views are Basic / Advanced / Scientific / Data. Basic is
  footprint only (DB size, device count, avg space, other collections).
  Scientific holds cited math, a single model, and how it flows; Data holds
  occupancy and cache-cliff. `#mode=simple` still opens Basic.
- Basic is one instrument: DB size in on the left (type or scrub the
  log slider), Host RAM and AWS/Azure SKUs (Low / Typical / High) on the
  right. Refine estimate is a full-width drawer for optional device
  counts. Caveats are quiet tags under the number, not filled banners.
  Under the SKUs, a cited NVD CVE-publication chart (Jerry Gamblin 2023–2025)
  shows next year as the corpus YoY band applied to 2025's count — not an
  NVD forecast; Basic still sizes today's record count.
  Scrubbing DB size no longer rebuilds hidden Scientific cited-math HTML;
  that cascade paints when you open Cited math. The Single-question sweep
  chart is skipped while that tab is hidden.
- `xycalc export` copies Bill's approved still from
  `src/xycalc/static/landing-still.png` to `og.png` (no generated substitute).
  `stamp.html` is the same digest / version / git as the calculator footer.
  `deploy-calculator.yml` splices both into `tools/xycalc/`.
- Calculator first paint accepts `?model=` / `?scenario=` as an alias for
  the hash permalink (`#tab=single&model=…` remains the form landing
  tables should emit).
- Advanced scenario list is two bands (Hardware / Runtime). Instance
  sizing stays open; database, storage, Celery, MongoDB, and Redis
  questions start in drawers so the form and results stay above the fold.
  Scientific single-question uses the same grouping. Optional spec
  sections (current node, concurrency, query regime) start collapsed.

## 0.6.0

- `xycalc sizing … --sensitivity` ranks each coefficient by how much it
  moves the answer when walked across its lo..hi band with every other
  coefficient held at mode. The same ranking is on `xycalc why … --sensitivity`
  (same input flags as sizing). The lead sentence names the shares
  (`the band is 100% decompression into cache`); `measure next` points at
  the top term — what to measure to shrink the band, pairing with `xycalc ingest`.
  Fraction terms invert through `evaluate`, not a second arithmetic path.

## 0.5.0

- Occupancy-band total-cache labels: at ≤42rem, lift `trigger 95` off the
  `target 80` row and keep the 90 tick visible (#132). Desktop layout is
  unchanged from #130.
- `xycalc export` writes landing sidecars next to the calculator HTML:
  `og.png` (1200×630 sweep chart with the band envelope and “what you
  already have” line) and `stamp.html` (model count, `corpus_digest`,
  `xycalc_version`, `xycalc_git` — the same string as the calculator
  footer). Calculator HTML stays byte-deterministic; a skipped chart
  does not change it. `deploy-calculator.yml` copies both into
  `tools/xycalc/` in swamplink-root.
- `xycalc_version` on the export blob follows `pyproject.toml` when the
  running code is a source/editable checkout, so a stale `pip install`
  (metadata still 0.1.1) cannot lie in the footer.
- Permalink shape for landing deep-links is documented in
  `docs/CALCULATOR.md` (`#tab=single&model=<slug>`,
  `#tab=scenario&scenario=<slug>`).

## 0.4.0

- Homepage 500 GB Simple question names cited SKUs (`r8i.96xlarge` lo/mode,
  `u7i-12tb.224xlarge` high) instead of “custom sizing”. Default instance
  ceiling is the largest cited U7i (32,768 GiB); the AWS picker uses the
  whole `aws-ec2` catalog rather than `r8i` only. `--max-ram 1536GiB` restores
  the old org cap.
- Simple first paint and Advanced Scenario “What you need” show the three
  measured size-path footnotes (occupancy/cache-cliff, tickets, EBS
  peak-to-mean). Show the math pins occupancy and EBS on their chain steps
  only; tickets stay on the size-to-instance scenario (not every model).
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
