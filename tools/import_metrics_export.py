"""Import Grafana / Prometheus / Coralogix metric exports into observation YAML.

Supported shapes (auto-detected, or pass --format):

* ``grafana_csv`` — Explore CSV (Time + Value columns, or Metric/Value)
* ``prometheus`` — Prometheus HTTP API ``query`` / ``query_range`` JSON,
  or OpenMetrics / Prometheus text exposition
* ``coralogix`` — DataPrime / metrics JSON (list of rows or ``result`` array)

Writes to ``local/`` by default (gitignored). Use ``--publish`` only for
numbers you are happy to put on the internet.

Unmapped series are listed in the result and skipped — add a row to
``tools/metrics_parameter_map.yaml`` (and a parameter in ``data/parameters.yaml``
if needed) rather than inventing a slug at import time.

    python tools/import_metrics_export.py export.csv \\
        --machine-class r6i.4xlarge --workload "prod read-heavy" \\
        --system-version 7.0.14
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
MAP_PATH = Path(__file__).resolve().parent / "metrics_parameter_map.yaml"

_PROM_LINE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)"
    r"(?:\{(?P<labels>[^}]*)\})?\s+"
    r"(?P<value>[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?\d+)?)"
    r"(?:\s+(?P<ts>\d+(?:\.\d+)?))?$"
)


def load_map() -> dict[str, dict[str, str]]:
    doc = yaml.safe_load(MAP_PATH.read_text(encoding="utf-8")) or {}
    raw = doc.get("mappings") or {}
    return {str(k).strip().lower(): v for k, v in raw.items()}


def _strip_prom_name(name: str) -> str:
    return re.sub(r"\{[^}]*\}", "", name).strip().lower()


def resolve_mapping(
    metric: str,
    mappings: dict[str, dict[str, str]],
    *,
    system: str | None,
    parameter: str | None,
    unit: str | None = None,
) -> dict[str, str] | None:
    if parameter:
        return {
            "system": system or "imported",
            "parameter": parameter,
            "unit": unit or "1",
            "agg": "last",
        }
    key = _strip_prom_name(metric)
    hit = mappings.get(key) or mappings.get(metric.strip().lower())
    if not hit:
        return None
    out = dict(hit)
    if system:
        out["system"] = system
    return out


def aggregate(values: list[float], how: str) -> float:
    if not values:
        raise ValueError("no values to aggregate")
    if how == "max":
        return max(values)
    if how == "min":
        return min(values)
    if how == "mean":
        return sum(values) / len(values)
    if how == "delta":
        return values[-1] - values[0]
    return values[-1]  # last


def detect_format(path: Path, text: str) -> str:
    name = path.name.lower()
    if name.endswith(".csv") or "Time," in text[:200] or "time," in text[:200]:
        return "grafana_csv"
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            if data.get("status") == "success" and "data" in data:
                return "prometheus"
            if "result" in data or "results" in data or "data" in data:
                # Coralogix often wraps rows; Prometheus too — prefer prom if resultType
                inner = data.get("data") or {}
                if isinstance(inner, dict) and inner.get("resultType"):
                    return "prometheus"
                return "coralogix"
        if isinstance(data, list):
            return "coralogix"
    if "# HELP" in text or "# TYPE" in text or _PROM_LINE.match(stripped.splitlines()[0] if stripped else ""):
        return "prometheus"
    raise SystemExit(
        f"cannot detect format for {path}; pass --format grafana_csv|prometheus|coralogix"
    )


def parse_grafana_csv(text: str) -> list[dict[str, Any]]:
    """Return series: {metric, points: [(ts, value), ...]}."""
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise SystemExit("grafana csv: no header row")
    fields = {f.lower(): f for f in reader.fieldnames}
    time_key = fields.get("time") or fields.get("timestamp") or fields.get("datetime")
    value_key = fields.get("value") or fields.get("avg") or fields.get("mean")
    metric_key = fields.get("metric") or fields.get("series") or fields.get("name")
    if not value_key:
        # wide format: first col time, rest metrics
        cols = list(reader.fieldnames)
        if len(cols) < 2:
            raise SystemExit("grafana csv: need Time + at least one value column")
        time_key = cols[0]
        series: dict[str, list] = {c: [] for c in cols[1:]}
        for row in reader:
            ts = row.get(time_key, "")
            for c in cols[1:]:
                raw = (row.get(c) or "").strip()
                if raw == "" or raw.lower() in ("null", "nan"):
                    continue
                series[c].append((ts, float(raw)))
        return [{"metric": m, "points": pts} for m, pts in series.items() if pts]

    series_map: dict[str, list] = {}
    for row in reader:
        metric = (row.get(metric_key) if metric_key else "value") or "value"
        raw = (row.get(value_key) or "").strip()
        if raw == "" or raw.lower() in ("null", "nan"):
            continue
        ts = row.get(time_key, "") if time_key else ""
        series_map.setdefault(metric, []).append((ts, float(raw)))
    return [{"metric": m, "points": pts} for m, pts in series_map.items() if pts]


def parse_prometheus(text: str) -> list[dict[str, Any]]:
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        data = json.loads(text)
        if isinstance(data, dict) and data.get("data"):
            data = data["data"]
        result = data.get("result") if isinstance(data, dict) else None
        if result is None and isinstance(data, dict):
            result = data.get("result", [])
        out = []
        for item in result or []:
            metric_labels = item.get("metric") or {}
            name = metric_labels.get("__name__") or metric_labels.get("name") or "series"
            if "values" in item:
                points = [(str(ts), float(v)) for ts, v in item["values"]]
            elif "value" in item:
                ts, v = item["value"]
                points = [(str(ts), float(v))]
            else:
                continue
            label_bits = ",".join(
                f'{k}="{v}"' for k, v in sorted(metric_labels.items()) if k != "__name__"
            )
            metric = f"{name}{{{label_bits}}}" if label_bits else name
            out.append({"metric": metric, "points": points})
        return out

    # text exposition / OpenMetrics
    out_map: dict[str, list] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _PROM_LINE.match(line)
        if not m:
            continue
        name = m.group("name")
        labels = m.group("labels") or ""
        metric = f"{name}{{{labels}}}" if labels else name
        ts = m.group("ts") or ""
        out_map.setdefault(metric, []).append((ts, float(m.group("value"))))
    return [{"metric": m, "points": pts} for m, pts in out_map.items() if pts]


def parse_coralogix(text: str) -> list[dict[str, Any]]:
    data = json.loads(text)
    rows = data
    if isinstance(data, dict):
        for key in ("result", "results", "data", "records", "rows"):
            if key in data and isinstance(data[key], list):
                rows = data[key]
                break
        else:
            if isinstance(data.get("data"), dict) and "result" in data["data"]:
                rows = data["data"]["result"]
    if not isinstance(rows, list):
        raise SystemExit("coralogix: expected a JSON list of rows")

    series_map: dict[str, list] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        metric = (
            row.get("metric")
            or row.get("name")
            or row.get("key")
            or row.get("series")
            or row.get("__name__")
            or "series"
        )
        if isinstance(metric, dict):
            metric = metric.get("__name__") or metric.get("name") or "series"
        value = row.get("value")
        if value is None:
            value = row.get("avg") or row.get("mean") or row.get("max") or row.get("last")
        if value is None:
            continue
        ts = row.get("timestamp") or row.get("time") or row.get("@timestamp") or ""
        series_map.setdefault(str(metric), []).append((str(ts), float(value)))
    return [{"metric": m, "points": pts} for m, pts in series_map.items() if pts]


PARSERS = {
    "grafana_csv": parse_grafana_csv,
    "prometheus": parse_prometheus,
    "coralogix": parse_coralogix,
}


def _observed_on(points: list) -> str:
    for ts, _ in reversed(points):
        if not ts:
            continue
        text = str(ts)
        # epoch seconds / ms
        try:
            num = float(text)
            if num > 1e12:
                num /= 1000.0
            if num > 1e9:
                return datetime.fromtimestamp(num, tz=timezone.utc).date().isoformat()
        except ValueError:
            pass
        # ISO-ish
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            if len(text) >= 10 and text[4] == "-":
                return text[:10]
    return date.today().isoformat()


def build_rows(
    series_list: list[dict[str, Any]],
    *,
    mappings: dict[str, dict[str, str]],
    system: str | None,
    parameter: str | None,
    machine_class: str,
    workload: str,
    system_version: str,
    tag: str,
    publisher: str,
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    sources = [
        {
            "slug": f"obs-{tag}",
            "title": f"Imported metrics ({tag})",
            "publisher": publisher,
            "type": "measured",
            "url": None,
            "notes": (
                "Imported via tools/import_metrics_export.py from Grafana CSV, "
                "Prometheus query/export, or Coralogix metrics JSON. "
                "Production identifiers belong in local/ only."
            ),
        }
    ]
    observations: list[dict] = []
    skipped: list[dict] = []
    for series in series_list:
        metric = series["metric"]
        points = series["points"]
        mapping = resolve_mapping(
            metric, mappings, system=system, parameter=parameter
        )
        if mapping is None:
            skipped.append(
                {
                    "metric": metric,
                    "reason": "unmapped — add tools/metrics_parameter_map.yaml entry",
                    "samples": len(points),
                }
            )
            continue
        values = [v for _, v in points]
        value = aggregate(values, mapping.get("agg", "last"))
        safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", _strip_prom_name(metric))[:60].strip("-")
        observations.append(
            {
                "slug": f"{tag}-{safe}",
                "system": mapping["system"],
                "parameter": mapping["parameter"],
                "value": value,
                "unit": mapping.get("unit", "1"),
                "workload": workload,
                "machine_class": machine_class,
                "system_version": system_version,
                "observed_on": _observed_on(points),
                "source": f"obs-{tag}",
                "notes": (
                    f"Imported metric {metric!r}; aggregated {mapping.get('agg', 'last')} "
                    f"over {len(values)} sample(s)."
                ),
            }
        )
    return sources, observations, [], skipped


def import_file(
    path: Path,
    *,
    format: str | None = None,
    system: str | None = None,
    parameter: str | None = None,
    machine_class: str = "unspecified",
    workload: str = "imported telemetry",
    system_version: str = "unknown",
    publish: bool = False,
    tag: str | None = None,
    publisher: str = "local measurement",
) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig")
    fmt = format or detect_format(path, text)
    if fmt not in PARSERS:
        raise SystemExit(f"unknown format {fmt!r}")
    series_list = PARSERS[fmt](text)
    mappings = load_map()
    use_tag = tag or f"metrics-{path.stem}-{date.today().isoformat()}"
    sources, observations, validations, skipped = build_rows(
        series_list,
        mappings=mappings,
        system=system,
        parameter=parameter,
        machine_class=machine_class,
        workload=workload,
        system_version=system_version,
        tag=use_tag,
        publisher=publisher,
    )

    root = ROOT / ("data" if publish else "local")
    written: list[str] = []
    if observations:
        for sub, key, rows in (
            ("sources", "sources", sources),
            ("observations", "observations", observations),
            ("validation", "validation", validations),
        ):
            if not rows:
                continue
            target = root / sub / f"{use_tag}.yaml"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                yaml.safe_dump({key: rows}, sort_keys=False), encoding="utf-8"
            )
            written.append(str(target.relative_to(ROOT)))

    # Always stash raw series summary for history even when nothing mapped.
    raw_dir = root / "metrics_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{use_tag}.json"
    raw_path.write_text(
        json.dumps(
            {
                "format": fmt,
                "source_file": str(path),
                "series": [
                    {
                        "metric": s["metric"],
                        "n": len(s["points"]),
                        "last": s["points"][-1][1] if s["points"] else None,
                    }
                    for s in series_list
                ],
                "skipped": skipped,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    written.append(str(raw_path.relative_to(ROOT)))

    return {
        "format": fmt,
        "tag": use_tag,
        "observations": len(observations),
        "skipped": skipped,
        "written": written,
        "publish": publish,
        "next": "xycalc build && xycalc audit",
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("export", type=Path)
    p.add_argument(
        "--format",
        choices=["auto", "grafana_csv", "prometheus", "coralogix"],
        default="auto",
    )
    p.add_argument("--system", help="Override system slug on mapped rows")
    p.add_argument(
        "--parameter",
        help="Force all series onto this parameter slug (must exist in parameters.yaml)",
    )
    p.add_argument("--machine-class", default="unspecified")
    p.add_argument("--workload", default="imported telemetry")
    p.add_argument("--system-version", default="unknown")
    p.add_argument("--tag")
    p.add_argument("--publisher", default="local measurement")
    p.add_argument(
        "--publish",
        action="store_true",
        help="write to data/ instead of local/",
    )
    args = p.parse_args(argv)

    result = import_file(
        args.export,
        format=None if args.format == "auto" else args.format,
        system=args.system,
        parameter=args.parameter,
        machine_class=args.machine_class,
        workload=args.workload,
        system_version=args.system_version,
        publish=args.publish,
        tag=args.tag,
        publisher=args.publisher,
    )
    for path in result["written"]:
        print(f"wrote {path}")
    print(
        f"\n{result['observations']} observation(s); "
        f"{len(result['skipped'])} skipped; format={result['format']}"
    )
    for s in result["skipped"][:20]:
        print(f"  skip: {s['metric']}: {s['reason']}")
    print(f"\nnow: {result['next']}")
    if not args.publish:
        print("(local/ is gitignored — these rows are yours, not the corpus's)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
