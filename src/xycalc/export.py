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
import re
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
APP_JS = STATIC / "app.js"

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
    "foreign_collections_size": "80GB",
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


def _obs_notes(conn: sqlite3.Connection, slug: str) -> str | None:
    row = conn.execute(
        "SELECT notes FROM observation WHERE slug = ?", (slug,)
    ).fetchone()
    if row is None or row["notes"] is None:
        return None
    return str(row["notes"])


def _latency_ms_from_notes(notes: str | None) -> float | None:
    """Pull 'Mean latency 9.19ms' out of ticket-probe observation notes."""
    if not notes:
        return None
    m = re.search(r"Mean latency\s+([\d.]+)\s*ms", notes, re.IGNORECASE)
    return None if m is None else float(m.group(1))


def _ratio_tag(ratio: float) -> str:
    """Encode 0.5 → 0p5, 1.0 → 1, 1.2 → 1p2 for observation slugs."""
    if float(ratio).is_integer():
        return str(int(ratio))
    return str(ratio).replace(".", "p")


def cache_cliff_guide(conn: sqlite3.Connection) -> dict:
    """Measured oversubscription shape for the Cache cliff tab (inv 006 / T1).

    Relative ops (normalised to the 0.5× leg) are the transferable claim;
    absolute ops/s stay in the table as throttle artifacts. No wt-cache
    sizing coefficient is derived here — relative ops under throttle ≠ hit
    ratio.
    """
    ratios = (0.5, 0.8, 1.0, 1.2, 1.5, 2.0, 4.0, 8.0, 50.0)
    legs: list[dict] = []
    for ratio in ratios:
        tag = _ratio_tag(ratio)
        ops = _obs_value(conn, f"swamplink-2026-08-21-cliff-a1r1-ops-{tag}")
        pages = _obs_value(conn, f"swamplink-2026-08-21-cliff-a1r1-pages-{tag}")
        ops_r2 = _obs_value(conn, f"swamplink-2026-08-21-cliff-a1r2-ops-{tag}")
        if ops is None:
            continue
        legs.append(
            {
                "ratio": ratio,
                "ops": ops,
                "pages_per_op": pages,
                "ops_r2": ops_r2,
                "ops_slug": f"swamplink-2026-08-21-cliff-a1r1-ops-{tag}",
                "pages_slug": (
                    None
                    if pages is None
                    else f"swamplink-2026-08-21-cliff-a1r1-pages-{tag}"
                ),
                "ops_r2_slug": (
                    None
                    if ops_r2 is None
                    else f"swamplink-2026-08-21-cliff-a1r2-ops-{tag}"
                ),
            }
        )
    base = legs[0]["ops"] if legs else None
    for leg in legs:
        leg["relative_ops"] = (
            None if base in (None, 0) else round(leg["ops"] / base, 4)
        )
        if leg["ops_r2"] is not None and base not in (None, 0):
            # r2 relative uses r2's own 0.5× when present, else r1 base.
            r2_base = next(
                (x["ops_r2"] for x in legs if x["ratio"] == 0.5 and x["ops_r2"]),
                None,
            )
            if r2_base:
                leg["relative_ops_r2"] = round(leg["ops_r2"] / r2_base, 4)
            else:
                leg["relative_ops_r2"] = None
        else:
            leg["relative_ops_r2"] = None
    a2_ratios = (0.5, 0.8, 1.0, 1.2, 1.5, 2.0)
    a2_legs: list[dict] = []
    for ratio in a2_ratios:
        tag = _ratio_tag(ratio)
        ops = _obs_value(conn, f"swamplink-2026-08-21-cliff-a2-ops-{tag}")
        if ops is None:
            continue
        a2_legs.append({"ratio": ratio, "ops": ops,
                        "ops_slug": f"swamplink-2026-08-21-cliff-a2-ops-{tag}"})
    a2_base = a2_legs[0]["ops"] if a2_legs else None
    for leg in a2_legs:
        leg["relative_ops"] = (
            None if a2_base in (None, 0) else round(leg["ops"] / a2_base, 4)
        )
    return {
        "model": "mongodb.wt-cache",
        "source": "obs-mongodb-cache-cliff-swamplink-2026-08-21",
        "investigation": "006-cache-cliff",
        "status": "measured",
        "wt_cache_gb": 0.25,
        "steepest_segment": [0.8, 1.0],
        "transfer": (
            "A2 (1.0 GB cache, knee 0.5…2.0) confirms same steepest segment "
            "0.8→1.0 (slope ≈ −3.8); far oversub still A1-only"
        ),
        "legs": legs,
        "a2_legs": a2_legs,
        "verdict": (
            "Throughput vs oversubscription is not a flat plateau then a cliff "
            "at 1.0×. Relative ops/s falls hard already between 0.5× and 1.0× "
            "(steepest log–log segment 0.8→1.0 on A1-r1, A1-r2, and A2), then "
            "the decline flattens into a shallow slope through 50×. Absolute "
            "ops/s are throttle artifacts; the shape is the claim. Do not treat "
            "cache-resident as cache == dataSize. No wt-cache sizing "
            "coefficient — relative ops under throttle are not a hit-ratio."
        ),
    }


