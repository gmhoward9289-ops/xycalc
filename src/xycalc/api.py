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
from .model import Model, ModelError, headroom, parse_bytes, validation_status

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
