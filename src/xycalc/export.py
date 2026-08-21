"""Export the calculator as one static HTML file that needs no server.

`xycalc gui` serves the corpus from FastAPI, which is the right shape for a
laptop and the wrong shape for a public page: it means a process to run, a port
to expose and a service to keep alive to answer a question that is, in the end,
a few dozen multiplications over a 100 KB corpus. So this compiles the corpus,
the arithmetic and the page into a single file that can be dropped on any
static host, opened from a USB stick, or read offline.

The uncomfortable part is that it makes the arithmetic exist twice -- once in
`model.py`, once in `static/evaluate.js`. A project whose entire claim is that
its numbers are trustworthy cannot have two implementations quietly disagreeing,
so the export writes GOLDEN VECTORS into the blob: input combinations with the
lo/mode/hi and the contribution strings Python produced for them. Those are
checked in three overlapping places, on purpose:

  * `tests/test_export.py` runs evaluate.js under node against them in CI;
  * the exported page re-runs them on load and refuses to render a number if
    any disagree;
  * a reader can open the file and read them.

The vectors come off a fixed ladder rather than being sampled, so the export is
deterministic: the same blob dict yields byte-identical HTML. No timestamp is
written into the file for the same reason -- a build artifact that differs on
every run cannot be diffed, and "what changed" is the question anyone
re-exporting is actually asking. `xycalc_git` records which commit produced the
blob, so two exports of the same corpus from different commits differ on purpose.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from . import __version__
from .db import connect
from .version import git_identity
from .model import (
    Model,
    ModelError,
    chain_evaluate,
    describe_scenarios,
    get_scenario,
    load_instance_catalog,
    parse_bytes,
    validation_status,
    DEFAULT_INSTANCE_CEILING,
)

STATIC = Path(__file__).parent / "static"
TEMPLATE = STATIC / "calculator.html"
EVALUATE_JS = STATIC / "evaluate.js"

FAMILY_STRIP = """<div class="family">
    <span>xycalc · a swamplink research property</span>
    <a href="https://swamplink.com/">swamplink.com</a>
    <a href="https://wings.swamplink.com/">wings — the chicken one</a>
    <a href="https://foundation.swamplink.com/">foundation</a>
    <a href="https://swamplink.com/data/plates/">plates — the surveillance one</a>
    <a href="https://swamplink.com/data/trust/">ai trust</a>
    <a href="https://swamplink.com/data/policy/">data policy</a>
    <a href="https://blog.swamplink.com/">the blog</a>
  </div>"""

# The ladder golden vectors are drawn from. Four magnitudes rather than one,
# because the interesting arithmetic is at the ends: `floor_at` binds only on
# small instances, and it binds silently -- a vector set clustered around
# "realistic" sizes would exercise none of it.
BYTE_LADDER = [1e8, 1e10, 5e11, 1e13]
SCALAR_LADDER = [1.0, 10.0, 100.0, 1000.0]

# Fixed inputs for the JS chain_evaluate self-check. Same set the live GUI
# prefills for MongoDB instance sizing, so a divergence shows up on the
# default path rather than a synthetic corner.
SCENARIO_GOLDEN_INPUTS = {
    "baseline_vuln_count": "250000",
    "baseline_storage_size": "100GB",
    "target_vuln_count": "280000",
    "index_size": "40GB",
    "snapshot_search_size": "80GB",
}


class ExportError(Exception):
    pass


def _term_dict(t) -> dict:
    return {
        "key": t.key,
        "label": t.label,
        "role": t.role,
        "apply": t.apply,
        "input_key": t.input_key,
        "optional": t.optional,
        "when_input": t.when_input,
        "unless_input": t.unless_input,
        "rationale": t.rationale,
        "coefficient": t.coefficient,
        "coeff_lo": t.coeff_lo,
        "coeff_mode": t.coeff_mode,
        "coeff_hi": t.coeff_hi,
        "unit": t.unit,
        "confidence": t.confidence,
        "applies_to": t.applies_to,
        "source": t.source,
        "source_title": t.source_title,
        "source_url": t.source_url,
        "quote": t.quote,
    }


def _model_dict(conn: sqlite3.Connection, slug: str) -> dict:
    m = Model.load(conn, slug)
    return {
        "slug": m.slug,
        "question": m.question,
        "system": m.system,
        "summary": m.summary,
        "reframe": m.reframe,
        "notes": m.notes,
        "output_unit": m.output_unit,
        "output_parameter": m.output_parameter,
        "inputs": [
            {
                "key": i["key"],
                "label": i["label"],
                "unit": i["unit"],
                "required": bool(i["required"]),
                "default_value": i["default_value"],
                "help": i["help"],
            }
            for i in m.inputs
        ],
        "terms": [_term_dict(t) for t in m.terms],
        "validation": validation_status(conn, slug),
    }


def golden_vectors(conn: sqlite3.Connection, slug: str) -> list[dict]:
    """What Python gets for a fixed set of inputs, for the JS to be held to.

    Two vectors per rung: one supplying every declared input, one supplying only
    the required ones. The second is not padding -- an optional input left out
    takes a different branch (the step is recorded as skipped rather than
    dropped), and that branch is exactly the sort of thing a second
    implementation gets subtly wrong.
    """
    m = Model.load(conn, slug)
    out: list[dict] = []
    for rung in range(len(BYTE_LADDER)):
        for required_only in (False, True):
            values = {}
            for spec in m.inputs:
                if required_only and not spec["required"]:
                    continue
                ladder = BYTE_LADDER if spec["unit"] == "bytes" else SCALAR_LADDER
                values[spec["key"]] = ladder[rung]
            try:
                r = m.evaluate(values)
            except ModelError:
                # A rung a model legitimately refuses -- a zero divisor, a
                # missing required input -- is not a vector. Recording the
                # refusal would pin the two implementations to identical error
                # *text*, which is not a promise worth making.
                continue
            out.append(
                {
                    "model": slug,
                    "inputs": values,
                    "lo": r.lo,
                    "mode": r.mode,
                    "hi": r.hi,
                    "contributions": [s.contribution for s in r.steps],
                }
            )
    return out


def scenario_golden_vectors(conn: sqlite3.Connection) -> list[dict]:
    """What Python's chain_evaluate produced, for the JS port to be held to."""
    out = []
    for listed in describe_scenarios(conn):
        if listed.get("disabled") or listed["slug"] != "mongodb.size-to-instance":
            continue
        steps = chain_evaluate(conn, get_scenario(listed["slug"]), SCENARIO_GOLDEN_INPUTS)
        packed = []
        for st in steps:
            item = {"kind": st.kind, "slug": st.slug}
            if st.result is not None:
                item["lo"] = st.result.lo
                item["mode"] = st.result.mode
                item["hi"] = st.result.hi
            if st.instance_pick:
                mode = st.instance_pick.get("pick_mode")
                item["pick_mode"] = None if mode is None else mode.name
            if st.gp3_spec:
                item["volume_gib"] = st.gp3_spec["volume_gib"]
                item["baseline_iops"] = st.gp3_spec["baseline_iops"]
            packed.append(item)
        out.append(
            {
                "scenario": listed["slug"],
                "inputs": SCENARIO_GOLDEN_INPUTS,
                "steps": packed,
            }
        )
    return out


