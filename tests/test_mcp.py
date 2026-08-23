"""MCP stdio surface.

The contract is the same as the HTTP API: a caller cannot get a number without
the citations and the validation grade. These tests spawn the real stdio
server so that contract is asserted across the process boundary, not only
in-process where a missed field is easier to hide.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

mcp = pytest.importorskip("mcp")

from mcp import ClientSession  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402

SIZING_INPUTS = {"storage_size": "500GB", "index_size": "40GB"}
SCENARIO_INPUTS = {
    "baseline_vuln_count": "250000",
    "baseline_storage_size": "500GB",
    "target_vuln_count": "250000",
    "index_size": "40GB",
}


def _env_for_server(db_path) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "XYCALC_DB": str(db_path),
        "PYTHONUNBUFFERED": "1",
    }
    # Editable installs resolve via site-packages; keep VIRTUAL_ENV if pytest
    # is running inside one so the child finds the same xycalc.
    for key in ("VIRTUAL_ENV", "PYTHONPATH"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


async def _call(db_path, name: str, arguments: dict[str, Any] | None = None):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "xycalc.mcp"],
        env=_env_for_server(db_path),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool(name, arguments or {})


def call(db_path, name: str, arguments: dict[str, Any] | None = None):
    return asyncio.run(_call(db_path, name, arguments))


def payload(result) -> dict:
    """Prefer structured content; fall back to the JSON text block."""
    assert not result.is_error, getattr(result.content[0], "text", result)
    data = result.structured_content
    if data is None:
        data = json.loads(result.content[0].text)
    if not isinstance(data, dict):
        pytest.fail(f"tool result is not an object: {data!r}")
    return data


def dumped(result) -> str:
    """What an assistant that only reads the text block would see."""
    body = payload(result)
    texts = [c.text for c in result.content if getattr(c, "text", None)]
    return json.dumps(body) + "\n" + "\n".join(texts)


def assert_validation_unavoidable(result, *, expect_unvalidated: bool = False):
    body = payload(result)
    text = dumped(result)
    assert "corpus_digest" in body and body["corpus_digest"]
    if "validation" in body:
        statuses = [body["validation"]]
    else:
        statuses = [
            step["validation"]
            for step in body.get("steps", [])
            if isinstance(step, dict) and step.get("kind") == "model"
        ]
        if "models" in body:
            statuses = [m["validation"] for m in body["models"]]
    assert statuses, "result carried no validation status"
    for status in statuses:
        assert status["text"], "validation text must never be empty"
        assert "grade" in status
        assert isinstance(status["validated"], bool)
        assert status["text"] in text
    if expect_unvalidated:
        assert any("unvalidated" in s["text"] for s in statuses)
        assert "unvalidated" in text


class TestHonestyInToolDescriptions:
    def test_every_tool_names_the_unvalidated_rule(self, db_path):
        async def _list(db_path):
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "xycalc.mcp"],
                env=_env_for_server(db_path),
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await session.list_tools()

        listed = asyncio.run(_list(db_path))
        names = {t.name for t in listed.tools}
        assert names == {
            "list_models",
            "sizing",
            "headroom",
            "scenario",
            "why",
            "import_metrics",
            "ingest_dbstats",
        }
        by_name = {t.name: (t.description or "") for t in listed.tools}
        assert "Grafana" in by_name["import_metrics"] or "Prometheus" in by_name["import_metrics"]
        assert "db.stats" in by_name["ingest_dbstats"]
        assert "ingest_dbstats" in by_name["import_metrics"]
        assert "import_metrics" in by_name["ingest_dbstats"]
        assert "never writes files" in by_name["ingest_dbstats"]
        for tool in listed.tools:
            desc = tool.description or ""
            assert "unvalidated (n=0)" in desc, tool.name
            assert "validation" in desc.lower(), tool.name


class TestListModels:
    def test_lists_models_with_grades_and_digest(self, db_path):
        result = call(db_path, "list_models")
        body = payload(result)
        slugs = {m["slug"] for m in body["models"]}
        assert "mongodb.wt-cache" in slugs
        for m in body["models"]:
            assert m["validation"]["text"]
            assert m["validation"]["grade"] in {"none", "thin", "reasonable"}
        assert_validation_unavoidable(result)


class TestSizing:
    def test_returns_band_citations_grade_and_digest(self, db_path):
        result = call(
            db_path,
            "sizing",
            {"model": "mongodb.wt-cache", "inputs": SIZING_INPUTS},
        )
        body = payload(result)
        assert body["answer"]["lo"] < body["answer"]["mode"] < body["answer"]["hi"]
        cited = [
            s
            for s in body["steps"]
            if not s["skipped"] and s["coefficient"] is not None
        ]
        assert cited
        for step in cited:
            assert step["source"], step["key"]
            assert step["applies_to"], step["key"]
            assert step["contribution"]
        assert_validation_unavoidable(result)

    def test_unvalidated_model_says_so_in_the_result(self, db_path):
        result = call(
            db_path,
            "sizing",
            {
                "model": "clickhouse.parts-insert-ceiling",
                "inputs": {},
            },
        )
        body = payload(result)
        assert body["validation"]["validated"] is False
        assert "unvalidated (n=0)" in body["validation"]["text"]
        assert_validation_unavoidable(result, expect_unvalidated=True)


class TestHeadroom:
    def test_headroom_carries_verdict_and_grade(self, db_path):
        result = call(
            db_path,
            "headroom",
            {
                "model": "mongodb.wt-cache",
                "inputs": SIZING_INPUTS,
                "available": "4TB",
            },
        )
        body = payload(result)
        assert "covered" in body["headroom"]["verdict"]
        assert_validation_unavoidable(result)


class TestScenario:
    def test_each_model_step_carries_citations_and_grade(self, db_path):
        result = call(
            db_path,
            "scenario",
            {
                "scenario": "mongodb.size-to-instance",
                "inputs": SCENARIO_INPUTS,
            },
        )
        body = payload(result)
        model_steps = [s for s in body["steps"] if s.get("kind") == "model"]
        assert model_steps
        for step in model_steps:
            assert step["validation"]["text"]
            assert "answer" in step
            assert step["answer"]["mode"] is not None
        assert_validation_unavoidable(result)


class TestWhy:
    def test_citation_chain_and_grade(self, db_path):
        result = call(db_path, "why", {"model": "ebs.iops-to-provision"})
        body = payload(result)
        assert body["terms"]
        quoted = [t for t in body["terms"] if t["quote"]]
        sourced = [t for t in body["terms"] if t["source"]]
        assert sourced
        assert quoted or sourced
        for term in sourced:
            assert term["source"]
        assert_validation_unavoidable(result)
        assert body["validation"]["grade"] in {"none", "thin", "reasonable"}
        wt = call(db_path, "why", {"model": "mongodb.wt-cache"})
        wt_body = payload(wt)
        for term in wt_body["terms"]:
            assert term["rationale"]
        assert_validation_unavoidable(wt)


class TestIngestDbstats:
    def test_paste_returns_candidate_extraction_and_sizing(self, db_path):
        fixture = Path(__file__).resolve().parent / "fixtures" / "ingest" / "mongodb-wrapped-numberlong.json"
        raw = fixture.read_text(encoding="utf-8")
        result = call(db_path, "ingest_dbstats", {"metrics": raw})
        body = payload(result)
        assert body["measurement"]["status"] == "candidate"
        assert body["measurement"]["cited"] is False
        assert body["measurement"]["validated"] is False
        assert body["model_inputs"]["storage_size"] == 500000000000
        assert body["sizing"]["answer"]["mode"] > 0
        assert_validation_unavoidable(result)
        text = dumped(result)
        assert "candidate" in text.lower()
        assert "not a cited" in body["measurement"]["text"]

    def test_emit_observation_yaml_uses_todo_not_filler(self, db_path):
        fixture = Path(__file__).resolve().parent / "fixtures" / "ingest" / "mongodb-serverstatus-nested.json"
        result = call(
            db_path,
            "ingest_dbstats",
            {"metrics": fixture.read_text(encoding="utf-8"), "emit_observation": True},
        )
        body = payload(result)
        yaml_text = body["observation_yaml"]
        assert "publisher: TODO" in yaml_text
        assert "local measurement" not in yaml_text
        assert "CANDIDATE" in yaml_text
        assert "source_type: measured" not in yaml_text
        assert body["applies_to"] == "8.0.4"
