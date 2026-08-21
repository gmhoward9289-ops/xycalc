---
name: xycalc
description: >-
  Infrastructure sizing from a cited corpus — how much X to run Y (MongoDB RAM,
  EBS IOPS, Redis/Celery, instance picks). Use when George asks about sizing,
  headroom, scenarios, validation grades, xycalc MCP tools, Grafana/Prometheus/
  Coralogix metric imports, or corpus citations. Prefer MCP tools list_models,
  sizing, headroom, scenario, why; never invent coefficients.
---

# xycalc

Canonical repo: `C:\Users\gmhow\dev\xycalc` ·
GitHub: `github.com/gmhoward9289-ops/xycalc`

## Honesty contract

Every answer must carry:

1. **lo / mode / hi band** — never collapse to a single SKU before the user sees the range
2. **Per-term citations** (source, quote, applies_to / version)
3. **Validation grade** — `none` / `thin` / `reasonable` (say unvalidated aloud)
4. **corpus_digest** when using MCP

There is no bare-number path.

## MCP tools (Cursor: `xycalc` server)

| Tool | When |
|------|------|
| `list_models` | Discover models + validation grades before sizing |
| `sizing` | Evaluate a model with inputs |
| `headroom` | Sizing vs available capacity (`256GB`) |
| `scenario` | Chain (e.g. size → instance) |
| `why` | Citation chain without running the model |
| `import_metrics` | Grafana CSV / Prometheus JSON / Coralogix → `local/` observations |

Install: `pip install -e ".[mcp]"` then `xycalc-mcp` or `python -m xycalc`.

## Workflow

1. `list_models` — note grades
2. `sizing` or `scenario` with declared inputs
3. `why` if a coefficient looks wrong
4. For history: export metrics → `import_metrics` (default `local/`) → `xycalc build && xycalc audit`

## Grafana monitoring

Estate: `https://grafana.swamplink.com/` or tunnel `http://localhost:8108/`.
Folder **xycalc** is provisioned from the monitoring repo. Source recipes:
`deploy/grafana/` and `docs/telemetry/recommendations.md`.
