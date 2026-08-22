"""xycalc — how much X does it take to run Y?

    xycalc models
    xycalc sizing   mongodb.wt-cache --storage-size 500GB --index-size 40GB
    xycalc sizing   mongodb.wt-cache --storage-size 500GB --index-size 40GB --sensitivity
    xycalc headroom mongodb.wt-cache --storage-size 500GB --available 256GB
    xycalc scenarios
    xycalc scenario mongodb.size-to-instance --storage-size 500GB --index-size 40GB
    xycalc why      mongodb.wt-cache
    xycalc ingest   dump.json
    xycalc ingest   dump.json --emit-observation candidate.yaml
    xycalc build
    xycalc audit
    xycalc gui

Flags for `sizing`, `headroom`, and `scenario` are generated from the
model's declared inputs, so a new model needs no argument-parsing code —
only YAML.

Every answer prints its validation status. A model that has never been checked
against a running system says so, on every invocation, without being asked.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from . import audit as audit_mod
from . import build as build_mod
from .db import connect
from .ingest import (
    IngestError,
    format_extraction,
    is_published_corpus_path,
    write_observation_files,
)
from .model import (
    DEFAULT_INSTANCE_CEILING,
    Model,
    ModelError,
    Sensitivity,
    build_instance_sizing_summary,
    chain_evaluate,
    format_quantity,
    get_scenario,
    headroom,
    load_instance_catalog,
    load_scenarios,
    parse_bytes,
    scenario_form_inputs,
    select_instance,
    validation_status,
)

BAR = "─" * 68


def _flag(key: str) -> str:
    return "--" + key.replace("_", "-")


def _fmt(value: float, unit: str) -> str:
    return format_quantity(value, unit)


def _load(conn: sqlite3.Connection, slug: str) -> Model:
    try:
        return Model.load(conn, slug)
    except ModelError as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(2)


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def _print_breakdown(result, model: Model) -> None:
    print(f"\n{model.question}")
    print(BAR)

    # Header on every CHANGE of role, not on first sight of one. Terms run in
    # sequence order and a model can legitimately return to an earlier role —
    # this model adds indexes (floor) after decompressing (amplifier). Grouping
    # by first-seen would file the index term under AMPLIFIER and misrepresent
    # the arithmetic that actually ran.
    previous = None
    for step in result.steps:
        if step.term.role != previous:
            previous = step.term.role
            print(f"\n  {step.term.role.upper()}")
        if step.skipped:
            print(f"    {step.term.label:<34} {'—':>14}   ({step.skip_reason})")
            continue
        print(
            f"    {step.term.label:<34} {step.contribution:>14}"
            f"   → {_fmt(step.mode, result.unit):>10}"
        )
        if step.term.source:
            print(f"    {'':<34} {'':>14}     [{step.term.source}]")

    print(f"\n{BAR}")
    print(f"  ANSWER   {_fmt(result.mode, result.unit)}")
    print(f"  band     {_fmt(result.lo, result.unit)} – {_fmt(result.hi, result.unit)}")


def _print_constraints(result) -> None:
    if not result.constraints:
        return
    print("\n  CONSTRAINTS — not in the arithmetic, and they still bind")
    for term in result.constraints:
        value = f"{term.coeff_mode:g}{'%' if term.unit == 'percent' else ''}"
        print(f"    · {term.label} ({value}) [{term.source}]")
        for line in _wrap(term.rationale, 66):
            print(f"        {line}")


def _print_validation(conn: sqlite3.Connection, slug: str) -> None:
    status = validation_status(conn, slug)
    # Three markers because there are three states. A tick beside a model
    # checked once at 41% error is the wrong signal.
    marker = {"none": "!", "thin": "~", "reasonable": "✓"}[status["grade"]]
    print(f"\n  {marker} {status['text']}")


def _wrap(text: str, width: int) -> list[str]:
    import textwrap

    return textwrap.wrap(" ".join(text.split()), width)


def _print_reframe(model: Model) -> None:
    if not model.reframe:
        return
    print(f"\n{BAR}")
    print("  READ THIS BEFORE ACTING ON THE NUMBER")
    print(BAR)
    for para in model.reframe.strip().split("\n\n"):
        for line in _wrap(para, 66):
            print(f"  {line}")
        print()


def _print_notes(model: Model) -> None:
    if not model.notes:
        return
    print(f"\n{BAR}")
    print("  VARIABLES & HOW TO USE THEM")
    print(BAR)
    for para in model.notes.strip().split("\n\n"):
        for line in _wrap(para, 66):
            print(f"  {line}")
        print()


def _print_sensitivity(report: Sensitivity) -> None:
    print(f"\n{BAR}")
    print("  SENSITIVITY — one coefficient at a time, others held at mode")
    print(BAR)
    print(f"  {report.sentence}")
    for t in report.terms:
        if t.span <= 0:
            continue
        pct = f"{int(round(t.share * 100))}%"
        print(
            f"    {t.label:<34} {pct:>6}   span {_fmt(t.span, report.unit)}"
        )
    if report.measure_next_label:
        print(f"\n  measure next: {report.measure_next_label}")
        print("    A measured value for this term is what shrinks the band.")
        print("    `xycalc ingest` turns a db.stats() paste into model inputs.")


def _model_values(args, model: Model) -> dict:
    values = {i["key"]: getattr(args, i["key"], None) for i in model.inputs}
    return {k: v for k, v in values.items() if v is not None}


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def cmd_models(args) -> int:
    conn = connect(args.db)
    for slug in Model.all(conn):
        m = Model.load(conn, slug)
        status = validation_status(conn, slug)
        print(f"{slug:<22} {m.question}")
        print(f"{'':<22} {status['text']}")
    conn.close()
    return 0


def cmd_sizing(args) -> int:
    conn = connect(args.db)
    model = _load(conn, args.model)
    values = _model_values(args, model)
    try:
        result = model.evaluate(values)
        report = model.sensitivity(values) if args.sensitivity else None
    except ModelError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    _print_breakdown(result, model)
    _print_constraints(result)
    if report is not None:
        _print_sensitivity(report)
    _print_validation(conn, model.slug)
    _print_reframe(model)
    _print_notes(model)
    conn.close()
    return 0


def cmd_headroom(args) -> int:
    conn = connect(args.db)
    model = _load(conn, args.model)
    values = {i["key"]: getattr(args, i["key"]) for i in model.inputs}
    values = {k: v for k, v in values.items() if v is not None}
    try:
        result = model.evaluate(values)
        from .model import parse_bytes

        available = parse_bytes(args.available)
    except ModelError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    h = headroom(result, available)
    print(f"\n{model.question}")
    print(BAR)
    print(f"  available          {_fmt(available, result.unit)}")
    print(f"  required (mode)    {_fmt(h['required_mode'], result.unit)}")
    print(
        f"  required (band)    {_fmt(h['required_lo'], result.unit)} – "
        f"{_fmt(h['required_hi'], result.unit)}"
    )
    print(f"  utilisation        {h['utilisation_mode_pct']:,.0f}% of available")
    print(f"  margin             {_fmt(h['margin_mode'], result.unit)}")
    print(f"\n  VERDICT  {h['verdict']}")
    _print_constraints(result)
    _print_validation(conn, model.slug)
    conn.close()
    return 0


def cmd_instance_select(args) -> int:
    """Run any model on its own inputs, then look up its band against an
    instance catalog instead of collapsing it to one number first --
    mirrors `headroom`'s shape, swapping a supplied `--available` for a
    lookup against `data/coefficients/aws-ec2.yaml` or `azure-vm.yaml`."""
    conn = connect(args.db)
    model = _load(conn, args.model)
    values = {i["key"]: getattr(args, i["key"]) for i in model.inputs}
    values = {k: v for k, v in values.items() if v is not None}
    try:
        result = model.evaluate(values)
    except ModelError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    catalog = load_instance_catalog(conn, args.catalog)
    try:
        ceiling = parse_bytes(args.max_ram) if args.max_ram else None
        if ceiling == 0:  # explicit escape hatch: --max-ram 0 lifts the cap
            ceiling = None
        sel = select_instance(result, catalog, family=args.family, ceiling_bytes=ceiling)
    except ModelError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    print(f"\n{model.question}")
    print(BAR)
    print(
        f"  required (band)    {_fmt(sel['required_lo'], result.unit)} – "
        f"{_fmt(sel['required_hi'], result.unit)}  "
        f"(mode {_fmt(sel['required_mode'], result.unit)})"
    )
    print()
    for label, key in (("low end", "pick_lo"), ("mode", "pick_mode"), ("high end", "pick_hi")):
        spec = sel[key]
        if spec is None:
            print(f"  {label:<10} custom sizing — over the largest instance in this pool")
        else:
            headroom_bytes = spec.ram_bytes - {
                "pick_lo": sel["required_lo"],
                "pick_mode": sel["required_mode"],
                "pick_hi": sel["required_hi"],
            }[key]
            print(
                f"  {label:<10} {spec.name:<28} "
                f"{_fmt(spec.ram_bytes, result.unit)} RAM"
                + (f", {spec.vcpu:g} vCPU" if spec.vcpu else "")
                + f"  (+{_fmt(headroom_bytes, result.unit)} headroom)"
            )

    if sel["exceeds_pool"]:
        largest = sel["largest_in_pool"]
        capped = ceiling is not None and largest.ram_bytes >= ceiling
        print(
            f"\n  NOTE  the high end of the band exceeds "
            + (
                f"the {_fmt(ceiling, result.unit)} sizing ceiling "
                f"(org policy, not a vendor limit — pass --max-ram to change it)."
                if capped
                else f"the largest instance in this pool "
                f"({largest.name}, {_fmt(largest.ram_bytes, result.unit)})."
            )
            + " Custom sizing, not a guessed SKU past the cited catalog."
        )

    _print_constraints(result)
    _print_validation(conn, model.slug)
    conn.close()
    return 0


def cmd_scenarios(args) -> int:
    for s in load_scenarios():
        if s.get("disabled"):
            print(f"{s['slug']:<26} (not yet modeled — {s.get('note', '').strip()})")
        else:
            print(f"{s['slug']:<26} {s['label']}")
    return 0


def cmd_scenario(args) -> int:
    """Run every step of a scenario, feeding one step's whole band into the
    next instead of the user copying a mode value by hand — the thing
    mongodb.host-ram's own `notes` field used to ask for."""
    conn = connect(args.db)
    try:
        scenario = get_scenario(args.scenario)
    except ModelError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if scenario.get("disabled"):
        print(
            f"error: {scenario['slug']}: not yet modeled — {scenario.get('note', '').strip()}",
            file=sys.stderr,
        )
        return 2

    values: dict = {}
    for inp in scenario_form_inputs(conn, scenario):
        v = getattr(args, inp["key"], None)
        if v is not None:
            values[inp["key"]] = v

    try:
        available = parse_bytes(args.available) if args.available else None
        steps = chain_evaluate(conn, scenario, values, available=available)
    except ModelError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    print(f"\n{scenario['label']}")
    print(BAR)
    if scenario.get("summary"):
        for line in _wrap(scenario["summary"], 68):
            print(f"  {line}")

    prev_unit = "bytes"
    for i, s in enumerate(steps, 1):
        print(f"\n{BAR}")
        if s.kind == "model":
            print(f"STEP {i} — {'chained from the step above' if s.chained else 'your input'}")
            if s.assumed_inputs:
                assumed = ", ".join(
                    f"{k}={v:g}" if isinstance(v, (int, float)) else f"{k}={v}"
                    for k, v in s.assumed_inputs.items()
                )
                line = f"  assumed            {assumed}"
                if s.assumed_note:
                    line += f" ({s.assumed_note})"
                print(line)
            _print_breakdown(s.result, s.model)
            _print_constraints(s.result)
            _print_validation(conn, s.model.slug)
            if s.headroom is not None:
                h = s.headroom
                print(f"\n  available          {_fmt(available, s.result.unit)}")
                print(f"  utilisation        {h['utilisation_mode_pct']:,.0f}% of available")
                print(f"  VERDICT  {h['verdict']}")
            _print_reframe(s.model)
            _print_notes(s.model)
            prev_unit = s.result.unit
        elif s.gp3_spec is not None:
            print(f"STEP {i} — gp3 volume baseline and ceilings")
            spec = s.gp3_spec
            print(f"  volume             {spec['volume_gib']:,.1f} GiB")
            print(f"  baseline IOPS      {spec['baseline_iops']:,.0f}")
            print(f"  max provisionable  {spec['max_provisionable_iops']:,.0f} IOPS")
            print(
                f"  throughput         {spec['baseline_throughput_mibps']:,.0f}–"
                f"{spec['max_throughput_mibps']:,.0f} MiB/s (baseline–max)"
            )
            if spec.get("instance_ebs_bandwidth_gbps") is not None:
                print(
                    f"  this pick's EBS    {spec['instance_name']} "
                    f"{spec['instance_ebs_bandwidth_gbps']:g} Gbps "
                    f"({spec['instance_ebs_throughput_mibps']:,.0f} MiB/s) — "
                    f"usable throughput {spec['usable_throughput_mibps']:,.0f} MiB/s"
                )
        else:
            pool = (
                "smallest Azure VM covering the band above"
                if s.slug.startswith("azure-vm")
                else "smallest AWS instance covering the band above"
            )
            print(f"STEP {i} — {pool}")
            pick = s.instance_pick
            for label, key in (
                ("low end", "pick_lo"),
                ("mode", "pick_mode"),
                ("high end", "pick_hi"),
            ):
                spec = pick[key]
                if spec is None:
                    print(f"  {label:<10} custom sizing — over the largest instance in this pool")
                    continue
                need = {
                    "pick_lo": pick["required_lo"],
                    "pick_mode": pick["required_mode"],
                    "pick_hi": pick["required_hi"],
                }[key]
                headroom_bytes = spec.ram_bytes - need
                print(
                    f"  {label:<10} {spec.name:<28} {_fmt(spec.ram_bytes, prev_unit)} RAM"
                    + (f", {spec.vcpu:g} vCPU" if spec.vcpu else "")
                    + (
                        f", EBS {spec.ebs_bandwidth_gbps:g} Gbps"
                        if spec.ebs_bandwidth_gbps
                        else ""
                    )
                    + f"  (+{_fmt(headroom_bytes, prev_unit)} headroom)"
                )
            if pick["exceeds_pool"]:
                print(
                    "\n  NOTE  the high end of the band exceeds this pool — custom "
                    "sizing, not a guessed SKU past the cited catalog."
                )

    summary = build_instance_sizing_summary(steps, values)
    if summary:
        print(f"\n{BAR}")
        print("  SIZING SUMMARY")
        if ram := summary.get("ram"):
            print(
                f"  RAM                {_fmt(ram['lo'], ram['unit'])} – "
                f"{_fmt(ram['hi'], ram['unit'])}  (mode {_fmt(ram['mode'], ram['unit'])})"
            )
        if cpu := summary.get("cpu"):
            print(
                f"  vCPU               {cpu['lo']} – {cpu['hi']}  "
                f"(mode {cpu['mode']}, instance {cpu['instance_mode']})"
            )
        if azure := summary.get("azure"):
            print(
                f"  Azure VM           {azure['lo']} – {azure['hi']}  "
                f"(mode {azure['mode']})"
            )
            if azure.get("exceeds_pool"):
                print(
                    "  Azure NOTE         high end exceeds this pool — custom "
                    "sizing, not a recommendation."
                )
        if disk := summary.get("disk"):
            line = (
                f"  gp3 disk           {disk['volume_gib']:,.1f} GiB; "
                f"{disk['baseline_iops']:,.0f} included IOPS; "
                f"volume cap {disk['max_provisionable_iops']:,.0f} IOPS"
            )
            if disk.get("provisioned_iops"):
                p = disk["provisioned_iops"]
                src = (
                    "assumed included mean"
                    if disk.get("provisioned_iops_assumed_mean")
                    else "measured mean"
                )
                line += (
                    f"; peak-second {p['lo']:,.0f}–{p['hi']:,.0f} "
                    f"(mode {p['mode']:,.0f}, {src})"
                )
            if disk.get("instance_ebs_bandwidth_gbps") is not None:
                line += (
                    f"; {disk['instance_name']} EBS "
                    f"{disk['instance_ebs_bandwidth_gbps']:g} Gbps"
                )
            print(line)

    if scenario.get("see_also"):
        print(f"\n{BAR}")
        print("  SEE ALSO")
        for sa in scenario["see_also"]:
            if sa.get("url"):
                print(f"    · {sa.get('label') or sa['url']}")
                print(f"        {sa['url']}")
            elif sa.get("scenario"):
                print(f"    · {sa['scenario']}")
            for line in _wrap(sa["reason"], 64):
                print(f"        {line}")

    conn.close()
    return 0


