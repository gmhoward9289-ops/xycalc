"""MCP registration for xycalc sizing tools.

Honesty contract (also in every tool description): answers include the lo/mode/hi
band, per-term citations, validation grade, and corpus digest. Unvalidated
models say so. There is no bare-number tool.
"""

from __future__ import annotations

from typing import Annotated, Any, Dict, Optional

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from . import __version__, tools

server = MCPServer(
    name="xycalc",
    title="xycalc (infra sizing)",
    version=__version__,
    instructions=(
        "Infrastructure sizing from a cited corpus: how much X to run Y.\n\n"
        "Honesty contract: every sizing answer includes the lo/mode/hi band, "
        "unit-labeled steps with sources/quotes/versions, a validation grade "
        "(none / thin / reasonable), and corpus_digest. Unvalidated models say "
        "so — do not present them as measured.\n\n"
        "Start with list_models, then sizing or scenario. Use why when the user "
        "distrusts a coefficient. Use headroom when they already have capacity.\n\n"
        "Import Grafana CSV, Prometheus query JSON, or Coralogix metrics via "
        "import_metrics into local/ observations (history for validation).\n\n"
        "Project: github.com/gmhoward9289-ops/xycalc"
    ),
)


def _read_only(title: str) -> ToolAnnotations:
    return ToolAnnotations(
        title=title,
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )


def _write_local(title: str) -> ToolAnnotations:
    return ToolAnnotations(
        title=title,
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )


@server.tool(name="list_models", annotations=_read_only("List models"))
async def list_models() -> Dict[str, Any]:
    """List corpus models with inputs and validation grades.

    Prefer this before sizing so you know which models are thin or unvalidated.
    """
    return tools.list_models()


@server.tool(name="sizing", annotations=_read_only("Size a model"))
async def sizing(
    model: Annotated[str, Field(description="Model slug, e.g. mongodb.wt-cache")],
    inputs: Annotated[
        Optional[Dict[str, Any]],
        Field(description="Model inputs as declared by list_models"),
    ] = None,
    available: Annotated[
        Optional[str],
        Field(description="Optional capacity for headroom, e.g. 256GB"),
    ] = None,
) -> Dict[str, Any]:
    """Evaluate a model. Returns band, cited steps, validation, corpus_digest.

    Never invent coefficients — if validation.grade is none, say so aloud.
    """
    return tools.sizing(model, inputs=inputs, available=available)


@server.tool(name="headroom", annotations=_read_only("Headroom vs capacity"))
async def headroom(
    model: Annotated[str, Field(description="Model slug")],
    available: Annotated[str, Field(description="Available capacity, e.g. 256GB")],
    inputs: Annotated[
        Optional[Dict[str, Any]],
        Field(description="Model inputs"),
    ] = None,
) -> Dict[str, Any]:
    """Sizing plus utilisation/margin against available capacity."""
    return tools.headroom_tool(model, available=available, inputs=inputs)


@server.tool(name="scenario", annotations=_read_only("Run or list scenarios"))
async def scenario(
    slug: Annotated[
        Optional[str],
        Field(description="Scenario slug; omit to list scenarios"),
    ] = None,
    inputs: Annotated[
        Optional[Dict[str, Any]],
        Field(description="Scenario inputs"),
    ] = None,
    available: Annotated[
        Optional[str],
        Field(description="Optional capacity for chained headroom"),
    ] = None,
) -> Dict[str, Any]:
    """Chain models into a scenario (e.g. size → instance), or list scenarios."""
    return tools.scenario(slug=slug, inputs=inputs, available=available)


@server.tool(name="why", annotations=_read_only("Citation chain"))
async def why(
    model: Annotated[str, Field(description="Model slug")],
) -> Dict[str, Any]:
    """Return the citation chain without running the model."""
    return tools.why(model)


@server.tool(name="import_metrics", annotations=_write_local("Import metrics history"))
async def import_metrics(
    path: Annotated[str, Field(description="Path to Grafana CSV, Prom JSON, or Coralogix JSON")],
    format: Annotated[
        Optional[str],
        Field(description="grafana_csv | prometheus | coralogix | auto"),
    ] = "auto",
    system: Annotated[
        Optional[str],
        Field(description="Override system slug (mongodb, redis, ebs, celery)"),
    ] = None,
    parameter: Annotated[
        Optional[str],
        Field(description="Override parameter slug from data/parameters.yaml"),
    ] = None,
    machine_class: Annotated[str, Field(description="Machine class for the observation")] = "unspecified",
    workload: Annotated[str, Field(description="Workload description")] = "imported telemetry",
    system_version: Annotated[str, Field(description="Software/version under observation")] = "unknown",
    publish: Annotated[
        bool,
        Field(description="Write to data/ (public). Default local/ gitignored."),
    ] = False,
) -> Dict[str, Any]:
    """Import exported metrics into observation YAML for validation history.

    Defaults to local/ (never published). Production identifiers stay out of git.
    """
    return tools.import_metrics(
        path,
        format=None if format in (None, "auto") else format,
        system=system,
        parameter=parameter,
        machine_class=machine_class,
        workload=workload,
        system_version=system_version,
        publish=publish,
    )


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
