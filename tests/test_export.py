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
import re
import shutil
import sqlite3
import subprocess
from collections import Counter
from pathlib import Path

import pytest

from xycalc.export import (
    APP_JS,
    EVALUATE_JS,
    TEMPLATE,
    ExportError,
    corpus_blob,
    export,
    golden_vectors,
    provenance_line,
    render,
    render_stamp_html,
)
from xycalc.model import Model

NODE = shutil.which("node")

_HTML_ID = re.compile(r'\bid="([^"]+)"', re.I)
_DOLLAR_ID = re.compile(r'\$\("([^"]+)"\)')
# Nodes Simple injects into the result panel; they are not in the template.
_SIMPLE_INJECTED_IDS = frozenset({"simple-open-scientific"})


def html_id_counts(html: str) -> Counter:
    """Real markup ids only — skip JS template literals like scn-in-${esc(i.key)}."""
    return Counter(i for i in _HTML_ID.findall(html) if "${" not in i)


def html_duplicate_ids(html: str) -> list[str]:
    return sorted(i for i, n in html_id_counts(html).items() if n > 1)


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


def test_a_golden_vector_uses_the_browser_string_path(blob, conn):
    """Existing ladder vectors carry numbers. The page sends formatted strings
    ('4,000 iops'); that path needs its own pin or the comma bug is invisible."""
    from xycalc.model import format_quantity

    displayed = format_quantity(4000, "iops")
    assert "," in displayed
    hits = [
        g
        for g in blob["golden"]
        if g["model"] == "ebs.iops-to-provision"
        and g["inputs"].get("average_iops") == displayed
    ]
    assert hits, f"no string-path vector for {displayed!r}"
    expected = Model.load(conn, "ebs.iops-to-provision").evaluate({"average_iops": 4000})
    assert hits[0]["mode"] == expected.mode


