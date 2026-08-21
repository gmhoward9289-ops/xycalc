"""MCP server over the xycalc corpus.

Stdio transport. Optional extra (`pip install -e ".[mcp]"`); the core package
does not grow a runtime dependency. Tools reuse `payloads.py` — the same
builders the HTTP API serialises — so an assistant cannot get a number without
the band, the citations, the validation grade, and the corpus digest.

The honesty contract is the point of this surface. An unvalidated model says
`unvalidated (n=0)` in the result. Omitting that would read as a validated
answer.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from . import __version__
from .db import connect
from .model import ModelError
from .payloads import (
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
    "Results include corpus_digest so two corpora cannot be confused."
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
            "Import Grafana Explore CSV, Prometheus query/query_range JSON "
            "(or OpenMetrics text), or Coralogix metrics JSON into observation "
            "YAML for validation history. Defaults to local/ (gitignored). "
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

    return server


def main() -> None:
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