def cmd_why(args) -> int:
    """The citation chain. Every term, what it cites, and the sentence it was
    read from."""
    conn = connect(args.db)
    model = _load(conn, args.model)
    print(f"\n{model.slug} — {model.question}")
    print(BAR)
    if model.summary:
        for line in _wrap(model.summary, 68):
            print(f"  {line}")

    for term in model.terms:
        print(f"\n  [{term.role}] {term.label}")
        for line in _wrap(term.rationale, 64):
            print(f"      {line}")
        if term.coefficient:
            band = (
                f"{term.coeff_mode:g}"
                if term.coeff_lo == term.coeff_hi
                else f"{term.coeff_lo:g}–{term.coeff_mode:g}–{term.coeff_hi:g}"
            )
            print(f"      value    {band} {term.unit}  ({term.confidence})")
            print(f"      applies  {term.applies_to}")
            print(f"      source   {term.source} — {term.source_title}")
            if term.source_url:
                print(f"               {term.source_url}")
            if term.quote:
                for line in _wrap(f'"{term.quote}"', 60):
                    print(f"        {line}")
        else:
            print(f"      value    supplied by the caller ({term.input_key})")

    if args.sensitivity:
        values = _model_values(args, model)
        try:
            _print_sensitivity(model.sensitivity(values))
        except ModelError as e:
            print(f"error: {e}", file=sys.stderr)
            conn.close()
            return 2

    _print_validation(conn, model.slug)
    _print_reframe(model)
    _print_notes(model)
    conn.close()
    return 0


