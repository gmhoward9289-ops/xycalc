"""Paste db.stats() / serverStatus → model inputs and a candidate observation.

An ingested measurement is a candidate. These tests exist so that contract
cannot regress into something that looks already cited or already validated.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from xycalc.build import build
from xycalc.cli import main
from xycalc.ingest import (
    TODO,
    IngestError,
    _parameter_map,
    extract_mongodb,
    observation_skeleton,
    parse_metrics,
    read_number,
    render_observation_yaml,
)
from xycalc.payloads import ingest_payload

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ingest"
WRAPPED = FIXTURES / "mongodb-wrapped-numberlong.json"
NESTED = FIXTURES / "mongodb-serverstatus-nested.json"


def _dump(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _run(argv, capsys):
    rc = main(argv)
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


class TestReadNumber:
    def test_mongosh_pair_is_the_swamplink_2gib_case(self):
        assert read_number({"high": 0, "low": -2147483648, "unsigned": False}) == 2 * 1024**3

    def test_ejson_and_boolean_refusal(self):
        assert read_number({"$numberLong": "500000000000"}) == 500000000000
        with pytest.raises(IngestError):
            read_number(True)


class TestExtractor:
    def test_wrapped_dump_maps_storage_not_datasize_as_storage_size(self):
        ext = extract_mongodb(_dump(WRAPPED))
        assert ext.model_inputs["storage_size"] == 500000000000
        assert ext.model_inputs["index_size"] == 40000000000
        assert ext.data_size == 1250000000000
        # The common mistake: feeding uncompressed dataSize as --storage-size.
        used_as = {f.path: f.used_as for f in ext.read}
        assert "storageSize" in "".join(used_as)
        data_size_uses = [f.used_as for f in ext.read if f.path.endswith("dataSize")]
        assert data_size_uses
        assert "NOT the model's --storage-size" in data_size_uses[0]
        assert ext.version == "7.0.14"
        assert ext.observed_on == "2026-07-15"
        assert ext.resident_cache == 2 * 1024**3
        assert ext.configured_cache == 8 * 1024**3
        ignored_blob = " ".join(ext.ignored)
        assert "objects" in ignored_blob
        assert "avgObjSize" in ignored_blob
        assert "ok" in ignored_blob
        assert "pages currently held in the cache" in ignored_blob
        # Mapped fields must not also appear as ignored.
        assert "storageSize" not in ignored_blob
        assert "dataSize" not in ignored_blob

    def test_nested_serverstatus_reads_version_and_lists_unmapped_regions(self):
        ext = extract_mongodb(_dump(NESTED))
        assert ext.version == "8.0.4"
        assert ext.observed_on == "2026-08-21"
        assert ext.model_inputs["storage_size"] == 131000000000
        assert ext.resident_cache == 298000000000
        ignored = set(ext.ignored)
        assert "serverStatus.connections" in ignored
        assert "serverStatus.extra_info" in ignored
        assert "serverStatus.wiredTiger.block-manager" in ignored
        assert "serverStatus.wiredTiger.concurrentTransactions" in ignored
        cache_keys = [p for p in ext.ignored if "eviction worker" in p]
        assert cache_keys

    def test_raw_dbstats_at_top_level(self):
        ext = extract_mongodb(
            {"db": "x", "dataSize": 10, "storageSize": 4, "indexSize": 1, "ok": 1}
        )
        assert ext.model_inputs == {"storage_size": 4, "index_size": 1}
        assert "ok" in ext.ignored

    def test_scalefactor_not_bytes_fails_loudly(self):
        with pytest.raises(IngestError, match="scaleFactor"):
            extract_mongodb(
                {"dataSize": 10, "storageSize": 4, "indexSize": 1, "scaleFactor": 1024}
            )

    def test_garbage_paste_fails_loudly(self):
        with pytest.raises(IngestError, match="does not look like"):
            extract_mongodb({"foo": 1, "bar": 2})
        with pytest.raises(IngestError, match="not JSON"):
            parse_metrics("not json {")


    def test_observation_parameters_come_from_the_shared_map(self):
        """Grafana import and db.stats ingest share tools/metrics_parameter_map.yaml."""
        ext = extract_mongodb(_dump(WRAPPED))
        by_field = {row["field"]: row["parameter"] for row in ext.observations}
        mappings, resolve = _parameter_map()
        assert resolve("storageSize", mappings, system=None, parameter=None)[
            "parameter"
        ] == by_field["storageSize"]
        assert resolve("dataSize", mappings, system=None, parameter=None)[
            "parameter"
        ] == by_field["dataSize"]
        assert resolve("indexSize", mappings, system=None, parameter=None)[
            "parameter"
        ] == by_field["indexSize"]
        assert resolve("bytes currently in the cache", mappings, system=None, parameter=None)[
            "parameter"
        ] == by_field["bytes currently in the cache"]


class TestHonesty:
    def test_skeleton_uses_todo_not_invented_publisher_or_date(self):
        ext = extract_mongodb(
            {"dataSize": 100, "storageSize": 40, "indexSize": 5, "ok": 1}
        )
        sk = observation_skeleton(ext)
        src = sk["sources"][0]
        assert src["publisher"] == TODO
        assert src["retrieved_on"] == TODO
        assert src["publisher"] != "local measurement"
        assert "CANDIDATE" in src["notes"]
        assert "not a validation" in src["notes"].lower()
        for obs in sk["observations"]:
            assert obs["workload"] == TODO
            assert obs["machine_class"] == TODO
            assert obs["system_version"] == TODO
            assert obs["observed_on"] == TODO
            assert "Candidate" in obs["notes"] or "candidate" in obs["notes"]

    def test_version_from_paste_is_applies_to_not_todo(self):
        ext = extract_mongodb(_dump(NESTED))
        sk = observation_skeleton(ext)
        assert sk["applies_to"] == "8.0.4"
        assert sk["sources"][0]["version"] == "8.0.4"
        assert sk["observations"][0]["system_version"] == "8.0.4"

    def test_payload_measurement_block_is_never_validated_or_cited(self, conn):
        body = ingest_payload(conn, _dump(WRAPPED), emit_observation=True)
        m = body["measurement"]
        assert m["status"] == "candidate"
        assert m["cited"] is False
        assert m["validated"] is False
        assert "not a cited" in m["text"]
        yaml_text = body["observation_yaml"]
        assert "CANDIDATE" in yaml_text
        assert "NOT been validated" in yaml_text
        assert "local measurement" not in yaml_text
        assert body["sizing"]["answer"]["mode"] > 0
        # The model's own grade is still present — that is about the model,
        # not about this paste.
        assert body["sizing"]["validation"]["text"]


class TestSkeletonBuildsOnceFilled:
    def test_filled_skeleton_is_accepted_by_build(self, corpus, tmp_path):
        ext = extract_mongodb(_dump(WRAPPED))
        sk = observation_skeleton(
            ext,
            tag="ingest-fixture-test",
            workload="read-heavy, production-shaped paste (fixture)",
            machine_class="r6i.4xlarge",
            publisher="fixture test",
        )
        src = corpus.data / "sources" / "ingest-fixture-test.yaml"
        obs = corpus.data / "observations" / "ingest-fixture-test.yaml"
        src.write_text(yaml.safe_dump({"sources": sk["sources"]}, sort_keys=False))
        obs.write_text(
            yaml.safe_dump({"observations": sk["observations"]}, sort_keys=False)
        )
        db = build(tmp_path / "ingest-filled.db")
        assert db.exists()


class TestCli:
    def test_ingest_prints_read_ignored_and_sizing(self, db_path, capsys):
        rc, out, err = _run(
            ["--db", str(db_path), "ingest", str(WRAPPED)],
            capsys,
        )
        assert rc == 0, err
        assert "CANDIDATE MEASUREMENT" in out
        assert "not cited" in out
        assert "READ" in out
        assert "IGNORED" in out
        assert "storageSize" in out
        assert "ANSWER" in out
        assert "not a validation" in out.lower()

    def test_emit_observation_yaml_is_a_skeleton_with_todos(
        self, db_path, tmp_path, capsys
    ):
        dest = tmp_path / "candidate.yaml"
        rc, out, err = _run(
            [
                "--db",
                str(db_path),
                "ingest",
                str(NESTED),
                "--emit-observation",
                str(dest),
            ],
            capsys,
        )
        assert rc == 0, err
        text = dest.read_text(encoding="utf-8")
        doc = yaml.safe_load(text)
        assert doc["sources"][0]["publisher"] == TODO
        assert doc["observations"][0]["workload"] == TODO
        assert doc["observations"][0]["system_version"] == "8.0.4"
        assert "CANDIDATE" in text
        units = {o["parameter"]: o["unit"] for o in doc["observations"]}
        assert units["storage.collection_bytes_on_disk"] == "bytes"
        assert units["storage.compression_ratio"] == "ratio"
