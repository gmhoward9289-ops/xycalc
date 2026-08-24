# Grafana pack for xycalc ops + sizing boards

**Primary home:** estate Grafana on swamplink
(`https://grafana.swamplink.com`, or tunnel `http://localhost:8108`).
Dashboards and Prometheus alert rules are provisioned from the
`monitoring` repo — not from a local smoke compose.

| Estate path | Role |
|---|---|
| `monitoring/grafana/provisioning/dashboards/xycalc/` | Board JSON (folder **xycalc**) |
| `monitoring/prometheus/alerts.yml` | `xycalc-pager` / `xycalc-ticket` groups |
| `monitoring/prometheus/` recording rules | Copy [`recording_rules.yml`](recording_rules.yml) (Percona → board aliases) |
| Prometheus datasource UID | `PBFA97CFB590B2093` |

Deploy estate changes from the monitoring repo:

```bash
cd ~/dev/monitoring
git push swamplink main
```

Then open folder **xycalc**:

- `/d/xycalc-mongodb-wt` — WiredTiger pressure
- `/d/xycalc-ebs-throttle` — ExceededCheck (max, never avg)
- `/d/xycalc-redis-celery` — broker headroom + outstanding
- `/d/xycalc-sizing-live` — live inputs + predicted RAM/SKU vs MemTotal

Panels stay empty until Mongo/Redis/EBS exporters scrape the named series.
Percona names are aliased in [`recording_rules.yml`](recording_rules.yml);
do not rewrite board PromQL for a lab dialect. Recipes remain authoritative
in `docs/telemetry/recommendations.md`.

Lab scrape (optional): `tools/bench/metrics_lab/` — Prometheus + recording
rules + Grafana provisioning pointed at the same datasource UID.

## Predicted band (sidecar)

`tools/xycalc_exporter.py` evaluates `mongodb.size-to-instance` from Prom
(or pasted inputs) and exposes `xycalc_predicted_bytes{bound=lo|mode|hi}` and
`xycalc_predicted_sku_info`. Minute cadence — planning, not paging. The
sizing board overlays those gauges on `node_memory_MemTotal_bytes`.

```bash
.venv/bin/python tools/xycalc_exporter.py \
  --prom-url http://127.0.0.1:9090 \
  --listen 127.0.0.1:9199
```

## This directory

Source JSON for the estate boards (keep in sync when recipes change).

## History → corpus

```bash
python tools/import_metrics_export.py path/to/export.csv \
  --machine-class r6i.4xlarge --workload "prod read-heavy" \
  --system-version 7.0.14
xycalc build && xycalc audit
```

Or MCP tool `import_metrics` (writes `local/` by default). Metric name →
parameter map: `tools/metrics_parameter_map.yaml`.
