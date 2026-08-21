# Grafana pack for xycalc ops boards

**Primary home:** estate Grafana on swamplink
(`https://grafana.swamplink.com`, or tunnel `http://localhost:8108`).
Dashboards and Prometheus alert rules are provisioned from the
`monitoring` repo — not from a local smoke compose.

| Estate path | Role |
|---|---|
| `monitoring/grafana/provisioning/dashboards/xycalc/` | Board JSON (folder **xycalc**) |
| `monitoring/prometheus/alerts.yml` | `xycalc-pager` / `xycalc-ticket` groups |
| Prometheus datasource UID | `PBFA97CFB590B2093` |

Deploy estate changes from the monitoring repo:

```bash
cd ~/dev/monitoring
git push swamplink main
```

Then open folder **xycalc**:

- `/d/xycalc-mongodb-wt`
- `/d/xycalc-ebs-throttle`
- `/d/xycalc-redis-celery`

Panels stay empty until Mongo/Redis/EBS exporters scrape the named series.
Recipes remain authoritative in `docs/telemetry/recommendations.md`.

## This directory

Source JSON for the estate boards (keep in sync when recipes change).

## History → corpus

```bash
python tools/import_metrics_export.py path/to/export.csv \
  --machine-class r6i.4xlarge --workload "prod read-heavy" \
  --system-version 7.0.14
xycalc build && xycalc audit
```

Or MCP tool `import_metrics` (writes `local/` by default).