def cmd_build(args) -> int:
    return build_mod.main([str(args.db)] if args.db else [])


def cmd_audit(args) -> int:
    return audit_mod.main([str(args.db)] if args.db else [])


def cmd_export(args) -> int:
    from . import export as export_mod

    argv = ["--out", str(args.out)]
    if args.db:
        argv += ["--db", str(args.db)]
    if args.crumb:
        argv += ["--crumb", args.crumb]
    return export_mod.main(argv)


def cmd_ingest(args) -> int:
    """Paste db.stats() / serverStatus JSON → model inputs + optional YAML.

    The paste is a candidate. By default this command never writes the
    corpus. ``--emit-observation`` writes candidate YAML outside ``data/``;
    destinations under the published tree are refused unless
    ``--force-corpus``.
    """
    from .ingest import extract_mongodb, observation_skeleton, parse_metrics
    from .payloads import ingest_payload

    if args.metrics in (None, "-", Path("-")):
        raw = sys.stdin.read()
    else:
        raw = Path(args.metrics).read_text(encoding="utf-8")

    emit_dest = args.emit_observation
    if emit_dest is not None:
        emit_dest = str(emit_dest)
    if (
        emit_dest
        and emit_dest != "-"
        and is_published_corpus_path(emit_dest)
        and not args.force_corpus
    ):
        print(
            "error: refusing to write under data/ (the published corpus that "
            "xycalc.build compiles). Default ingest writes nothing. Pass "
            "--emit-observation a path outside data/, or --force-corpus "
            "if you really mean to write candidate YAML into the published "
            "tree.",
            file=sys.stderr,
        )
        return 2

    conn = connect(args.db)
    try:
        body = ingest_payload(
            conn,
            raw,
            model=args.model,
            emit_observation=bool(emit_dest),
            tag=args.tag,
            workload=args.workload,
            machine_class=args.machine_class,
            publisher=args.publisher,
            source_type=args.source_type,
        )
    except (IngestError, ModelError) as e:
        print(f"error: {e}", file=sys.stderr)
        conn.close()
        return 2

    dump = parse_metrics(raw)
    extracted = extract_mongodb(dump)
    report = format_extraction(extracted)
    # YAML on stdout would be unusable mixed with the human report.
    report_file = sys.stderr if emit_dest == "-" else sys.stdout
    print(report, file=report_file)

    if body.get("sizing"):
        model = _load(conn, args.model)
        try:
            result = model.evaluate(extracted.model_inputs)
        except ModelError as e:
            print(f"error: {e}", file=sys.stderr)
            conn.close()
            return 2
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _print_breakdown(result, model)
            _print_constraints(result)
            _print_validation(conn, model.slug)
            _print_reframe(model)
        print(file=report_file)
        print(buf.getvalue(), end="", file=report_file)

    if emit_dest:
        skeleton = observation_skeleton(
            extracted,
            tag=args.tag,
            workload=args.workload,
            machine_class=args.machine_class,
            publisher=args.publisher,
            source_type=args.source_type,
        )
        if emit_dest == "-":
            from .ingest import render_observation_yaml

            sys.stdout.write(render_observation_yaml(skeleton))
        else:
            written = write_observation_files(
                skeleton, emit_dest, force_corpus=args.force_corpus
            )
            for path in written:
                print(f"wrote {path}", file=report_file)

    conn.close()
    return 0


