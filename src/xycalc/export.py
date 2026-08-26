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

Sidecars next to the HTML (`stamp.html`, and `og.png` when Bill's approved
still is present) are for the landing page. They are not spliced into the
calculator file. Export copies `static/landing-still.png` to `og.png`; it does
not generate a substitute hero if that file is missing.
"""

from __future__ import annotations

import argparse
import hashlib
import html as html_lib
import json
import shutil
import sqlite3
import sys
from pathlib import Path

from .db import connect
from .version import git_identity, package_version
from .model import (
    Model,
    ModelError,
    chain_evaluate,
    describe_scenarios,
    format_quantity,
    get_scenario,
    load_instance_catalog,
    parse_bytes,
    validation_status,
    lab_status,
    DEFAULT_INSTANCE_CEILING,
)

STATIC = Path(__file__).parent / "static"
TEMPLATE = STATIC / "calculator.html"
EVALUATE_JS = STATIC / "evaluate.js"
APP_JS = STATIC / "app.js"
# Bill's approved landing still. Export copies this file to og.png.
LANDING_STILL = STATIC / "landing-still.png"

FAMILY_STRIP = """<nav class="family" aria-label="swamplink properties">
    <span>xycalc · a swamplink research property</span><span class="sep" aria-hidden="true">·</span>
    <a href="https://swamplink.com/">swamplink.com</a><span class="sep" aria-hidden="true">·</span>
    <a href="https://wings.swamplink.com/">wings</a><span class="sep" aria-hidden="true">·</span>
    <a href="https://foundation.swamplink.com/">foundation</a><span class="sep" aria-hidden="true">·</span>
    <a href="https://swamplink.com/data/plates/">plates</a><span class="sep" aria-hidden="true">·</span>
    <a href="https://swamplink.com/data/trust/">ai trust</a><span class="sep" aria-hidden="true">·</span>
    <a href="https://swamplink.com/data/policy/">data policy</a><span class="sep" aria-hidden="true">·</span>
    <a href="https://blog.swamplink.com/">the blog</a>
  </nav>"""

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
    "foreign_collections_size": "80GB",
}

# Simple-view homepage question (issue #114): today's 500 GB footprint, no
# demo index/foreign pads, target == baseline. Must name a cited SKU at
# every band-end — this is the example the landing page advertises.
HOMEPAGE_SIMPLE_INPUTS = {
    "baseline_vuln_count": "250000",
    "baseline_storage_size": "500GB",
    "target_vuln_count": "250000",
}


class ExportError(Exception):
    pass


# The calculator banners these with human labels. A new grade that is not in
# this set used to render as "undefined —" in the page's most important strip.
KNOWN_VALIDATION_GRADES = frozenset({"none", "thin", "reasonable"})


def _term_dict(t) -> dict:
    return {
        "key": t.key,
        "label": t.label,
        "role": t.role,
        "apply": t.apply,
        "input_key": t.input_key,
        "input_key_b": t.input_key_b,
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
    validation = validation_status(conn, slug)
    grade = validation.get("grade")
    if grade not in KNOWN_VALIDATION_GRADES:
        raise ExportError(
            f"{slug}: validation grade {grade!r} is not in "
            f"{sorted(KNOWN_VALIDATION_GRADES)}; the calculator has no label "
            "for it and would render 'undefined —'"
        )
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
        "validation": validation,
        "lab": lab_status(conn, slug),
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


def browser_string_path_vector(conn: sqlite3.Connection) -> dict:
    """The path the calculator actually sends: a formatted display string.

    Ladder vectors carry Python numbers, which is why scrub-commit's
    '3,000 iops' → parseFloat → 3 never showed up as a golden failure.
    """
    slug = "ebs.iops-to-provision"
    m = Model.load(conn, slug)
    displayed = format_quantity(4000, "iops")
    r = m.evaluate({"average_iops": displayed})
    return {
        "model": slug,
        "inputs": {"average_iops": displayed},
        "lo": r.lo,
        "mode": r.mode,
        "hi": r.hi,
        "contributions": [s.contribution for s in r.steps],
    }


def _pack_scenario_golden_steps(steps) -> list[dict]:
    packed = []
    for st in steps:
        item = {"kind": st.kind, "slug": st.slug}
        if st.result is not None:
            item["lo"] = st.result.lo
            item["mode"] = st.result.mode
            item["hi"] = st.result.hi
        if st.instance_pick:
            for key in ("pick_lo", "pick_mode", "pick_hi"):
                spec = st.instance_pick.get(key)
                item[key] = None if spec is None else spec.name
            if st.family:
                item["family"] = st.family
        if st.gp3_spec:
            item["volume_gib"] = st.gp3_spec["volume_gib"]
            item["baseline_iops"] = st.gp3_spec["baseline_iops"]
        packed.append(item)
    return packed


def scenario_golden_vectors(conn: sqlite3.Connection) -> list[dict]:
    """What Python's chain_evaluate produced, for the JS port to be held to."""
    out = []
    for listed in describe_scenarios(conn):
        if listed.get("disabled") or listed["slug"] != "mongodb.size-to-instance":
            continue
        scenario = get_scenario(listed["slug"])
        for inputs in (SCENARIO_GOLDEN_INPUTS, HOMEPAGE_SIMPLE_INPUTS):
            steps = chain_evaluate(conn, scenario, inputs)
            out.append(
                {
                    "scenario": listed["slug"],
                    "inputs": inputs,
                    "steps": _pack_scenario_golden_steps(steps),
                }
            )
    return out


