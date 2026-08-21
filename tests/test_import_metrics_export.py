"""Grafana / Prometheus / Coralogix metric import → observation YAML."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import import_metrics_export as ime  # noqa: E402


def test_grafana_csv_maps_cache_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(ime, "ROOT", tmp_path)
    csv_path = tmp_path / "explore.csv"
    csv_path.write_text(
        "Time,Metric,Value\n"
        "2026-08-01T00:00:00Z,bytes currently in the cache,100\n"
        "2026-08-01T00:01:00Z,bytes currently in the cache,200\n",
        encoding="utf-8",
    )
    result = ime.import_file(
        csv_path,
        format="grafana_csv",
        machine_class="test-box",
        system_version="7.0.14",
        tag="test-grafana",
    )
    assert result["format"] == "grafana_csv"
    assert result["observations"] == 1
    obs_path = tmp_path / "local" / "observations" / "test-grafana.yaml"
    doc = yaml.safe_load(obs_path.read_text(encoding="utf-8"))
    row = doc["observations"][0]
    assert row["parameter"] == "cache.size_bytes"
    assert row["value"] == 200  # last


def test_prometheus_query_json(tmp_path, monkeypatch):
    monkeypatch.setattr(ime, "ROOT", tmp_path)
    payload = {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {
                    "metric": {"__name__": "maxmemory"},
                    "value": [1722470400, "8589934592"],
                }
            ],
        },
    }
    path = tmp_path / "prom.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = ime.import_file(path, format="prometheus", tag="test-prom")
    assert result["observations"] == 1
    doc = yaml.safe_load(
        (tmp_path / "local" / "observations" / "test-prom.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert doc["observations"][0]["parameter"] == "server.maxmemory_bytes"


def test_coralogix_rows_and_skip_unmapped(tmp_path, monkeypatch):
    monkeypatch.setattr(ime, "ROOT", tmp_path)
    path = tmp_path / "cx.json"
    path.write_text(
        json.dumps(
            [
                {"metric": "maxmemory", "value": 1000, "timestamp": "2026-08-01"},
                {"metric": "totally_unknown_series", "value": 1},
            ]
        ),
        encoding="utf-8",
    )
    result = ime.import_file(path, format="coralogix", tag="test-cx")
    assert result["observations"] == 1
    assert any(s["metric"] == "totally_unknown_series" for s in result["skipped"])
    raw = json.loads(
        (tmp_path / "local" / "metrics_raw" / "test-cx.json").read_text(encoding="utf-8")
    )
    assert raw["format"] == "coralogix"