def cmd_gui(args) -> int:
    try:
        import uvicorn
    except ImportError:
        print(
            "error: the web UI needs the gui extras — pip install -e '.[gui]'",
            file=sys.stderr,
        )
        return 2
    from .api import app

    uvicorn.run(app, host=args.host, port=args.port)
    return 0


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------


def _add_model_flags(parser: argparse.ArgumentParser, db: Path | None) -> None:
    """Generate one flag per declared input across every model.

    Inputs are unioned rather than made per-model subparsers: the model name is
    positional and argparse would need it resolved before it can know the
    flags. A flag belonging to another model is simply never read.
    """
    try:
        # Autobuild so a fresh clone can run the README quick start before
        # `xycalc build`. `build`/`audit` still parse if the corpus cannot
        # be compiled yet — those subparsers do not need model flags.
        conn = connect(db, autobuild=True)
    except (FileNotFoundError, sqlite3.Error, build_mod.BuildError):
        return
    seen: dict[str, str] = {}
    for r in conn.execute(
        "SELECT DISTINCT key, label, unit, help FROM model_input ORDER BY key"
    ):
        if r["key"] in seen:
            continue
        seen[r["key"]] = r["unit"]
        parser.add_argument(
            _flag(r["key"]),
            dest=r["key"],
            metavar=r["unit"].upper(),
            # argparse's HelpFormatter treats help text as a %-format string
            # (it substitutes %(default)s etc.), so a literal '%' from a
            # label like "growth (%)" raises ValueError at parse time. Escape
            # it here rather than banning '%' from every future label.
            help=r["label"].replace("%", "%%"),
        )
    conn.close()