def _coeff_row(conn: sqlite3.Connection, slug: str) -> dict | None:
    row = conn.execute(
        """
        SELECT c.slug, c.value_mode, c.applies_to, c.quote, c.notes,
               s.slug AS source, s.title AS source_title, s.url AS source_url
          FROM coefficient c
          JOIN source s ON s.id = c.source_id
         WHERE c.slug = ?
        """,
        (slug,),
    ).fetchone()
    if row is None:
        return None
    return {
        "slug": row["slug"],
        "value": row["value_mode"],
        "applies_to": row["applies_to"],
        "quote": row["quote"],
        "notes": row["notes"],
        "source": row["source"],
        "source_title": row["source_title"],
        "source_url": row["source_url"],
    }


def _obs_value(conn: sqlite3.Connection, slug: str) -> float | None:
    row = conn.execute(
        "SELECT value FROM observation WHERE slug = ?", (slug,)
    ).fetchone()
    return None if row is None else float(row["value"])


def _display_number(value: float, places: int | None = None) -> str:
    """Format a cited figure for interpolated guide prose."""
    if places is not None:
        text = f"{value:.{places}f}"
    elif float(value).is_integer():
        text = str(int(value))
    else:
        text = str(value)
    if text.startswith("-"):
        return "−" + text[1:]
    return text


def _is_obs_ref(node: object) -> bool:
    return isinstance(node, dict) and "observation" in node


def _is_coeff_ref(node: object) -> bool:
    return isinstance(node, dict) and "coefficient" in node


def _resolve_obs(conn: sqlite3.Connection, ref: dict, parent: dict | None) -> float | None:
    slug = ref["observation"]
    value = _obs_value(conn, slug)
    as_key = ref.get("as")
    if as_key and parent is not None:
        parent[as_key] = None if value is None else slug
    return value


def _render_row(conn: sqlite3.Connection, spec: dict) -> dict:
    out: dict = {}
    for key, val in spec.items():
        if _is_obs_ref(val):
            out[key] = _resolve_obs(conn, val, out)
        elif _is_coeff_ref(val):
            out[key] = _coeff_row(conn, val["coefficient"])
        else:
            out[key] = render_guide(conn, val)
    return out


def _row_complete(row: dict, required: list[str]) -> bool:
    return all(row.get(key) is not None for key in required)


def _apply_derive(row: dict, rules: list[dict]) -> None:
    for rule in rules:
        op = rule.get("op")
        key = rule["key"]
        if op == "percent_delta":
            num = row.get(rule["num"])
            den = row.get(rule["den"])
            if num is None or not den:
                row[key] = None
                continue
            delta = ((num - den) / den) * 100.0
            places = rule.get("round")
            row[key] = round(delta, places) if places is not None else delta
        else:
            raise ExportError(f"unknown guide derive op {op!r}")


def _apply_relative(rows: list[dict], rules: list[dict]) -> None:
    for rule in rules:
        field = rule["from"]
        key = rule["key"]
        places = rule.get("round", 4)
        base = next((r.get(field) for r in rows if r.get(field) not in (None, 0)), None)
        for row in rows:
            value = row.get(field)
            if value is None or base in (None, 0):
                row[key] = None
            else:
                row[key] = round(value / base, places)


def _render_table(conn: sqlite3.Connection, spec: dict) -> list[dict]:
    required = spec.get("require") or []
    derive = spec.get("derive") or []
    out: list[dict] = []
    for raw in spec.get("rows") or []:
        row = _render_row(conn, raw)
        if required and not _row_complete(row, required):
            continue
        _apply_derive(row, derive)
        out.append(row)
    return out


def _render_series(conn: sqlite3.Connection, spec: dict) -> list[dict]:
    rows = _render_table(conn, spec)
    _apply_relative(rows, spec.get("relative") or [])
    return rows