def test_export_refuses_an_unknown_validation_grade(monkeypatch, conn):
    import xycalc.export as export_mod

    real = export_mod.validation_status

    def fake(c, slug):
        d = dict(real(c, slug))
        d["grade"] = "legendary"
        return d

    monkeypatch.setattr(export_mod, "validation_status", fake)
    with pytest.raises(ExportError, match="legendary"):
        export_mod.corpus_blob(conn)


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
def test_js_parse_is_the_inverse_of_format(tmp_path):
    """Same round-trip the Python tests pin, under Node, so a JS-only parse
    regression cannot hide behind numeric golden vectors."""
    script = tmp_path / "roundtrip.js"
    script.write_text(
        "const XY = require(process.argv[2]);\n"
        "const cases = [1, 12, 999, 1000, 3000, 4000, 1280, 1000000];\n"
        "for (const n of cases) {\n"
        "  const s = XY.formatQuantity(n, 'iops');\n"
        "  const got = XY.parseNumber(s);\n"
        "  if (got !== n) { console.log(n, s, got); process.exit(1); }\n"
        "}\n"
        "try { XY.parseBytes('1.2.3 GB'); console.log('accepted 1.2.3'); process.exit(1); }\n"
        "catch (e) { if (!/cannot read a size/.test(e.message)) { console.log(e.message); process.exit(1); } }\n"
        "const bytes = XY.parseBytes(XY.formatQuantity(5e11, 'bytes'));\n"
        "if (Math.abs(bytes - 5e11) > 1e-6) { console.log(bytes); process.exit(1); }\n"
        "if (XY.parseBytes(XY.formatBytes(1500)) !== 1500) { console.log('1500 B'); process.exit(1); }\n"
        "if (XY.parseNumber('3,000 iops') !== 3000) { console.log('comma'); process.exit(1); }\n"
        "console.log('parse/format round-trip ok');\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [NODE, str(script), str(EVALUATE_JS.resolve())],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "round-trip ok" in proc.stdout


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


CHECK_EXPORT_GOLDENS = (
    Path(__file__).resolve().parents[1] / ".github" / "scripts" / "check-export-goldens.js"
)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_deploy_golden_script_accepts_a_good_export(blob, tmp_path):
    """The deploy workflow's Node gate is the same checkGolden() CI already
    runs, pointed at the exported HTML. Pin the script, not a one-off in YAML,
    so a 1000x parse regression cannot ship because the live grep still saw 200.
    """
    html = tmp_path / "calculator.html"
    html.write_text(render(blob), encoding="utf-8")
    proc = subprocess.run(
        [NODE, str(CHECK_EXPORT_GOLDENS), str(html)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert '"golden_failures":0' in proc.stdout


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_deploy_golden_script_rejects_a_bent_export(blob, tmp_path):
    bent = json.loads(json.dumps(blob))
    bent["golden"][0]["mode"] *= 1.01
    html = tmp_path / "bent.html"
    html.write_text(render(bent), encoding="utf-8")
    proc = subprocess.run(
        [NODE, str(CHECK_EXPORT_GOLDENS), str(html)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 1


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_deploy_golden_script_rejects_a_stale_live_blob(blob, tmp_path):
    good = tmp_path / "export.html"
    good.write_text(render(blob), encoding="utf-8")
    stale_blob = json.loads(json.dumps(blob))
    stale_blob["corpus_digest"] = "stale-digest"
    stale = tmp_path / "live.html"
    stale.write_text(render(stale_blob), encoding="utf-8")
    proc = subprocess.run(
        [NODE, str(CHECK_EXPORT_GOLDENS), str(stale), str(good)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 1
    assert "corpus_digest mismatch" in proc.stderr


def test_render_is_deterministic(blob):
    # Same corpus, byte-identical page. An export that differs on every run
    # cannot be diffed, and "what changed" is the whole question when
    # republishing.
    assert render(blob) == render(blob)


def test_render_substitutes_every_placeholder(blob):
    html = render(blob)
    for marker in ("__XYCALC_CORPUS_JSON__", "__XYCALC_EVALUATE_JS__", "__XYCALC_APP_JS__", "__XYCALC_CRUMB__", "__XYCALC_FAMILY_STRIP__"):
        assert marker not in html
    assert "XY.checkGolden" in html, "the page shipped without its self-check"
    assert "XY.chainEvaluate" in html, "the page shipped without scenario arithmetic"
    assert "xycalc · a swamplink research property" in html
    assert "function setTab" in html, "the page shipped without its UI"
    assert "XYCALC_APP" in html, "the page shipped without the extracted UI script"
    assert "calculateSimple" in html, "the page shipped without Simple mode"
    assert "simpleFirstPaintHtml" in html
    assert "SIMPLE_HONESTY_LINE" in html
    assert "SIZE_PATH_FOOTNOTES" in html
    assert "size-path-footnote" in html
    assert 'id="simple-view"' in html
    assert 'id="single-model-footnotes"' in html
    assert 'id="simple-honesty-slot"' in html
    assert 'id="mode-simple"' in html
    assert 'id="mode-scientific"' in html
    assert 'id="scientific-math"' in html
    assert "--btn-ink" in html
    assert "--error" in html
    assert "Copy as citation" in html
    assert 'aria-live="polite"' in html
    assert "aria-labelledby" not in html
    assert "Show the math" in html
    assert 'id="scenario-workbench"' in html
    assert 'id="scenario-compact"' in html
    assert "existing ? existing.open : false" in html
    assert 'class="nvd-fold"' in html
    assert "tabindex=\"0\"" in html
    assert "renderCascadeModelStep" in html
    assert "weakestValidation" in html
    assert "the sentence it was read from" in html
    assert 'id="simple-vulns"' in html
    assert 'id="simple-vuln-storage"' in html
    assert "simple-db-size" not in html


def test_duplicate_id_helper_catches_the_issue_137_shape():
    """#112 shipped two id=simple-vulns. This helper is the silent-ship gate."""
    broken = (
        '<label for="simple-vulns">Vulnerability records</label>'
        '<input id="simple-vulns">'
        '<label for="simple-vulns">Vulnerability records</label>'
        '<input id="simple-vulns">'
    )
    assert html_duplicate_ids(broken) == ["simple-vulns"]
    assert html_id_counts(broken)["simple-vulns"] == 2
    assert html_duplicate_ids('<input id="a"><input id="b">') == []


def test_simple_form_ids_unique_and_match_app_js():
    """bootSimple throws on missing IDs; duplicate IDs bind the wrong node.

    Issue #137: calculator.html duplicated simple-vulns and dropped
    simple-vuln-storage / devices / residual while app.js still read them.
    """
    html = TEMPLATE.read_text(encoding="utf-8")
    app = APP_JS.read_text(encoding="utf-8")
    counts = html_id_counts(html)
    assert html_duplicate_ids(html) == []

    listed = re.search(r"const SIMPLE_FORM_FIELD_IDS = \[([^\]]+)\]", app, re.S)
    assert listed, "SIMPLE_FORM_FIELD_IDS missing from app.js"
    field_ids = re.findall(r'"([^"]+)"', listed.group(1))
    assert field_ids == [
        "simple-vulns",
        "simple-vuln-storage",
        "simple-devices",
        "simple-device-avg",
        "simple-residual",
    ]
    for fid in field_ids:
        assert counts[fid] == 1, f"{fid} occurs {counts[fid]} time(s)"
        assert f'$("{fid}")' in app

    missing = []
    for rid in sorted(set(_DOLLAR_ID.findall(app))):
        if not rid.startswith("simple-") or rid in _SIMPLE_INJECTED_IDS:
            continue
        if counts[rid] != 1:
            missing.append((rid, counts[rid]))
    assert missing == [], f"app.js simple ids missing or duplicated in HTML: {missing}"
    assert "simple-db-size" not in html
    assert "simple-db-size" not in app


def test_exported_page_simple_ids_stay_unique(blob):
    html = render(blob)
    assert html_duplicate_ids(html) == []
    counts = html_id_counts(html)
    for fid in (
        "simple-vulns",
        "simple-vuln-storage",
        "simple-devices",
        "simple-device-avg",
        "simple-residual",
        "simple-status",
        "simple-result",
    ):
        assert counts[fid] == 1, fid
    assert "Enter vuln count and storageSize" in html


def test_export_blob_carries_scenario_chain(blob):
    slugs = {s["slug"] for s in blob["scenarios"]}
    assert "mongodb.size-to-instance" in slugs
    inst = next(s for s in blob["scenarios"] if s["slug"] == "mongodb.size-to-instance")
    assert inst["nvd_chart"]["annual"][0]["count"] == 28818
    assert inst["nvd_chart"]["annual"][2]["microsoft"] == 1255
    assert blob["instance_catalog"]
    assert blob["instance_catalogs"]["azure-vm"]
    assert any(i["name"].startswith("Esv6.") for i in blob["instance_catalogs"]["azure-vm"])
    assert blob["scenario_golden"]
    homepage = next(
        g
        for g in blob["scenario_golden"]
        if g["inputs"].get("baseline_storage_size") == "500GB"
        and "index_size" not in g["inputs"]
    )
    aws = next(s for s in homepage["steps"] if s["slug"] == "aws-ec2.instance-select")
    assert aws["pick_lo"] == "r8i.96xlarge"
    assert aws["pick_mode"] == "r8i.96xlarge"
    assert aws["pick_hi"] == "u7i-12tb.224xlarge"
    assert any(i["name"] == "u7i-12tb.224xlarge" for i in blob["instance_catalog"])
    assert any(i["name"] == "r6i.32xlarge" for i in blob["instance_catalog"])
    r6 = next(s for s in homepage["steps"] if s.get("family") == "r6i")
    assert r6["pick_mode"] is None
    assert r6["pick_lo"] is None
    assert r6["pick_hi"] is None


def test_export_blob_carries_occupancy_band(blob):
    g = blob["occupancy_band"]
    assert g["model"] == "mongodb.wt-cache"
    assert g["ladder"]["eviction_target"]["value"] == 80
    assert g["ladder"]["eviction_trigger"]["value"] == 95
    assert len(g["passes"]) == 3
    assert g["passes"][1]["label"] == "confirm 25 s #1"
    assert g["passes"][2]["label"] == "confirm 25 s #2"
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


def test_guides_are_loaded_from_corpus_yaml(conn):
    slugs = {r[0] for r in conn.execute("SELECT slug FROM guide")}
    assert slugs == {"occupancy_band", "cache_cliff"}


def test_export_py_does_not_hardcode_guide_figures():
    """The whole point of #84: these numbers live on observation rows."""
    src = Path(__file__).resolve().parent.parent / "src" / "xycalc" / "export.py"
    text = src.read_text(encoding="utf-8")
    assert "occupancy_band_guide" not in text
    assert "cache_cliff_guide" not in text
    assert "_latency_ms_from_notes" not in text
    assert "Mean latency" not in text
    assert "wt_cache_gb\": 0.25" not in text
    assert "slope ≈" not in text


def test_export_blob_carries_cache_cliff(blob):
    g = blob["cache_cliff"]
    assert g["status"] == "measured"
    assert g["a2_legs"]
    assert g["a2_legs"][0]["ratio"] == 0.5
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
    assert "slope ≈ −3.8" in g["transfer"]
    assert "1.0 GB cache" in g["transfer"]


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
    from xycalc.version import package_version, pyproject_version

    package_version.cache_clear()
    b = corpus_blob(conn)
    assert b["xycalc_version"] == pyproject_version()
    assert b["xycalc_version"] == package_version()
    assert b["xycalc_git"] == "deadbee"


def test_blob_version_ignores_stale_install_metadata(monkeypatch, conn):
    monkeypatch.setattr(
        "xycalc.version.installed_version", lambda name: "0.1.1"
    )
    from xycalc.version import package_version, pyproject_version

    package_version.cache_clear()
    b = corpus_blob(conn)
    assert b["xycalc_version"] != "0.1.1"
    assert b["xycalc_version"] == pyproject_version()
    package_version.cache_clear()


def test_rendered_page_embeds_git_identity(monkeypatch, conn):
    monkeypatch.setenv("GITHUB_SHA", "feedface00000000")

    b = corpus_blob(conn)
    html = render(b)
    assert b["xycalc_git"] == "feedfac"
    compact = html.replace(" ", "")
    from xycalc.version import package_version, pyproject_version

    package_version.cache_clear()
    assert '"xycalc_git":"feedfac"' in compact
    assert f'"xycalc_version":"{pyproject_version()}"' in compact


def test_template_keeps_simple_claim_and_reserved_advanced_subnav():
    text = TEMPLATE.read_text(encoding="utf-8")
    assert ".view-subnav {" in text
    assert "min-height: 2.35rem;" in text
    assert "body.mode-simple .view-subnav .tabs" in text
    assert "body.mode-simple .claim" not in text
    assert "body.mode-scientific .claim" not in text


def test_calculator_template_prints_git_in_provenance():
    text = APP_JS.read_text(encoding="utf-8")
    assert "CORPUS.xycalc_git" in text
    assert "exported by xycalc" in text


def test_calculator_template_splices_app_js_like_evaluate():
    html = TEMPLATE.read_text(encoding="utf-8")
    assert "/*__XYCALC_APP_JS__*/" in html
    assert "/*__XYCALC_EVALUATE_JS__*/" in html
    assert "(() => {" not in html
    assert "function setTab" not in html


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_static_javascript_parses():
    for path in (EVALUATE_JS, APP_JS):
        proc = subprocess.run(
            [NODE, "--check", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert proc.returncode == 0, path.name + ":\n" + proc.stdout + proc.stderr


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_app_js_helpers():
    """Pure UI helpers: ticks, nearestIndex, sweep grid, maybeAuto predicate."""
    script = Path(__file__).resolve().parent / "check_app_helpers.js"
    proc = subprocess.run(
        [NODE, str(script), str(APP_JS.resolve())],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "app helpers ok" in proc.stdout


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_occupancy_ladder_labels_do_not_collide():
    """#132: at 375px, target 80 and trigger 95 must not share a row; 90 stays painted."""
    script = Path(__file__).resolve().parent / "check_occupancy_ladder.js"
    proc = subprocess.run(
        [NODE, str(script), str(APP_JS.resolve()), str(TEMPLATE.resolve())],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "occupancy ladder labels ok" in proc.stdout


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_simple_first_paint_cannot_show_ram_without_weakest_grade(blob, tmp_path):
    """Default 100GB Simple path: a host-RAM figure without the weakest
    chained grade (and Validated at 0 in-band) is the live honesty miss."""
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps(blob), encoding="utf-8")
    script = Path(__file__).resolve().parent / "check_simple_first_paint.js"
    proc = subprocess.run(
        [NODE, str(script), str(APP_JS.resolve()), str(EVALUATE_JS.resolve()), str(corpus)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "simple first paint ok" in proc.stdout


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_size_path_footnotes_on_default_mongodb_chain(blob, tmp_path):
    """Simple first paint and size-to-instance What-you-need carry the three
    measured footnotes; ebs.microburst only gets the EBS sentence."""
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps(blob), encoding="utf-8")
    script = Path(__file__).resolve().parent / "check_size_path_footnotes.js"
    proc = subprocess.run(
        [NODE, str(script), str(APP_JS.resolve()), str(EVALUATE_JS.resolve()), str(corpus)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "size path footnotes ok" in proc.stdout


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_static_javascript_lints():
    """ESLint when present; otherwise the mechanical rules the config encodes."""
    repo = Path(__file__).resolve().parents[1]
    eslint = repo / "node_modules" / ".bin" / "eslint"
    if eslint.exists():
        proc = subprocess.run(
            [str(eslint), "src/xycalc/static/app.js"],
            cwd=repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return
    # CI does not npm-install. Pin the same rules the config names so a
    # `var` or an unused helper cannot land without the eslint binary.
    for path in (EVALUATE_JS, APP_JS):
        text = path.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.lstrip()
            assert not stripped.startswith("var "), f"{path.name}:{i} uses var"
            assert "\t" not in line, f"{path.name}:{i} contains a tab"


def test_stamp_html_matches_footer_fields(blob):
    line = provenance_line(blob)
    html = render_stamp_html(blob)
    assert line in html
    assert f'data-corpus-digest="{blob["corpus_digest"]}"' in html
    assert f'data-xycalc-version="{blob["xycalc_version"]}"' in html
    assert f'data-xycalc-git="{blob["xycalc_git"]}"' in html
    assert f'data-models="{len(blob["models"])}"' in html
    assert "\r" not in html


def test_export_writes_stamp_without_inventing_a_hero(db_path, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "abcdef0123456789")
    from xycalc.db import connect
    from xycalc.version import package_version

    package_version.cache_clear()
    html_path = tmp_path / "calculator.html"
    written = export(html_path, db=db_path)
    assert written == html_path
    conn = connect(db_path)
    blob = corpus_blob(conn)
    conn.close()
    html = html_path.read_text(encoding="utf-8")
    assert html == render(blob)
    stamp = (tmp_path / "stamp.html").read_text(encoding="utf-8")
    assert provenance_line(blob) in stamp
    lab_html = (tmp_path / "lab-table.html").read_text(encoding="utf-8")
    assert "Still needs a case" in lab_html
    assert blob["xycalc_version"] in html
    assert blob["xycalc_version"] in stamp
    assert blob["corpus_digest"] in stamp
    assert blob["xycalc_git"] in stamp
    still = Path(__file__).resolve().parents[1] / "src" / "xycalc" / "static" / "landing-still.png"
    og = tmp_path / "og.png"
    if still.is_file():
        assert og.read_bytes() == still.read_bytes()
        assert og.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    else:
        assert not og.exists()
    assert not (tmp_path / "chart.svg").exists()


def test_export_copies_approved_still_when_present(db_path, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "abcdef0123456789")
    from xycalc.version import package_version
    from xycalc.pngutil import write_rgb_png
    import xycalc.export as export_mod

    package_version.cache_clear()
    still = tmp_path / "landing-still.png"
    write_rgb_png(still, 2, 2, bytes([10, 20, 30] * 4))
    monkeypatch.setattr(export_mod, "LANDING_STILL", still)
    out = tmp_path / "out"
    out.mkdir()
    export(out / "calculator.html", db=db_path)
    og = out / "og.png"
    assert og.read_bytes() == still.read_bytes()
    assert not (out / "chart.svg").exists()
    html = (out / "calculator.html").read_text(encoding="utf-8")
    from xycalc.db import connect

    conn = connect(db_path)
    blob = corpus_blob(conn)
    conn.close()
    assert html == render(blob)


def test_export_skips_og_when_still_missing(db_path, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "abcdef0123456789")
    import xycalc.export as export_mod
    from xycalc.db import connect
    from xycalc.version import package_version

    package_version.cache_clear()
    monkeypatch.setattr(export_mod, "LANDING_STILL", tmp_path / "no-such-still.png")
    export(tmp_path / "calculator.html", db=db_path)
    conn = connect(db_path)
    blob = corpus_blob(conn)
    conn.close()
    assert (tmp_path / "calculator.html").read_text(encoding="utf-8") == render(blob)
    assert not (tmp_path / "og.png").exists()
    assert provenance_line(blob) in (tmp_path / "stamp.html").read_text(encoding="utf-8")


def test_deploy_workflow_copies_landing_sidecars():
    text = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "deploy-calculator.yml"
    ).read_text(encoding="utf-8")
    assert "cp /tmp/og.png tools/xycalc/og.png" in text
    assert "cp /tmp/stamp.html tools/xycalc/stamp.html" in text
    assert "git add tools/xycalc/og.png" in text
    assert "git add tools/xycalc/stamp.html" in text
    assert "chart.svg" not in text


def test_docs_name_the_shipped_permalink_shape():
    docs = (
        Path(__file__).resolve().parents[1] / "docs" / "CALCULATOR.md"
    ).read_text(encoding="utf-8")
    assert "#tab=single&model=<slug>" in docs
    assert "#tab=scenario&scenario=<slug>" in docs
    assert "landing-still.png" in docs
    assert "stamp.html" in docs
    assert "?model=" in docs
    assert "substitute" in docs