def build_parser(db: Path | None = None) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="xycalc", description="How much X does it take to run Y?"
    )
    p.add_argument("--db", type=Path, default=None, help="corpus path override")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("models", help="list models and their validation status").set_defaults(
        func=cmd_models
    )

    for name, fn, extra in (
        ("sizing", cmd_sizing, False),
        ("headroom", cmd_headroom, True),
    ):
        sp = sub.add_parser(name, help=f"{name} against a model")
        sp.add_argument("model")
        if extra:
            sp.add_argument(
                "--available", required=True, help="what you actually have, e.g. 256GB"
            )
        if name == "sizing":
            sp.add_argument(
                "--sensitivity",
                action="store_true",
                help=(
                    "rank each coefficient's contribution to the answer's band "
                    "(one term at a time, others held at mode)"
                ),
            )
        _add_model_flags(sp, db)
        sp.set_defaults(func=fn)

    sp = sub.add_parser(
        "instance-select",
        help="which named instance covers a model's required band",
    )
    sp.add_argument("model")
    sp.add_argument(
        "--catalog",
        default="aws-ec2",
        help="coefficient system to pick from: aws-ec2 (default) or azure-vm",
    )
    sp.add_argument(
        "--family",
        default=None,
        help="filter the catalog by name prefix, e.g. r8i, Esv5, or Esv6 (default: whole catalog)",
    )
    sp.add_argument(
        "--max-ram",
        default=DEFAULT_INSTANCE_CEILING,
        help=(
            "org policy ceiling, not an AWS spec — above this, report "
            f"'custom sizing' rather than naming an instance (default: "
            f"{DEFAULT_INSTANCE_CEILING}, == u7inh-32tb.480xlarge; pass a larger "
            "value or 0 to lift it; 1536GiB restores the old r8i.48xlarge cap)"
        ),
    )
    _add_model_flags(sp, db)
    sp.set_defaults(func=cmd_instance_select)

    sub.add_parser(
        "scenarios", help="list scenarios — chains of models a user can run in one step"
    ).set_defaults(func=cmd_scenarios)

    sp = sub.add_parser(
        "scenario",
        help="run a chain of models, feeding one step's whole band into the next",
    )
    sp.add_argument("scenario")
    sp.add_argument(
        "--available",
        default=None,
        help="what you already have, e.g. 256GB — turns the last step into a headroom check",
    )
    _add_model_flags(sp, db)
    sp.set_defaults(func=cmd_scenario)

    sp = sub.add_parser("why", help="the citation chain behind every term")
    sp.add_argument("model")
    sp.add_argument(
        "--sensitivity",
        action="store_true",
        help=(
            "rank each coefficient's contribution to the answer's band "
            "(requires the same input flags as sizing)"
        ),
    )
    _add_model_flags(sp, db)
    sp.set_defaults(func=cmd_why)

    sub.add_parser("build", help="compile the YAML corpus").set_defaults(func=cmd_build)
    sub.add_parser("audit", help="run the corpus gates").set_defaults(func=cmd_audit)

    sp = sub.add_parser("gui", help="serve the calculator")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8200)
    sp.set_defaults(func=cmd_gui)

    sp = sub.add_parser(
        "export", help="write the calculator as one self-contained HTML file"
    )
    sp.add_argument("--out", type=Path, default=Path("dist/xycalc.html"))
    sp.add_argument("--crumb", default=None, help="raw HTML inserted above the header")
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser(
        "ingest",
        help="paste db.stats()/serverStatus JSON → model inputs and a candidate observation",
    )
    sp.add_argument(
        "metrics",
        nargs="?",
        default="-",
        help="JSON file, or - / omit to read stdin",
    )
    sp.add_argument(
        "--model",
        default="mongodb.wt-cache",
        help="model to run on the extracted inputs (default: mongodb.wt-cache)",
    )
    sp.add_argument(
        "--emit-observation",
        nargs="?",
        const="-",
        default=None,
        metavar="PATH",
        help=(
            "write a candidate observation YAML skeleton. PATH.yaml is one "
            "combined file; a directory gets sources/ + observations/ "
            "(corpus layout). Destinations under data/ (the published "
            "corpus xycalc.build compiles) are refused unless "
            "--force-corpus. Omit PATH to print YAML on stdout (report "
            "goes to stderr). Default ingest writes nothing. Provenance "
            "that cannot be derived is TODO, never invented."
        ),
    )
    sp.add_argument(
        "--force-corpus",
        action="store_true",
        help=(
            "allow --emit-observation to write under data/. Without this "
            "flag, ingest never writes the published corpus. MCP "
            "ingest_dbstats never writes files."
        ),
    )
    sp.add_argument(
        "--tag",
        help=(
            "slug prefix for the skeleton (default: ingest-<db> or "
            "ingest-<db>-<observed_on from the paste>; never today's date)"
        ),
    )
    sp.add_argument("--workload", help="fills observation.workload; otherwise TODO")
    sp.add_argument("--machine-class", help="fills observation.machine_class; otherwise TODO")
    sp.add_argument("--publisher", help="fills source.publisher; otherwise TODO")
    sp.add_argument(
        "--source-type",
        choices=["measured", "benchmark"],
        default=None,
        help=(
            "measured (a running system) or benchmark (a committed harness). "
            "Default: TODO — not invented as measured"
        ),
    )
    sp.set_defaults(func=cmd_ingest)

    return p


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    # The corpus is read once to discover model inputs before parsing, so
    # --db has to be found ahead of argparse. A missing corpus is not fatal
    # here: `build` and `audit` must still run without one.
    db = None
    if "--db" in argv:
        i = argv.index("--db")
        if i + 1 < len(argv):
            db = Path(argv[i + 1])
    args = build_parser(db).parse_args(argv)
    try:
        return args.func(args)
    except build_mod.BuildError as e:
        print(f"corpus build failed: {e}", file=sys.stderr)
        return 1
    except IngestError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