def _render_format(conn: sqlite3.Connection, spec: dict) -> str:
    values = {}
    for name, ref in (spec.get("values") or {}).items():
        if not _is_obs_ref(ref) and not _is_coeff_ref(ref):
            values[name] = ref
            continue
        if _is_coeff_ref(ref):
            row = _coeff_row(conn, ref["coefficient"])
            number = None if row is None else row["value"]
        else:
            number = _obs_value(conn, ref["observation"])
        if number is None:
            values[name] = ""
        else:
            values[name] = _display_number(float(number), ref.get("places"))
    try:
        return spec["template"].format(**values)
    except KeyError as e:
        raise ExportError(f"guide format missing value {e}") from e


def render_guide(conn: sqlite3.Connection, spec: object):
    """Resolve one guide spec: observation/coefficient refs, tables, series.

    The YAML under data/guides/ is the structure; this walker is the only
    Python that should grow when a new investigation adds a tab — and it
    should not, if that tab fits table/series/format.
    """
    if isinstance(spec, dict):
        kind = spec.get("kind")
        if kind == "table":
            return _render_table(conn, spec)
        if kind == "series":
            return _render_series(conn, spec)
        if kind == "format":
            return _render_format(conn, spec)
        if _is_obs_ref(spec):
            return _resolve_obs(conn, spec, None)
        if _is_coeff_ref(spec):
            return _coeff_row(conn, spec["coefficient"])
        return _render_row(conn, spec)
    if isinstance(spec, list):
        return [render_guide(conn, item) for item in spec]
    return spec


def render_guides(conn: sqlite3.Connection) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in conn.execute("SELECT slug, spec_json FROM guide ORDER BY slug"):
        spec = json.loads(row["spec_json"])
        rendered = render_guide(conn, spec)
        if not isinstance(rendered, dict):
            raise ExportError(f"guide '{row['slug']}' did not render to an object")
        out[row["slug"]] = rendered
    return out


def _instance_catalog_dicts(conn: sqlite3.Connection, system: str = "aws-ec2") -> list[dict]:
    return [
        {
            "name": i.name,
            "ram_bytes": i.ram_bytes,
            "vcpu": i.vcpu,
            "ebs_bandwidth_gbps": i.ebs_bandwidth_gbps,
            "source_title": i.source_title,
            "source_url": i.source_url,
        }
        for i in load_instance_catalog(conn, system)
    ]


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
    golden.append(browser_string_path_vector(conn))
    blob = {
        "xycalc_version": package_version(),
        "xycalc_git": git_identity(),
        "models": models,
        "golden": golden,
        "scenarios": describe_scenarios(conn),
        "instance_catalog": _instance_catalog_dicts(conn, "aws-ec2"),
        "instance_catalogs": {
            "aws-ec2": _instance_catalog_dicts(conn, "aws-ec2"),
            "azure-vm": _instance_catalog_dicts(conn, "azure-vm"),
        },
        "coefficient_mode": {
            row[0]: row[1]
            for row in conn.execute("SELECT slug, value_mode FROM coefficient")
        },
        "default_instance_ceiling_bytes": parse_bytes(DEFAULT_INSTANCE_CEILING),
        "scenario_golden": scenario_golden_vectors(conn),
        "grafana_public_base": "https://grafana.swamplink.com",
    }
    blob.update(render_guides(conn))
    # A short digest of the corpus itself (not the vectors), so a reader can
    # tell two exported pages apart without diffing 100 KB of JSON.
    payload = json.dumps(models, sort_keys=True, separators=(",", ":"))
    blob["corpus_digest"] = hashlib.sha256(payload.encode()).hexdigest()[:12]
    return blob


