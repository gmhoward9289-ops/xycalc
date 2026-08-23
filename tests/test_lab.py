"""Lab YAML is the public short form of testing — not FINDINGS, not optional."""

from __future__ import annotations

import sqlite3

from xycalc.export import export, render_lab_table_html
from xycalc.model import lab_status, validation_status


def test_every_model_has_lab_copy(db_path):
    conn = sqlite3.connect(db_path)
    models = [r[0] for r in conn.execute("SELECT slug FROM model")]
    assert models
    for slug in models:
        lab = lab_status(conn, slug)
        assert lab.get("measured"), slug
        assert lab.get("still_needs"), slug
        assert lab.get("label"), slug
        assert len(lab["measured"]) <= 400, slug
        assert len(lab["still_needs"]) <= 400, slug
    conn.close()


def test_azure_ceiling_cases_are_thin_not_empty(db_path):
    conn = sqlite3.connect(db_path)
    azure = validation_status(conn, "azure.premium-v2-throughput-ceiling")
    conn.close()
    assert azure["cases"] == 2
    assert azure["within_band"] == 2
    assert azure["grade"] == "thin"


def test_lab_table_names_measured_and_gap(db_path):
    conn = sqlite3.connect(db_path)
    html = render_lab_table_html(conn)
    conn.close()
    assert "Still needs a case" in html
    assert "Measured" in html
    assert "WiredTiger" in html
    assert "Premium SSD v2" in html
    assert "unvalidated (n=0)" in html


def test_export_writes_lab_table(db_path, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "abcdef0123456789")
    out = tmp_path / "calculator.html"
    export(out, db=db_path)
    lab = (tmp_path / "lab-table.html").read_text(encoding="utf-8")
    assert "Still needs a case" in lab
    assert "<table" in lab
