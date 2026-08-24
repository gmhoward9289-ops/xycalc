"""xycalc_exporter renders predicted gauges without Prom."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPORTER = ROOT / "tools" / "xycalc_exporter.py"


def test_exporter_once_emits_predicted_band():
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        py = Path(sys.executable)
    out = subprocess.check_output(
        [
            str(py),
            str(EXPORTER),
            "--once",
            "--storage-size",
            "500GB",
            "--index-size",
            "40GB",
            "--instance",
            "test-lab",
        ],
        cwd=str(ROOT),
        text=True,
    )
    assert 'xycalc_input_bytes{parameter="storage.collection_bytes_on_disk"' in out
    assert 'xycalc_predicted_bytes{model="mongodb.host-ram",bound="lo"' in out
    assert 'bound="mode"' in out
    assert 'bound="hi"' in out
    assert 'instance="test-lab"' in out
    assert "xycalc_exporter_up" in out


def test_metrics_map_covers_percona_and_yace():
    import yaml

    doc = yaml.safe_load(
        (ROOT / "tools" / "metrics_parameter_map.yaml").read_text(encoding="utf-8")
    )
    m = {k.lower(): v for k, v in (doc.get("mappings") or {}).items()}
    assert "mongodb_ss_wt_cache_bytes_currently_in_the_cache" in m
    assert m["aws_ebs_volume_iops_exceeded_check_maximum"]["agg"] == "max"
    assert m["mongodb_dbstats_storage_size"]["parameter"] == (
        "storage.collection_bytes_on_disk"
    )