def render(blob: dict, crumb: str | None = None) -> str:
    template = TEMPLATE.read_text(encoding="utf-8")
    js = EVALUATE_JS.read_text(encoding="utf-8")
    app_js = APP_JS.read_text(encoding="utf-8")

    payload = json.dumps(blob, sort_keys=True, separators=(",", ":"), allow_nan=False)
    # `</script>` inside a JSON string would end the block early and hand the
    # rest of the corpus to the HTML parser. Nothing in the corpus contains one
    # today; a quote from an HTML page one day will.
    payload = payload.replace("</", "<\\/")

    html = template.replace("/*__XYCALC_CORPUS_JSON__*/", payload)
    html = html.replace("/*__XYCALC_EVALUATE_JS__*/", js)
    html = html.replace("/*__XYCALC_APP_JS__*/", app_js)
    html = html.replace("<!--__XYCALC_CRUMB__-->", crumb or "")
    html = html.replace("<!--__XYCALC_FAMILY_STRIP__-->", FAMILY_STRIP)
    for marker in (
        "__XYCALC_CORPUS_JSON__",
        "__XYCALC_EVALUATE_JS__",
        "__XYCALC_APP_JS__",
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


def provenance_line(blob: dict) -> str:
    """The calculator footer string. Stamp HTML uses this verbatim."""
    return (
        f"{len(blob['models'])} models · corpus {blob['corpus_digest']} · "
        f"exported by xycalc {blob['xycalc_version']} · {blob['xycalc_git']}"
    )


def _validated_cell(status: dict) -> str:
    """Table cell: grade + n, without the audit essay."""
    if status.get("grade") == "none" or not status.get("cases"):
        return "unvalidated (n=0)"
    text = status.get("text") or ""
    return text.split(" — ")[0]


def render_lab_table_html(conn: sqlite3.Connection) -> str:
    """Landing snippet: validated | measured | still needs. Not FINDINGS."""
    rows: list[str] = []
    for extra in conn.execute(
        "SELECT slug, kind, label, blurb, href, validated, measured, still_needs "
        "FROM lab_landing ORDER BY sequence, slug"
    ):
        slug, kind, label, blurb, href, validated, measured, still = extra
        sub = html_lib.escape(blurb or kind)
        rows.append(_lab_tr(href, label, sub, validated, measured, still))
    for slug, label, question, measured, still in conn.execute(
        "SELECT m.slug, l.label, m.question, l.measured, l.still_needs "
        "FROM lab l JOIN model m ON m.id = l.model_id "
        "ORDER BY l.sequence, m.slug"
    ):
        href = f"/tools/xycalc/calculator/#tab=single&model={slug}"
        status = validation_status(conn, slug)
        rows.append(
            _lab_tr(
                href,
                label,
                question,
                _validated_cell(status),
                measured,
                still,
            )
        )
    body = "\n".join(rows)
    return (
        "<!-- generated by xycalc export; do not edit -->\n"
        '<table class="xycalc-lab">\n'
        "<thead><tr>"
        "<th>Model</th><th>Validated</th>"
        "<th>Measured</th><th>Still needs a case</th>"
        "</tr></thead>\n"
        f"<tbody>\n{body}\n</tbody>\n</table>\n"
    )


def _lab_tr(
    href: str,
    label: str,
    blurb: str,
    validated: str,
    measured: str,
    still: str,
) -> str:
    return (
        "<tr>"
        f'<td><strong><a class="model" href="{html_lib.escape(href, quote=True)}">'
        f"{html_lib.escape(label)}</a></strong><br>"
        f"{html_lib.escape(blurb)}</td>"
        f'<td class="state">{html_lib.escape(validated)}</td>'
        f"<td>{html_lib.escape(measured)}</td>"
        f"<td>{html_lib.escape(still)}</td>"
        "</tr>"
    )


def render_stamp_html(blob: dict) -> str:
    """Snippet the landing page can include. Same fields as the footer."""
    line = provenance_line(blob)
    n = str(len(blob["models"]))
    digest = html_lib.escape(str(blob["corpus_digest"]), quote=True)
    ver = html_lib.escape(str(blob["xycalc_version"]), quote=True)
    git = html_lib.escape(str(blob["xycalc_git"]), quote=True)
    text = html_lib.escape(line)
    return (
        "<!-- generated by xycalc export; do not edit -->\n"
        f'<p class="xycalc-stamp" data-models="{n}" '
        f'data-corpus-digest="{digest}" data-xycalc-version="{ver}" '
        f'data-xycalc-git="{git}">{text}</p>\n'
    )


def copy_landing_still(dest: Path) -> Path | None:
    """Copy Bill's approved still to og.png. None if the source file is absent."""
    if not LANDING_STILL.is_file():
        print(
            "landing still missing: add Bill's approved PNG at "
            f"{LANDING_STILL} — not generating a substitute hero",
            file=sys.stderr,
        )
        if dest.exists():
            dest.unlink()
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(LANDING_STILL, dest)
    return dest


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
    stamp = target.parent / "stamp.html"
    stamp.write_text(render_stamp_html(blob), encoding="utf-8", newline="")
    lab = target.parent / "lab-table.html"
    lab.write_text(render_lab_table_html(conn), encoding="utf-8", newline="")
    copy_landing_still(target.parent / "og.png")
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
        "(default: dist/xycalc.html). Also writes stamp.html alongside it, "
        "and og.png when static/landing-still.png is present (Bill's "
        "approved still — export does not generate a substitute).",
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
    stamp = target.parent / "stamp.html"
    if stamp.is_file():
        print(f"wrote {stamp} ({stamp.stat().st_size:,} bytes)")
    lab = target.parent / "lab-table.html"
    if lab.is_file():
        print(f"wrote {lab} ({lab.stat().st_size:,} bytes)")
    og = target.parent / "og.png"
    if og.is_file():
        print(f"wrote {og} ({og.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
