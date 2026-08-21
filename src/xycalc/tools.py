"""Pure sync tool surface for the MCP server (and tests).

No ``mcp`` import here — keep the core free of the optional extra. Every
answer reuses the same payloads as the HTTP API so CLI / API / MCP cannot
drift on citations or validation grades.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .db import connect
from .export import corpus_blob
from .model import (
    Model,
    ModelError,
    build_instance_sizing_summary,
    chain_evaluate,
    describe_scenarios,
    get_scenario,
    headroom,
    parse_bytes,
)
from .payloads import serialise_result, serialise_scenario_step, why_payload

ROOT = Path(__file__).resolve().parent.parent.parent


def _conn(db: str | Path | None = None):
    return connect(Path(db) if db else None)


def _digest(conn) -> str:
    return corpus_blob(conn)["corpus_digest"]


def list_models(db: str | Path | None = None) -> dict[str, Any]:
    conn = _conn(db)
    out = []
    for slug in Model.all(conn):
        m = Model.load(conn, slug)
        from .model import validation_status

        out.append(
            {
                "slug": slug,
                "question": m.question,
                "system": m.system,
                "summary": m.summary,
                "unit": m.output_unit,
                "inputs": m.inputs,
                "validation": validation_status(conn, slug),
            }
        )
    return {"models": out, "corpus_digest": _digest(conn)}


def sizing(
    model: str,
    inputs: dict[str, Any] | None = None,
    available: str | None = None,
    db: str | Path | None = None,
) -> dict[str, Any]:
    """Run a model. Always returns band + step citations + validation grade."""
    conn = _conn(db)
    try:
        m = Model.load(conn, model)
        result = m.evaluate(inputs or {})
    except ModelError as e:
        return {"error": str(e), "corpus_digest": _digest(conn)}
    body = serialise_result(conn, result, m)
    if available:
        try:
            body["headroom"] = headroom(result, parse_bytes(available))
        except ModelError as e:
            return {"error": str(e), "corpus_digest": _digest(conn)}
    body["corpus_digest"] = _digest(conn)
    return body


def headroom_tool(
    model: str,
    available: str,
    inputs: dict[str, Any] | None = None,
    db: str | Path | None = None,
) -> dict[str, Any]:
    """Sizing plus headroom against an available capacity string (e.g. ``256GB``)."""
    if not available:
        return {"error": "available is required (e.g. '256GB')"}
    return sizing(model, inputs=inputs, available=available, db=db)


def scenario(
    slug: str | None = None,
    inputs: dict[str, Any] | None = None,
    available: str | None = None,
    db: str | Path | None = None,
) -> dict[str, Any]:
    """Run a scenario chain, or list scenarios when ``slug`` is omitted."""
    conn = _conn(db)
    if not slug:
        return {
            "scenarios": describe_scenarios(conn),
            "corpus_digest": _digest(conn),
        }
    try:
        sc = get_scenario(slug)
    except ModelError as e:
        return {"error": str(e), "corpus_digest": _digest(conn)}
    if sc.get("disabled"):
        return {
            "error": f"{sc['slug']}: not yet modeled",
            "corpus_digest": _digest(conn),
        }
    try:
        available_bytes = parse_bytes(available) if available else None
        steps = chain_evaluate(conn, sc, inputs or {}, available=available_bytes)
    except ModelError as e:
        return {"error": str(e), "corpus_digest": _digest(conn)}
    summary = build_instance_sizing_summary(steps, inputs or {})
    return {
        "scenario": sc["slug"],
        "label": sc["label"],
        "summary": sc.get("summary"),
        "see_also": sc.get("see_also", []),
        "steps": [serialise_scenario_step(conn, s) for s in steps],
        "sizing_summary": summary if summary else None,
        "corpus_digest": _digest(conn),
    }


def why(model: str, db: str | Path | None = None) -> dict[str, Any]:
    """Citation chain without evaluating — for distrust, not sizing."""
    conn = _conn(db)
    try:
        m = Model.load(conn, model)
    except ModelError as e:
        return {"error": str(e), "corpus_digest": _digest(conn)}
    body = why_payload(conn, m)
    body["corpus_digest"] = _digest(conn)
    return body


def import_metrics(
    path: str,
    *,
    format: str | None = None,
    system: str | None = None,
    parameter: str | None = None,
    machine_class: str = "unspecified",
    workload: str = "imported telemetry",
    system_version: str = "unknown",
    publish: bool = False,
    db: str | Path | None = None,
) -> dict[str, Any]:
    """Load Grafana CSV / Prometheus JSON / Coralogix JSON into observation YAML.

    Writes under ``local/`` by default (gitignored). Pass ``publish=True`` only
    for numbers you are happy to put on the internet.
    """
    # Late import: tools/ lives outside the package install path in editable mode.
    import sys

    tools_dir = ROOT / "tools"
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))
    import import_metrics_export as ime  # type: ignore

    result = ime.import_file(
        Path(path),
        format=format,
        system=system,
        parameter=parameter,
        machine_class=machine_class,
        workload=workload,
        system_version=system_version,
        publish=publish,
    )
    result["corpus_digest"] = _digest(_conn(db))
    return result
