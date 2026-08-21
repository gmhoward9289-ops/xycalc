"""Shared answer payloads for the HTTP API and the MCP server.

The honesty contract lives here so every surface ships the same shape: the
band, term-by-term citations, and validation status. A caller cannot get the
number without also getting how confident to be about it. MCP must not
re-derive this; it reuses these builders.
"""

from __future__ import annotations

import sqlite3

from .export import corpus_blob
from .model import (
    Model,
    ModelError,
    build_instance_sizing_summary,
    chain_evaluate,
    get_scenario,
    headroom,
    parse_bytes,
    validation_status,
)


def _available_bytes(available) -> float | None:
    """Refuse non-size values the way the HTTP surface does, so MCP and the
    API cannot disagree about what is unreadable."""
    if not available:
        return None
    if isinstance(available, bool) or not isinstance(available, (str, int, float)):
        raise ModelError(f"cannot read a size from {available!r}")
    try:
        return parse_bytes(available)
    except (TypeError, ValueError) as e:
        raise ModelError(str(e)) from e


def corpus_digest(conn: sqlite3.Connection) -> str:
    """The same 12-character digest the export blob stamps on the page."""
    return corpus_blob(conn)["corpus_digest"]


def with_corpus_digest(body: dict, conn: sqlite3.Connection) -> dict:
    """Stamp the digest onto a payload. Never optional — two answers from
    different corpora must be distinguishable without diffing the rest."""
    out = dict(body)
    out["corpus_digest"] = corpus_digest(conn)
    return out


def serialise(result, model: Model, conn: sqlite3.Connection) -> dict:
    return {
        "model": model.slug,
        "question": model.question,
        "unit": result.unit,
        "answer": {"lo": result.lo, "mode": result.mode, "hi": result.hi},
        "inputs": result.inputs,
        "steps": [
            {
                "key": s.term.key,
                "label": s.term.label,
                "role": s.term.role,
                "contribution": s.contribution,
                "running": {"lo": s.lo, "mode": s.mode, "hi": s.hi},
                "skipped": s.skipped,
                "skip_reason": s.skip_reason,
                "rationale": s.term.rationale,
                "coefficient": s.term.coefficient,
                "confidence": s.term.confidence,
                "applies_to": s.term.applies_to,
                "source": s.term.source,
                "source_title": s.term.source_title,
                "source_url": s.term.source_url,
                "quote": s.term.quote,
            }
            for s in result.steps
        ],
        "constraints": [
            {
                "key": t.key,
                "label": t.label,
                "value": t.coeff_mode,
                "unit": t.unit,
                "rationale": t.rationale,
                "source": t.source,
                "source_url": t.source_url,
            }
            for t in result.constraints
        ],
        # Never optional, never omitted when absent. A response without it
        # would read as a validated answer to anything that forgot to check.
        "validation": validation_status(conn, model.slug),
        "reframe": model.reframe,
        "notes": model.notes,
    }


def serialise_instance_spec(spec) -> dict | None:
    if spec is None:
        return None
    return {
        "name": spec.name,
        "ram_bytes": spec.ram_bytes,
        "vcpu": spec.vcpu,
        "ebs_bandwidth_gbps": spec.ebs_bandwidth_gbps,
        "source_title": spec.source_title,
        "source_url": spec.source_url,
    }


def serialise_instance_pick(pick: dict) -> dict:
    return {
        "required_lo": pick["required_lo"],
        "required_mode": pick["required_mode"],
        "required_hi": pick["required_hi"],
        "pick_lo": serialise_instance_spec(pick["pick_lo"]),
        "pick_mode": serialise_instance_spec(pick["pick_mode"]),
        "pick_hi": serialise_instance_spec(pick["pick_hi"]),
        "largest_in_pool": serialise_instance_spec(pick["largest_in_pool"]),
        "exceeds_pool": pick["exceeds_pool"],
    }


def list_models_payload(conn: sqlite3.Connection) -> dict:
    out = []
    for slug in Model.all(conn):
        m = Model.load(conn, slug)
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
    return {"models": out}


def why_payload(conn: sqlite3.Connection, model_slug: str) -> dict:
    model = Model.load(conn, model_slug)
    return {
        "model": model.slug,
        "question": model.question,
        "summary": model.summary,
        "reframe": model.reframe,
        "notes": model.notes,
        "validation": validation_status(conn, model.slug),
        "terms": [
            {
                "key": t.key,
                "label": t.label,
                "role": t.role,
                "rationale": t.rationale,
                "coefficient": t.coefficient,
                "band": (
                    None
                    if t.coefficient is None
                    else {"lo": t.coeff_lo, "mode": t.coeff_mode, "hi": t.coeff_hi}
                ),
                "unit": t.unit,
                "confidence": t.confidence,
                "applies_to": t.applies_to,
                "source": t.source,
                "source_title": t.source_title,
                "source_url": t.source_url,
                "quote": t.quote,
                "input_key": t.input_key,
            }
            for t in model.terms
        ],
    }


def sizing_payload(
    conn: sqlite3.Connection,
    model_slug: str,
    inputs: dict,
    available=None,
) -> dict:
    model = Model.load(conn, model_slug)
    result = model.evaluate(inputs or {})
    body = serialise(result, model, conn)
    available_bytes = _available_bytes(available)
    if available_bytes is not None:
        body["headroom"] = headroom(result, available_bytes)
    return body


def scenario_payload(
    conn: sqlite3.Connection,
    scenario_slug: str,
    inputs: dict,
    available=None,
) -> dict:
    scenario = get_scenario(scenario_slug)
    if scenario.get("disabled"):
        raise ModelError(f"{scenario['slug']}: not yet modeled")

    steps = chain_evaluate(
        conn, scenario, inputs or {}, available=_available_bytes(available)
    )

    body_steps = []
    for s in steps:
        if s.kind == "model":
            step_body = serialise(s.result, s.model, conn)
            step_body["chained"] = s.chained
            if s.headroom is not None:
                step_body["headroom"] = s.headroom
            if s.assumed_inputs:
                step_body["assumed_inputs"] = s.assumed_inputs
            body_steps.append({"kind": "model", **step_body})
        else:
            if s.gp3_spec is not None:
                body_steps.append(
                    {
                        "kind": "lookup",
                        "lookup": s.slug,
                        "chained": False,
                        "gp3": s.gp3_spec,
                    }
                )
            else:
                body_steps.append(
                    {
                        "kind": "lookup",
                        "lookup": s.slug,
                        "chained": True,
                        "pick": serialise_instance_pick(s.instance_pick),
                    }
                )

    summary = build_instance_sizing_summary(steps, inputs or {})

    return {
        "scenario": scenario["slug"],
        "label": scenario["label"],
        "summary": scenario.get("summary"),
        "see_also": scenario.get("see_also", []),
        "steps": body_steps,
        "sizing_summary": summary if summary else None,
    }