def corpus_blob(conn: sqlite3.Connection) -> dict:
    slugs = Model.all(conn)
    models = [_model_dict(conn, s) for s in slugs]
    golden: list[dict] = []
    for s in slugs:
        golden.extend(golden_vectors(conn, s))
    if not golden:
        raise ExportError(
            "no golden vectors could be generated — the export refuses to ship "
            "a page whose arithmetic nothing checks"
        )
    blob = {
        "xycalc_version": __version__,
        "xycalc_git": git_identity(),
        "models": models,
        "golden": golden,
        "scenarios": describe_scenarios(conn),
        "instance_catalog": [
            {
                "name": i.name,
                "ram_bytes": i.ram_bytes,
                "vcpu": i.vcpu,
                "ebs_bandwidth_gbps": i.ebs_bandwidth_gbps,
                "source_title": i.source_title,
                "source_url": i.source_url,
            }
            for i in load_instance_catalog(conn)
        ],
        "coefficient_mode": {
            row[0]: row[1]
            for row in conn.execute("SELECT slug, value_mode FROM coefficient")
        },
        "default_instance_ceiling_bytes": parse_bytes(DEFAULT_INSTANCE_CEILING),
        "scenario_golden": scenario_golden_vectors(conn),
    }
    # A short digest of the corpus itself (not the vectors), so a reader can
    # tell two exported pages apart without diffing 100 KB of JSON.
    payload = json.dumps(models, sort_keys=True, separators=(",", ":"))
    blob["corpus_digest"] = hashlib.sha256(payload.encode()).hexdigest()[:12]
    return blob


