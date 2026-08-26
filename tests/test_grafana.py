"""Packed Grafana board UIDs and lab-window URLs."""

from __future__ import annotations

import sqlite3

import pytest

from xycalc.grafana import (
    GRAFANA_PUBLIC_BASE,
    GRAFANA_TUNNEL_BASE,
    dashboard_uids,
    dashboard_url,
)
from xycalc.model import lab_status


EXPECTED_UIDS = frozenset(
    {
        "xycalc-mongodb-wt",
        "xycalc-ebs-throttle",
        "xycalc-redis-celery",
        "xycalc-sizing-live",
        "xycalc-wt-cgroup",
    }
)


def test_packed_dashboard_uids_match_json():
    assert dashboard_uids() == EXPECTED_UIDS


def test_dashboard_url_uses_ms_and_var_prefix():
    url = dashboard_url(
        "xycalc-wt-cgroup",
        from_ts=1_000,
        to_ts=2_000,
        variables={"container": "xycalc-lab-mongo"},
    )
    assert url.startswith(GRAFANA_PUBLIC_BASE + "/d/xycalc-wt-cgroup?")
    assert "from=1000000" in url
    assert "to=2000000" in url
    assert "var-container=xycalc-lab-mongo" in url


def test_dashboard_url_rejects_unknown_uid():
    with pytest.raises(ValueError, match="unknown grafana uid"):
        dashboard_url("xycalc-not-a-board")


def test_tunnel_base_is_localhost():
    url = dashboard_url("xycalc-mongodb-wt", base=GRAFANA_TUNNEL_BASE)
    assert url == "http://localhost:8108/d/xycalc-mongodb-wt"


def test_every_lab_grafana_uid_is_packed_or_null(db_path):
    conn = sqlite3.connect(db_path)
    known = dashboard_uids()
    models = [r[0] for r in conn.execute("SELECT slug FROM model")]
    mapped = 0
    for slug in models:
        lab = lab_status(conn, slug)
        uid = lab.get("grafana_uid")
        if uid is None:
            continue
        mapped += 1
        assert uid in known, slug
    conn.close()
    assert mapped >= 8