def occupancy_band_guide(conn: sqlite3.Connection) -> dict:
    """Structured 007 ladder + measured 80→90 legs for the Occupancy tab.

    Numbers come from coefficients and observations already in the corpus —
    the page must not invent a second copy of the findings table.
    """
    target = _coeff_row(conn, "mongodb.eviction-target-pct")
    trigger = _coeff_row(conn, "mongodb.eviction-trigger-pct")
    dirty_target = _coeff_row(conn, "mongodb.eviction-dirty-target-pct")
    dirty_trigger = _coeff_row(conn, "mongodb.eviction-dirty-trigger-pct")
    passes = []
    for label, suffix in (
        ("smoke 12 s", ""),
        ("confirm 25 s #1", "-confirm1"),
        ("confirm 25 s #2", "-confirm2"),
    ):
        ops80 = _obs_value(conn, f"swamplink-2026-08-21-occ80-ops{suffix}")
        ops90 = _obs_value(conn, f"swamplink-2026-08-21-occ90-ops{suffix}")
        occ80 = _obs_value(conn, f"swamplink-2026-08-21-occ80-occupancy{suffix}")
        occ90 = _obs_value(conn, f"swamplink-2026-08-21-occ90-occupancy{suffix}")
        if None in (ops80, ops90, occ80, occ90):
            continue
        delta_pct = ((ops90 - ops80) / ops80) * 100.0 if ops80 else None
        passes.append(
            {
                "label": label,
                "ops_at_80": ops80,
                "ops_at_90": ops90,
                "ops_delta_pct": None if delta_pct is None else round(delta_pct, 2),
                "occ_mean_at_80": occ80,
                "occ_mean_at_90": occ90,
                "ops_80_slug": f"swamplink-2026-08-21-occ80-ops{suffix}",
                "ops_90_slug": f"swamplink-2026-08-21-occ90-ops{suffix}",
                "occ_80_slug": f"swamplink-2026-08-21-occ80-occupancy{suffix}",
                "occ_90_slug": f"swamplink-2026-08-21-occ90-occupancy{suffix}",
            }
        )
    reef = _obs_value(conn, "reef-mongo-bench-2026-08-19-eviction-target-actual")

    ticket_rows = []
    for concurrency in (1, 8, 64):
        tickets = _obs_value(
            conn, f"swamplink-2026-08-01-tickets-c{concurrency}"
        )
        ops = _obs_value(conn, f"swamplink-2026-08-01-opsec-c{concurrency}")
        if tickets is None or ops is None:
            continue
        ops_notes = _obs_notes(conn, f"swamplink-2026-08-01-opsec-c{concurrency}")
        ticket_rows.append(
            {
                "concurrency": concurrency,
                "peak_tickets": tickets,
                "ops_per_s": ops,
                "latency_ms": _latency_ms_from_notes(ops_notes),
                "tickets_slug": f"swamplink-2026-08-01-tickets-c{concurrency}",
                "ops_slug": f"swamplink-2026-08-01-opsec-c{concurrency}",
            }
        )

    knobs = [
        {
            "key": "eviction_target",
            "coeff": target,
            "blurb": "Occupancy WiredTiger works to hold",
            "example": (
                "Size cache ≈ working_set ÷ 0.8 so workers are not always "
                "fighting. Reef saturated scan settled at the default hold."
            ),
        },
        {
            "key": "eviction_trigger",
            "coeff": trigger,
            "blurb": "App threads start eviction",
            "example": (
                "Diagnose latency with pages evicted by application threads, "
                "not RSS alone."
            ),
        },
        {
            "key": "eviction_dirty_target",
            "coeff": dirty_target,
            "blurb": "Dirty-page hold",
            "example": "Write path analogue of eviction_target.",
        },
        {
            "key": "eviction_dirty_trigger",
            "coeff": dirty_trigger,
            "blurb": "Writers stall on dirty eviction",
            "example": (
                "A bulk load can hit 20% dirty while total occupancy is still "
                "low — total-bytes sizing will not catch it."
            ),
        },
    ]

    return {
        "model": "mongodb.wt-cache",
        "ticket_model": "mongodb.ticket-throughput-ceiling",
        "source": "obs-mongodb-occupancy-band-swamplink-2026-08-21",
        "ticket_source": "obs-mongodb-ticket-probe-swamplink-2026-08-01",
        "investigation": "007-eviction-band-and-tickets",
        "ladder": {
            "eviction_target": target,
            "eviction_trigger": trigger,
            "eviction_dirty_target": dirty_target,
            "eviction_dirty_trigger": dirty_trigger,
        },
        "knobs": [k for k in knobs if k["coeff"] is not None],
        "passes": passes,
        "reef_saturated_occupancy_pct": reef,
        "ticket_ladder": ticket_rows,
        "snapshot_recipe": (
            "const s = db.serverStatus();\n"
            "const c = s.wiredTiger.cache;\n"
            "const t = (s.tcmalloc && s.tcmalloc.generic) || {};\n"
            "const max = c['maximum bytes configured'];\n"
            "printjson({\n"
            "  occupancyPct: 100 * c['bytes currently in the cache'] / max,\n"
            "  dirtyPct: 100 * c['tracked dirty bytes in the cache'] / max,\n"
            "  appEvict: c['pages evicted by application threads'],\n"
            "  unable: c['eviction server unable to reach eviction goal'],\n"
            "  tickets: (s.queues && s.queues.execution)\n"
            "    ? s.queues.execution.read.totalTickets\n"
            "    : s.wiredTiger.concurrentTransactions.read.totalTickets,\n"
            "  tcmallocHeap: t.heap_size,\n"
            "  tcmallocAllocated: t.current_allocated_bytes "
            "|| t.total_allocated_bytes\n"
            "});"
        ),
        "playbook": [
            {
                "when": "Occupancy stuck mid-80s + unable to reach eviction goal rising",
                "do": "Danger band before 95%. Check disk and dirty% before touching ticket knobs.",
            },
            {
                "when": "Workers 20/20 and pages evicted by application threads rising",
                "do": "Raise IOPS or shrink the working set — threads_max will not go past 20.",
            },
            {
                "when": "High RSS, healthy occupancy, large tcmalloc heap−allocated gap",
                "do": "Tune tcmallocReleaseRate (not aggressive decommit) — this is fragmentation, not WT occupancy.",
            },
            {
                "when": "Flat ops/s, rising latency, climbing totalTickets on MongoDB 7",
                "do": "Storage admission contention (investigation 003) — fix the device or working set, do not chase ticket count as capacity.",
            },
        ],
        "weakest_inference": (
            "Single host, concurrency 1, 0.25 GB cache, device-throttled. "
            "Ops/s delta for target 80→90 moved from ~0 (12 s) to mid-single-digit "
            "/ low-teens (25 s) — window length matters. Do not promote a "
            "'raise target to 90 for +X% throughput' coefficient."
        ),
        "verdict": (
            "Raising eviction_target 80→90 holds the cache fuller under a "
            "read-miss / throttled workload; ops/s deltas are modest and noisy. "
            "Do not raise production target to 90 for throughput. The documented "
            "danger remains 95% (app-thread eviction) and, on MongoDB 7 under "
            "real concurrency, ticket climb against a saturated device."
        ),
    }


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
    blob = {
        "xycalc_version": __version__,
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
        "occupancy_band": occupancy_band_guide(conn),
        "cache_cliff": cache_cliff_guide(conn),
    }
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