def render(blob: dict, crumb: str | None = None) -> str:
    template = TEMPLATE.read_text(encoding="utf-8")
    js = EVALUATE_JS.read_text(encoding="utf-8")

    payload = json.dumps(blob, sort_keys=True, separators=(",", ":"), allow_nan=False)
    # `</script>` inside a JSON string would end the block early and hand the
    # rest of the corpus to the HTML parser. Nothing in the corpus contains one
    # today; a quote from an HTML page one day will.
    payload = payload.replace("</", "<\\/")

    html = template.replace("/*__XYCALC_CORPUS_JSON__*/", payload)
    html = html.replace("/*__XYCALC_EVALUATE_JS__*/", js)
    html = html.replace("<!--__XYCALC_CRUMB__-->", crumb or "")
    html = html.replace("<!--__XYCALC_FAMILY_STRIP__-->", FAMILY_STRIP)
    for marker in (
        "__XYCALC_CORPUS_JSON__",
        "__XYCALC_EVALUATE_JS__",
        "__XYCALC_CRUMB__",
        "__XYCALC_FAMILY_STRIP__",
    ):
        if marker in html:
            raise ExportError(f"template placeholder {marker} was not substituted")
    # LF, always. Git hands a Windows checkout CRLF template sources, so
    # without this the same corpus exports to a different file on a different
    # machine -- and the artifact is committed to the site repo, where that
    # shows up as a whole-file diff saying nothing changed. "Deterministic"
    # has to mean across machines or it means very little.
    return html.replace("\r\n", "\n")


def export(
    out: Path, db: Path | None = None, crumb: str | None = None
) -> Path:
    conn = connect(db)
    blob = corpus_blob(conn)
    html = render(blob, crumb=crumb)
    target = out if out.suffix == ".html" else out / "index.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    # newline="" so a Windows checkout does not write CRLF and change the byte
    # count of a file whose whole point is being reproducible.
    target.write_text(html, encoding="utf-8", newline="")
    return target


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="xycalc export",
        description="write the calculator as one self-contained HTML file",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=Path("dist/xycalc.html"),
        help="file to write, or a directory to write index.html into "
        "(default: dist/xycalc.html)",
    )
    p.add_argument("--db", type=Path, default=None, help="corpus path override")
    p.add_argument(
        "--crumb",
        default=None,
        help="raw HTML inserted above the header, for embedding the page in a "
        "site that wants its own breadcrumb back out",
    )
    args = p.parse_args(argv)
    try:
        target = export(args.out, db=args.db, crumb=args.crumb)
    except ExportError as e:
        print(f"export failed: {e}", file=sys.stderr)
        return 1
    size = target.stat().st_size
    print(f"wrote {target} ({size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
