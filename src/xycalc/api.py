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
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .db import connect
from .export import corpus_blob, render
from .model import Model, ModelError, describe_scenarios
from .payloads import (
    list_models_payload,
    scenario_payload,
    sizing_payload,
    why_payload,
)

STATIC = Path(__file__).parent / "static"

app = FastAPI(
    title="xycalc",
    description="How much X does it take to run Y?",
    version=__version__,
)


def _conn():
    return connect()


def _model(slug: str) -> Model:
    try:
        return Model.load(_conn(), slug)
    except ModelError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/models")
def list_models():
    return list_models_payload(_conn())


@app.get("/api/sizing/{model_slug:path}")
def sizing(model_slug: str, **_):
    raise HTTPException(status_code=400, detail="use POST /api/sizing")


@app.post("/api/sizing")
def post_sizing(payload: dict):
    _model(payload.get("model", ""))
    try:
        return sizing_payload(
            _conn(),
            payload.get("model", ""),
            payload.get("inputs", {}),
            available=payload.get("available"),
        )
    except ModelError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.get("/api/scenarios")
def list_scenarios():
    return {"scenarios": describe_scenarios(_conn())}


@app.post("/api/scenario")
def post_scenario(payload: dict):
    try:
        return scenario_payload(
            _conn(),
            payload.get("scenario", ""),
            payload.get("inputs", {}),
            available=payload.get("available"),
        )
    except ModelError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.get("/api/why/{model_slug:path}")
def why(model_slug: str):
    """The citation chain, without running the model. What a reader wants when
    they distrust a number rather than when they need one."""
    _model(model_slug)
    return why_payload(_conn(), model_slug)


@app.get("/")
def index():
    """Same page as the static export — one calculator for GUI and deploy."""
    return HTMLResponse(render(corpus_blob(_conn())))


@app.get("/api/corpus")
def api_corpus():
    """The export blob, for tooling that wants the page without HTML."""
    return corpus_blob(_conn())


app.mount("/static", StaticFiles(directory=STATIC), name="static")
