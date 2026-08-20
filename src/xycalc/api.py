"""The calculator's HTTP surface.

Every response carries the same three things the CLI prints: the band, the
term-by-term breakdown with its citations, and the validation status. A caller
cannot get the number without also getting how confident to be about it — which
is the point, and the reason there is no bare `{"answer": 1612500000000}`
endpoint to be tempted by.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .db import connect
from .model import (
    Model,
    ModelError,
    build_instance_sizing_summary,
    chain_evaluate,
    get_scenario,
    headroom,
    load_scenarios,
    parse_bytes,
    scenario_form_inputs,
    validation_status,
)

STATIC = Path(__file__).parent / "static"

app = FastAPI(
    title="xycalc",
    description="How much X does it take to run Y?",
    version="0.1.0",
)


def _conn():
    return connect()


def _model(slug: str) -> Model:
    try:
        return Model.load(_conn(), slug)
    except ModelError as e:
        raise HTTPException(status_code=404, detail=str(e))


def _serialise(result, model: Model) -> dict:
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
        "validation": validation_status(_conn(), model.slug),
        "reframe": model.reframe,
    }


@app.get("/api/models")
def list_models():
    conn = _conn()
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


@app.get("/api/sizing/{model_slug:path}")
def sizing(model_slug: str, **_):
    raise HTTPException(status_code=400, detail="use POST /api/sizing")


@app.post("/api/sizing")
def post_sizing(payload: dict):
    model = _model(payload.get("model", ""))
    try:
        result = model.evaluate(payload.get("inputs", {}))
    except ModelError as e:
        raise HTTPException(status_code=422, detail=str(e))
    body = _serialise(result, model)

    available = payload.get("available")
    if available:
        try:
            body["headroom"] = headroom(result, parse_bytes(available))
        except ModelError as e:
            raise HTTPException(status_code=422, detail=str(e))
    return body


def _serialise_instance_spec(spec) -> dict | None:
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


def _serialise_instance_pick(pick: dict) -> dict:
    return {
        "required_lo": pick["required_lo"],
        "required_mode": pick["required_mode"],
        "required_hi": pick["required_hi"],
        "pick_lo": _serialise_instance_spec(pick["pick_lo"]),
        "pick_mode": _serialise_instance_spec(pick["pick_mode"]),
        "pick_hi": _serialise_instance_spec(pick["pick_hi"]),
        "largest_in_pool": _serialise_instance_spec(pick["largest_in_pool"]),
        "exceeds_pool": pick["exceeds_pool"],
    }


@app.get("/api/scenarios")
def list_scenarios():
    conn = _conn()
    out = []
    for s in load_scenarios():
        entry = {
            "slug": s["slug"],
            "label": s["label"],
            "summary": s.get("summary"),
            "default": bool(s.get("default")),
            "disabled": bool(s.get("disabled")),
            "note": s.get("note"),
            "see_also": s.get("see_also", []),
            "extra_inputs": s.get("extra_inputs", []),
        }
        if not entry["disabled"]:
            form_inputs = scenario_form_inputs(conn, s)
            input_map = {i["key"]: i for i in form_inputs}
            for extra in entry.get("extra_inputs", []):
                input_map[extra["key"]] = extra
            entry["inputs"] = form_inputs
            sections = []
            for sec in s.get("input_sections", []):
                sections.append(
                    {
                        "title": sec["title"],
                        "inputs": [
                            input_map[k]
                            for k in sec["keys"]
                            if k in input_map
                        ],
                    }
                )
            if sections:
                entry["input_sections"] = sections
            first_model = next(
                (
                    st["model"]
                    for st in s["steps"]
                    if st.get("kind", "model") == "model"
                ),
                None,
            )
            if first_model:
                m = Model.load(conn, first_model)
                entry["question"] = m.question
                entry["unit"] = m.output_unit
            if s.get("slug") == "mongodb.size-to-instance":
                entry["nvd_chart"] = {
                    "annual": [
                        {"year": 2023, "count": 28818},
                        {"year": 2024, "count": 40009},
                        {"year": 2025, "count": 48185},
                    ],
                    "cumulative_2025": 308920,
                    "growth_pct": {"lo": 15, "mode": 21, "hi": 39},
                    "source": "jerrygamblin-2025-cve-review",
                    "source_url": (
                        "https://jerrygamblin.com/2026/01/01/2025-cve-data-review/"
                    ),
                }
        out.append(entry)
    return {"scenarios": out}


@app.post("/api/scenario")
def post_scenario(payload: dict):
    scenario = get_scenario(payload.get("scenario", ""))
    if scenario.get("disabled"):
        raise HTTPException(status_code=422, detail=f"{scenario['slug']}: not yet modeled")

    available = payload.get("available")
    try:
        available_bytes = parse_bytes(available) if available else None
        steps = chain_evaluate(
            _conn(), scenario, payload.get("inputs", {}), available=available_bytes
        )
    except ModelError as e:
        raise HTTPException(status_code=422, detail=str(e))

    body_steps = []
    for s in steps:
        if s.kind == "model":
            step_body = _serialise(s.result, s.model)
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
                        "pick": _serialise_instance_pick(s.instance_pick),
                    }
                )

    summary = build_instance_sizing_summary(steps, payload.get("inputs", {}))

    return {
        "scenario": scenario["slug"],
        "label": scenario["label"],
        "summary": scenario.get("summary"),
        "see_also": scenario.get("see_also", []),
        "steps": body_steps,
        "sizing_summary": summary if summary else None,
    }


@app.get("/api/why/{model_slug:path}")
def why(model_slug: str):
    """The citation chain, without running the model. What a reader wants when
    they distrust a number rather than when they need one."""
    model = _model(model_slug)
    return {
        "model": model.slug,
        "question": model.question,
        "summary": model.summary,
        "reframe": model.reframe,
        "validation": validation_status(_conn(), model.slug),
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


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
