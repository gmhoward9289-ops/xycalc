"""The static export, and the seam it opens.

Exporting the calculator to a page with no server behind it means the band
arithmetic exists twice: `model.py` in Python, `static/evaluate.js` in
JavaScript. Two implementations of the same numbers is precisely the drift this
corpus exists to refuse, so the interesting test here is not "does the file get
written" — it is `test_javascript_agrees_with_python`, which runs the JS under
node against vectors Python computed and fails on any disagreement.

That test skips when node is absent, which is a real hole: a laptop without node
would report green on a suite that never checked the thing most likely to break.
The exported page closes it from the other side by re-running the same vectors
in the browser and refusing to render a number if they disagree — so a
divergence that slips past CI still cannot reach a reader as a wrong figure.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from xycalc.export import (
    EVALUATE_JS,
    ExportError,
    corpus_blob,
    golden_vectors,
    render,
)
from xycalc.model import Model

NODE = shutil.which("node")


@pytest.fixture
def blob(conn: sqlite3.Connection) -> dict:
    return corpus_blob(conn)


def test_every_model_is_exported(blob, conn):
    assert [m["slug"] for m in blob["models"]] == Model.all(conn)


def test_every_model_carries_its_validation_status(blob):
    # The page cannot render a number without this, by construction. Exporting
    # a model with the field missing would leave the browser free to decide
    # what an absent status means, and the only safe answer -- "unvalidated" --
    # is the one a bug would be least likely to reach.
    for m in blob["models"]:
        assert m["validation"]["grade"] in {"none", "thin", "reasonable"}
        assert m["validation"]["text"]


def test_every_model_has_golden_vectors(blob, conn):
    covered = {g["model"] for g in blob["golden"]}
    assert covered == set(Model.all(conn))


def test_vectors_exercise_the_optional_input_branch(conn):
    """An optional input left out takes a different path through evaluate()
    than one supplied, and a second implementation gets exactly that kind of
    branch subtly wrong. If the ladder ever stops producing both shapes, the
    JS check silently gets weaker rather than failing."""
    vectors = golden_vectors(conn, "mongodb.wt-cache")
    widths = {len(v["inputs"]) for v in vectors}
    assert len(widths) > 1, "no vector omits an optional input"


def test_a_vector_lands_where_the_floor_binds(conn):
    """`floor_at` binds only on small instances, which is where nobody looks.
    The smallest rung of the ladder exists to reach it; this asserts it still
    does rather than trusting that it does."""
    vectors = [v for v in golden_vectors(conn, "mongodb.wt-cache") if v["inputs"]["storage_size"] == 1e8]
    assert vectors
    assert any("≥" in c for v in vectors for c in v["contributions"])


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_javascript_agrees_with_python(blob, tmp_path):
    """The seam. Runs evaluate.js against every vector Python produced.

    Checks the figures AND the contribution strings: "x 2.5 (1.5–3.5)" is what
    a reader compares against the terminal, and a formatter that rounds
    differently on one surface is the same class of bug as arithmetic that
    divides differently — it just looks smaller.
    """
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps(blob), encoding="utf-8")
    script = tmp_path / "check.js"
    script.write_text(
        "const XY = require(process.argv[2]);\n"
        "const corpus = JSON.parse(require('fs').readFileSync(process.argv[3], 'utf8'));\n"
        "const failures = XY.checkGolden(corpus);\n"
        "for (const f of failures) {\n"
        "  console.log(f.vector.model + ' ' + JSON.stringify(f.vector.inputs) + ' :: ' + f.reason);\n"
        "}\n"
        "process.exit(failures.length ? 1 : 0);\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [NODE, str(script), str(EVALUATE_JS.resolve()), str(corpus)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0, (
        "the JavaScript port disagrees with model.py:\n" + proc.stdout + proc.stderr
    )


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_check_can_actually_fail(blob, tmp_path):
    """A gate nobody has watched fire is a gate nobody knows is wired up. Bend
    one vector by a percent and the check must reject it — otherwise the test
    above passes for the wrong reason on the day evaluate.js breaks."""
    bent = json.loads(json.dumps(blob))
    bent["golden"][0]["mode"] *= 1.01
    corpus = tmp_path / "bent.json"
    corpus.write_text(json.dumps(bent), encoding="utf-8")
    script = tmp_path / "check.js"
    script.write_text(
        "const XY = require(process.argv[2]);\n"
        "const corpus = JSON.parse(require('fs').readFileSync(process.argv[3], 'utf8'));\n"
        "process.exit(XY.checkGolden(corpus).length ? 1 : 0);\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [NODE, str(script), str(EVALUATE_JS.resolve()), str(corpus)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1


def test_render_is_deterministic(blob):
    # Same corpus, byte-identical page. An export that differs on every run
    # cannot be diffed, and "what changed" is the whole question when
    # republishing.
    assert render(blob) == render(blob)


def test_render_substitutes_every_placeholder(blob):
    html = render(blob)
    for marker in ("__XYCALC_CORPUS_JSON__", "__XYCALC_EVALUATE_JS__", "__XYCALC_CRUMB__", "__XYCALC_FAMILY_STRIP__"):
        assert marker not in html
    assert "XY.checkGolden" in html, "the page shipped without its self-check"
    assert "XY.chainEvaluate" in html, "the page shipped without scenario arithmetic"
    assert "xycalc · a swamplink research property" in html


def test_export_blob_carries_scenario_chain(blob):
    slugs = {s["slug"] for s in blob["scenarios"]}
    assert "mongodb.size-to-instance" in slugs
    inst = next(s for s in blob["scenarios"] if s["slug"] == "mongodb.size-to-instance")
    assert inst["nvd_chart"]["annual"][0]["count"] == 28818
    assert inst["nvd_chart"]["annual"][2]["microsoft"] == 1255
    assert blob["instance_catalog"]
    assert blob["scenario_golden"]


def test_export_blob_carries_occupancy_band(blob):
    g = blob["occupancy_band"]
    assert g["model"] == "mongodb.wt-cache"
    assert g["ladder"]["eviction_target"]["value"] == 80
    assert g["ladder"]["eviction_trigger"]["value"] == 95
    assert len(g["passes"]) == 3
    assert g["passes"][1]["ops_delta_pct"] == 6.73
    assert g["reef_saturated_occupancy_pct"] == 80.55
    assert len(g["playbook"]) >= 4
    assert "occupancyPct" in g["snapshot_recipe"]
    assert len(g["ticket_ladder"]) == 3
    assert g["ticket_ladder"][0]["concurrency"] == 1
    assert g["ticket_ladder"][0]["peak_tickets"] == 4
    assert g["ticket_ladder"][-1]["peak_tickets"] == 74
    assert g["ticket_ladder"][-1]["latency_ms"] == 535.51
    assert g["weakest_inference"]
    assert any(k["key"] == "eviction_target" for k in g["knobs"])


def test_export_blob_carries_cache_cliff(blob):
    g = blob["cache_cliff"]
    assert g["status"] == "provisional"
    assert g["steepest_segment"] == [0.8, 1.0]
    assert g["wt_cache_gb"] == 0.25
    assert len(g["legs"]) == 9
    by_ratio = {leg["ratio"]: leg for leg in g["legs"]}
    assert by_ratio[0.5]["ops"] == 1590
    assert by_ratio[0.5]["relative_ops"] == 1.0
    assert by_ratio[1.0]["ops"] == 219
    assert by_ratio[1.0]["pages_per_op"] == 0.4
    assert by_ratio[0.8]["ops_r2"] == 520
    assert by_ratio[0.8]["relative_ops_r2"] == round(520 / 2189, 4)


def test_exported_page_has_flow_and_occupancy_tabs(blob):
    html = render(blob)
    assert 'data-tab="flow"' in html
    assert 'data-tab="occupancy"' in html
    assert 'data-tab="cliff"' in html
    assert 'id="tab-flow"' in html
    assert 'id="tab-occupancy"' in html
    assert 'id="tab-cliff"' in html
    assert "Occupancy bands" in html
    assert "Cache cliff" in html
    assert "How it flows" in html
    assert "Scrub the curve" in html
    assert "cache_cliff" in html
    assert "Operator playbook" in html
    assert "Not the same cliff" in html
    assert 'id="occ-dirty"' in html
    assert 'id="occ-recipe"' in html
    assert 'id="occ-tickets"' in html
    assert 'id="occ-playbook"' in html


def test_closing_script_tags_in_the_corpus_cannot_escape(blob):
    """One `</script>` inside a quote hands the rest of the corpus to the HTML
    parser. Nothing in the corpus contains one today; a quote lifted from an
    HTML page one day will."""
    poisoned = json.loads(json.dumps(blob))
    poisoned["models"][0]["summary"] = "</script><script>alert(1)</script>"
    html = render(poisoned)
    assert "</script><script>alert(1)" not in html
    assert "<\\/script>" in html


def test_export_refuses_a_corpus_it_cannot_check(monkeypatch, conn):
    """No vectors means nothing holds the JS to the Python. The export fails
    rather than shipping a page whose arithmetic is unattended."""
    import xycalc.export as export_mod

    monkeypatch.setattr(export_mod, "golden_vectors", lambda *a, **k: [])
    with pytest.raises(ExportError):
        export_mod.corpus_blob(conn)


def test_crumb_is_inserted_verbatim(blob):
    html = render(blob, crumb='<div class="crumb">home / here</div>')
    assert '<div class="crumb">home / here</div>' in html


def test_render_always_emits_lf(blob):
    """The artifact is committed to the site repo. A Windows checkout hands
    Python a CRLF template, and without normalising, re-exporting on a
    different machine produces a whole-file diff that says nothing changed."""
    assert "\r" not in render(blob)


def test_blob_carries_xycalc_version_and_git(monkeypatch, conn):
    monkeypatch.setenv("GITHUB_SHA", "deadbeefcafebabe")
    from xycalc import __version__

    b = corpus_blob(conn)
    assert b["xycalc_version"] == __version__
    assert b["xycalc_git"] == "deadbee"


def test_rendered_page_embeds_git_identity(monkeypatch, conn):
    monkeypatch.setenv("GITHUB_SHA", "feedface00000000")

    b = corpus_blob(conn)
    html = render(b)
    assert b["xycalc_git"] == "feedfac"
    compact = html.replace(" ", "")
    from xycalc import __version__

    assert '"xycalc_git":"feedfac"' in compact
    assert f'"xycalc_version":"{__version__}"' in compact


def test_calculator_template_prints_git_in_provenance():
    from xycalc.export import TEMPLATE

    text = TEMPLATE.read_text(encoding="utf-8")
    assert "CORPUS.xycalc_git" in text
    assert "exported by xycalc" in text
