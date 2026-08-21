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
            ("lookup", "azure-vm.instance-select"),
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
        mode_name = summary["cpu"]["instance_mode"]
        assert mode_name is None or mode_name.startswith("r8i")

        assert "azure" in summary
        azure = summary["azure"]
        assert azure["exceeds_pool"] in (True, False)
        if azure["mode"] is not None:
            assert azure["mode"].startswith("Esv6.")

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
            assert disk["instance_name"].startswith("r8i")

    def test_small_footprint_names_esv6_skus_per_band_end(self, conn):
        scenario = get_scenario("mongodb.size-to-instance")
        small = {
            "baseline_vuln_count": "250000",
            "baseline_storage_size": "8GB",
            "target_vuln_count": "250000",
            "index_size": "1GB",
        }
        steps = chain_evaluate(conn, scenario, small)
        azure = next(s for s in steps if s.slug == "azure-vm.instance-select")
        pick = azure.instance_pick
        assert pick["pick_lo"].name.startswith("Esv6.Standard_E")
        assert pick["pick_mode"].name.startswith("Esv6.Standard_E")
        assert pick["pick_hi"].name.startswith("Esv6.Standard_E")
        assert pick["pick_lo"].ram_bytes <= pick["pick_mode"].ram_bytes <= pick["pick_hi"].ram_bytes
        assert not pick["exceeds_pool"]
        aws = next(s for s in steps if s.slug == "aws-ec2.instance-select")
        assert aws.instance_pick["pick_mode"].name.startswith("r8i")


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

    def test_unbuilt_scenario_is_disabled_not_fatal(self, conn, monkeypatch):
        from xycalc.model import Model, ModelError, describe_scenarios

        orig = Model.load.__func__

        def selective(cls, c, slug):
            if slug == "ebs.gp3-iops-at-io-size":
                raise ModelError(f"no model '{slug}'")
            return orig(cls, c, slug)

        monkeypatch.setattr(Model, "load", classmethod(selective))
        scenarios = describe_scenarios(conn)
        gap = next(s for s in scenarios if s["slug"] == "storage.ebs-vs-nvme-at-io-size")
        assert gap["disabled"]
        assert gap["default"] is False
        assert "ebs.gp3-iops-at-io-size" in gap["note"]
        mongodb = next(s for s in scenarios if s["slug"] == "mongodb.size-to-instance")
        assert not mongodb["disabled"]


class TestRedisCeleryBrokerScenario:
    """Issue #88: compose 004/005 without picking a maxmemory-policy winner."""

    @pytest.fixture
    def scenario(self):
        return get_scenario("redis.celery-broker")

    def test_stub_is_gone_and_the_chain_is_enabled(self, conn):
        from xycalc.model import describe_scenarios

        listed = describe_scenarios(conn)
        slugs = [s["slug"] for s in listed]
        assert "redis.stub" not in slugs
        card = next(s for s in listed if s["slug"] == "redis.celery-broker")
        assert not card["disabled"]
        assert "no sizing scenario yet" not in (card.get("note") or "")

    def test_spine_is_failure_modes_then_drain_then_workers(self, conn, scenario):
        steps = chain_evaluate(conn, scenario, {})
        assert [(s.kind, s.slug) for s in steps] == [
            ("model", "celery.redis-broker-maxmemory"),
            ("model", "celery.queue-amplification"),
            ("model", "celery.worker-prefetch"),
        ]

    def test_does_not_pick_a_winner(self, conn, scenario):
        first = chain_evaluate(conn, scenario, {})[0]
        keys = {t.key for t in first.result.constraints}
        assert "noeviction_task_loss" in keys
        assert "allkeys_lru_task_loss" in keys
        assert "occupancy_alert" in keys
        labels = " ".join(t.label for t in first.result.constraints)
        assert "noeviction" in labels
        assert "allkeys-lru" in labels
        assert "used_memory/maxmemory" in labels
        reframe = first.model.reframe.lower()
        assert "neither" in reframe
        assert "do not pick a winner" in reframe
        # Answer is the documented policy count, not a loss rate that would
        # quietly prefer one arm.
        assert first.result.mode == pytest.approx(2.0)

    def test_names_the_measured_thresholds(self, conn, scenario):
        constraints = {
            t.key: t.coeff_mode
            for t in chain_evaluate(conn, scenario, {})[0].result.constraints
        }
        assert constraints["noeviction_task_loss"] == pytest.approx(1.0)
        assert constraints["allkeys_lru_task_loss"] == pytest.approx(0.6872)
        assert constraints["noeviction_worker_starts"] == pytest.approx(0)
        assert constraints["allkeys_lru_worker_starts"] == pytest.approx(1)

    def test_drain_and_ceiling_are_the_004_coefficients(self, conn, scenario):
        drain = next(
            s for s in chain_evaluate(conn, scenario, {}) if s.slug == "celery.queue-amplification"
        )
        assert drain.result.mode == pytest.approx(36.5)
        ceiling = next(
            t for t in drain.result.constraints if t.key == "completion_ceiling"
        )
        assert ceiling.coeff_mode == pytest.approx(82.4)

    def test_prefetch_defaults_to_the_004_baseline_concurrency(self, conn, scenario):
        pref = next(
            s for s in chain_evaluate(conn, scenario, {}) if s.slug == "celery.worker-prefetch"
        )
        assert pref.result.mode == pytest.approx(32.0)  # 8 × 4

    def test_prefetch_honours_supplied_concurrency(self, conn, scenario):
        pref = next(
            s
            for s in chain_evaluate(conn, scenario, {"concurrency": "4"})
            if s.slug == "celery.worker-prefetch"
        )
        assert pref.result.mode == pytest.approx(16.0)

    def test_new_models_print_unvalidated(self, conn):
        from xycalc.model import validation_status

        for slug in ("celery.redis-broker-maxmemory", "celery.worker-prefetch"):
            status = validation_status(conn, slug)
            assert status["validated"] is False
            assert status["cases"] == 0
            assert "unvalidated" in status["text"]
            assert "n=0" in status["text"]

    def test_api_returns_the_conflict_and_validation(self, api_client):
        r = api_client.post(
            "/api/scenario",
            json={"scenario": "redis.celery-broker", "inputs": {}},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["scenario"] == "redis.celery-broker"
        first = body["steps"][0]
        assert first["model"] == "celery.redis-broker-maxmemory"
        assert first["validation"]["validated"] is False
        assert "n=0" in first["validation"]["text"]
        constraint_keys = {c["key"] for c in first["constraints"]}
        assert "noeviction_task_loss" in constraint_keys
        assert "allkeys_lru_task_loss" in constraint_keys
        assert "Do not pick a winner" in first["reframe"]
