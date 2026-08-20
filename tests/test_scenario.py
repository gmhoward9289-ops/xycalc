"""Scenario chains: gp3 lookup, instance sizing summary, when_input gating."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from xycalc.model import (
    InstanceSpec,
    build_instance_sizing_summary,
    chain_evaluate,
    get_scenario,
    gp3_volume_spec,
    parse_bytes,
    _attach_instance_ebs,
)

# 500 GB collection today (1:1 target count), plus 40 GB indexes — matches the
# sizing question wording even though the scenario's first step is NVD projection.
INSTANCE_INPUTS = {
    "baseline_vuln_count": "250000",
    "baseline_storage_size": "500GB",
    "target_vuln_count": "250000",
    "index_size": "40GB",
}


class TestGp3VolumeSpec:
    def test_500gb_volume_gib_and_max_iops(self):
        volume = parse_bytes("500GB")
        spec = gp3_volume_spec(volume)
        gib = volume / (1024**3)
        assert spec["volume_bytes"] == pytest.approx(volume)
        assert spec["volume_gib"] == pytest.approx(gib)
        assert spec["baseline_iops"] == 3000.0
        assert spec["max_provisionable_iops"] == pytest.approx(min(80_000.0, 500.0 * gib))
        assert spec["baseline_throughput_mibps"] == 125.0
        assert spec["max_throughput_mibps"] == 2000.0


class TestAttachInstanceEbs:
    def test_r8i_large_pipe_is_below_gp3_catalog_ceiling(self):
        spec = _attach_instance_ebs(
            gp3_volume_spec(parse_bytes("500GB")),
            InstanceSpec(
                name="r8i.large",
                ram_bytes=16 * 1024**3,
                vcpu=2,
                ebs_bandwidth_gbps=10,
                source_title="test",
                source_url=None,
            ),
        )
        assert spec["max_provisionable_iops"] > 3000
        assert spec["instance_ebs_bandwidth_gbps"] == 10
        assert spec["usable_throughput_mibps"] == pytest.approx(1250.0)
        assert spec["usable_throughput_mibps"] < spec["max_throughput_mibps"]


class TestChainEvaluate:
    @pytest.fixture
    def scenario(self):
        return get_scenario("mongodb.size-to-instance")

    def test_size_to_instance_includes_models_and_lookups(self, conn, scenario):
        steps = chain_evaluate(conn, scenario, INSTANCE_INPUTS)
        assert [(s.kind, s.slug) for s in steps] == [
            ("model", "nvd.storage-from-vuln-growth"),
            ("model", "mongodb.wt-cache"),
            ("model", "mongodb.host-ram"),
            ("lookup", "aws-ec2.instance-select"),
            ("lookup", "ebs.gp3-spec"),
            ("model", "ebs.iops-to-provision"),
        ]
        nvd = steps[0].result
        assert nvd is not None
        assert nvd.mode == pytest.approx(parse_bytes("500GB"))
        ebs = steps[-1]
        assert ebs.assumed_inputs is not None
        assert "average_iops" in ebs.assumed_inputs
        assert ebs.result.mode == pytest.approx(9000.0)

    def test_measured_average_replaces_included_iops_assumption(self, conn, scenario):
        without = chain_evaluate(conn, scenario, INSTANCE_INPUTS)
        ebs = next(s for s in without if s.slug == "ebs.iops-to-provision")
        assert ebs.assumed_inputs and "average_iops" in ebs.assumed_inputs
        assert ebs.result.mode == pytest.approx(9000.0)

        with_iops = chain_evaluate(
            conn, scenario, {**INSTANCE_INPUTS, "average_iops": "1200"}
        )
        slugs = [s.slug for s in with_iops]
        assert "ebs.iops-to-provision" in slugs
        assert slugs.index("ebs.gp3-spec") < slugs.index("ebs.iops-to-provision")
        measured = next(s for s in with_iops if s.slug == "ebs.iops-to-provision")
        assert not measured.assumed_inputs
        assert measured.result.mode == pytest.approx(3600.0)


class TestBuildInstanceSizingSummary:
    def test_extracts_ram_cpu_disk(self, conn):
        scenario = get_scenario("mongodb.size-to-instance")
        steps = chain_evaluate(conn, scenario, INSTANCE_INPUTS)
        summary = build_instance_sizing_summary(steps, INSTANCE_INPUTS)

        assert "ram" in summary
        assert summary["ram"]["unit"] == "bytes"
        assert summary["ram"]["lo"] < summary["ram"]["mode"] < summary["ram"]["hi"]

        assert "cpu" in summary
        assert summary["cpu"]["unit"] == "vcpu"

        assert "disk" in summary
        disk = summary["disk"]
        assert disk["baseline_iops"] == 3000.0
        assert disk["max_provisionable_iops"] > 0
        assert "volume_gib" in disk
        # gp3 volume = projected collection bytes + index footprint
        assert disk["volume_gib"] == pytest.approx(
            (parse_bytes("500GB") + parse_bytes("40GB")) / (1024**3)
        )
        assert disk["provisioned_iops"]["mode"] == pytest.approx(9000.0)
        assert disk["provisioned_iops_assumed_mean"] is True
        if disk.get("instance_ebs_bandwidth_gbps") is not None:
            assert disk["usable_throughput_mibps"] <= disk["max_throughput_mibps"]


@pytest.fixture
def api_client(db_path, monkeypatch):
    import xycalc.db as db_mod
    from xycalc.api import app

    monkeypatch.setattr(db_mod, "DEFAULT_DB", db_path)
    return TestClient(app)


class TestScenarioApi:
    def test_post_scenario_returns_sizing_summary(self, api_client):
        r = api_client.post(
            "/api/scenario",
            json={"scenario": "mongodb.size-to-instance", "inputs": INSTANCE_INPUTS},
        )
        assert r.status_code == 200
        body = r.json()

        assert body["scenario"] == "mongodb.size-to-instance"
        assert body["sizing_summary"] is not None
        assert "ram" in body["sizing_summary"]
        assert "cpu" in body["sizing_summary"]
        assert "disk" in body["sizing_summary"]

        kinds = [(s["kind"], s.get("lookup") or s.get("model")) for s in body["steps"]]
        assert ("lookup", "ebs.gp3-spec") in kinds
        assert ("model", "ebs.iops-to-provision") in kinds
        assert body["sizing_summary"]["disk"]["provisioned_iops"]["mode"] == pytest.approx(9000.0)
        kb = next(sa for sa in body["see_also"] if sa.get("url"))
        assert "ebs-troubleshoot-performance-issues-ec2" in kb["url"]

    def test_nvd_chart_is_cited_from_the_corpus(self, api_client):
        body = api_client.get("/api/scenarios").json()
        inst = next(s for s in body["scenarios"] if s["slug"] == "mongodb.size-to-instance")
        chart = inst["nvd_chart"]
        by_year = {row["year"]: row for row in chart["annual"]}
        assert by_year[2023]["count"] == 28818
        assert by_year[2024]["count"] == 40009
        assert by_year[2025]["count"] == 48185
        assert by_year[2025]["microsoft"] == 1255
        assert "microsoft" not in by_year[2023]
        assert chart["source"] == "jerrygamblin-2025-cve-review"
