"""MCP server over the xycalc corpus.

Stdio transport. Optional extra (`pip install -e ".[mcp]"`); the core package
does not grow a runtime dependency. Tools reuse `payloads.py` — the same
builders the HTTP API serialises — so an assistant cannot get a number without
the band, the citations, the validation grade, and the corpus digest.

The honesty contract is the point of this surface. An unvalidated model says
`unvalidated (n=0)` in the result. Omitting that would read as a validated
answer.

Two import paths, deliberately distinct:

- ``import_metrics`` — Grafana / Prometheus / Coralogix *export files* into
  ``local/`` observation YAML (PR #110).
- ``ingest_dbstats`` — a pasted MongoDB ``db.stats()`` / ``serverStatus``
  document into model inputs and a *candidate* observation skeleton (issue #83).

They share ``tools/metrics_parameter_map.yaml`` for metric-name → parameter
slugs. They must not share a tool name: an agent cannot tell a CSV path from a
JSON paste if both are called ``import_metrics``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from . import __version__
from .db import connect
from .ingest import IngestError
from .model import ModelError
from .payloads import (
    ingest_payload,
    list_models_payload,
    scenario_payload,
    sizing_payload,
    why_payload,
    with_corpus_digest,
)

# Repeated in every tool description so a client that only shows the first
# tool still sees the grade rule. A listing without it would let an assistant
# treat n=0 as silence.
_HONESTY = (
    "Every result includes validation grade (none / thin / reasonable) and the "
    "verbatim status text. Unvalidated models say 'unvalidated (n=0)' — never "
    "omit this; a response without validation would read as a validated answer."
)

_INSTRUCTIONS = (
    "xycalc answers infrastructure sizing questions from a cited corpus. "
    "Always report the lo/mode/hi band, not a point estimate. "
    f"{_HONESTY} "
    "Cite per-term sources, quotes, and the versions they apply to. "
    "Results include corpus_digest so two corpora cannot be confused. "
    "import_metrics takes a Grafana/Prometheus/Coralogix export file. "
    "ingest_dbstats takes pasted MongoDB db.stats()/serverStatus JSON. "
    "Do not confuse the two."
)


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    """One connection per tool call; closed even if the handler raises."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def create_server():
    """Build the stdio MCP server. Imported lazily so `import xycalc.mcp`
    without the extra installed fails only when the SDK is actually needed."""
    try:
        from mcp.server.mcpserver import MCPServer
        from mcp.server.mcpserver.exceptions import ToolError
    except ImportError as e:  # pragma: no cover
        raise SystemExit(
            "xycalc MCP server requires the optional extra: "
            'pip install -e ".[mcp]"'
        ) from e

    server = MCPServer(
        name="xycalc",
        version=__version__,
        instructions=_INSTRUCTIONS,
    )

    def _fail(exc: Exception) -> None:
        raise ToolError(str(exc)) from exc

    @server.tool(
        name="list_models",
        description=(
            "List corpus models with their questions, inputs, and validation "
            f"grades. {_HONESTY}"
        ),
    )
    def list_models() -> dict[str, Any]:
        with _db() as conn:
            return with_corpus_digest(list_models_payload(conn), conn)

    @server.tool(
        name="sizing",
        description=(
            "How much X does this workload need? Returns lo/mode/hi band, "
            "unit-labeled step contributions, per-term sources/quotes/versions, "
            f"validation status, and corpus_digest. {_HONESTY} "
            "Pass model slug from list_models and inputs as a dict of declared "
            "input names to values (e.g. storage_size: '500GB')."
        ),
    )
    def sizing(model: str, inputs: dict[str, Any]) -> dict[str, Any]:
        with _db() as conn:
            try:
                body = sizing_payload(conn, model, inputs)
            except ModelError as e:
                _fail(e)
            return with_corpus_digest(body, conn)

    @server.tool(
        name="headroom",
        description=(
            "How much margin is left given what they already have? Same payload "
            "as sizing plus a headroom verdict against the whole band (covers "
            "mode but not high end is the interesting case). "
            f"{_HONESTY} available is a size string such as '256GB'."
        ),
    )
    def headroom(
        model: str, inputs: dict[str, Any], available: str
    ) -> dict[str, Any]:
        with _db() as conn:
            try:
                body = sizing_payload(conn, model, inputs, available=available)
            except ModelError as e:
                _fail(e)
            return with_corpus_digest(body, conn)

    @server.tool(
        name="scenario",
        description=(
            "Run a declared multi-model scenario chain (e.g. "
            "mongodb.size-to-instance). Each model step carries the same "
            "serialised answer as sizing, including validation grade. "
            f"{_HONESTY}"
        ),
    )
    def scenario(
        scenario: str,
        inputs: dict[str, Any],
        available: str | None = None,
    ) -> dict[str, Any]:
        with _db() as conn:
            try:
                body = scenario_payload(
                    conn, scenario, inputs, available=available
                )
            except ModelError as e:
                _fail(e)
            return with_corpus_digest(body, conn)

    @server.tool(
        name="why",
        description=(
            "Citation chain for a model without running it: every term's "
            "rationale, coefficient band, source, quote, and applies_to versions, "
            f"plus validation status. {_HONESTY}"
        ),
    )
    def why(model: str) -> dict[str, Any]:
        with _db() as conn:
            try:
                body = why_payload(conn, model)
            except ModelError as e:
                _fail(e)
            return with_corpus_digest(body, conn)

    @server.tool(
        name="import_metrics",
        description=(
            "Import a Grafana Explore CSV, Prometheus query/query_range JSON "
            "(or OpenMetrics text), or Coralogix metrics JSON *file* into "
            "observation YAML for validation history. Input is a filesystem "
            "path to an export, not a pasted db.stats() document — use "
            "ingest_dbstats for that. Defaults to local/ (gitignored). "
            "Pass publish=true only for numbers safe to put on the internet. "
            "Unmapped series are skipped and listed — extend "
            "tools/metrics_parameter_map.yaml rather than inventing slugs. "
            f"{_HONESTY}"
        ),
    )
    def import_metrics(
        path: str,
        format: str | None = None,
        system: str | None = None,
        parameter: str | None = None,
        machine_class: str = "unspecified",
        workload: str = "imported telemetry",
        system_version: str = "unknown",
        publish: bool = False,
    ) -> dict[str, Any]:
        # Late import: tools/ sits outside the installable package.
        import sys
        from pathlib import Path

        tools_dir = Path(__file__).resolve().parent.parent.parent / "tools"
        if str(tools_dir) not in sys.path:
            sys.path.insert(0, str(tools_dir))
        import import_metrics_export as ime  # type: ignore

        fmt = None if format in (None, "auto", "") else format
        result = ime.import_file(
            Path(path),
            format=fmt,
            system=system,
            parameter=parameter,
            machine_class=machine_class,
            workload=workload,
            system_version=system_version,
            publish=publish,
        )
        with _db() as conn:
            return with_corpus_digest(result, conn)

    @server.tool(
        name="ingest_dbstats",
        description=(
            "Paste MongoDB db.stats() / serverStatus JSON (object or JSON text) "
            "and get the model inputs the corpus actually consumes, a sizing "
            "run on those inputs, and (optionally) a ready-to-PR observation "
            "YAML skeleton. Input is the document itself, not a Grafana/"
            "Prometheus export file — use import_metrics for those. "
            "The paste is a CANDIDATE — not cited, not validated. Do not "
            "present ingest output as a corpus fact. This tool never writes "
            "files (YAML is returned in the payload only). Observation "
            "parameter slugs come from tools/metrics_parameter_map.yaml. "
            "Model results still include validation grade; unvalidated models "
            "say 'unvalidated (n=0)'. "
            f"{_HONESTY} "
            "emit_observation adds the YAML skeleton with TODO for provenance "
            "that cannot be derived."
        ),
    )
    def ingest_dbstats(
        metrics: str | dict[str, Any],
        emit_observation: bool = False,
        model: str = "mongodb.wt-cache",
        workload: str | None = None,
        machine_class: str | None = None,
        publisher: str | None = None,
    ) -> dict[str, Any]:
        with _db() as conn:
            try:
                body = ingest_payload(
                    conn,
                    metrics,
                    model=model,
                    emit_observation=emit_observation,
                    workload=workload,
                    machine_class=machine_class,
                    publisher=publisher,
                )
            except (IngestError, ModelError) as e:
                _fail(e)
            return with_corpus_digest(body, conn)

    return server


def main() -> None:
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
