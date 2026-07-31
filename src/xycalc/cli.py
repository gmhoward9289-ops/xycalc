"""xycalc — how much X does it take to run Y?

    xycalc models
    xycalc sizing   mongodb.wt-cache --storage-size 500GB --index-size 40GB
    xycalc headroom mongodb.wt-cache --storage-size 500GB --available 256GB
    xycalc why      mongodb.wt-cache
    xycalc build
    xycalc audit
    xycalc gui

Flags for `sizing` and `headroom` are generated from the model's declared
inputs, so a new model needs no argument-parsing code — only YAML.

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
from .model import Model, ModelError, format_bytes, headroom, validation_status

BAR = "─" * 68


def _flag(key: str) -> str:
    return "--" + key.replace("_", "-")


def _fmt(value: float, unit: str) -> str:
    if unit == "bytes":
        return format_bytes(value)
    if unit == "percent":
        return f"{value:,.1f}%"
    return f"{value:,.2f} {unit}"


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
    marker = "✓" if status["validated"] else "!"
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
    values = {i["key"]: getattr(args, i["key"]) for i in model.inputs}
    values = {k: v for k, v in values.items() if v is not None}
    try:
        result = model.evaluate(values)
    except ModelError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    _print_breakdown(result, model)
    _print_constraints(result)
    _print_validation(conn, model.slug)
    _print_reframe(model)
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

    _print_validation(conn, model.slug)
    _print_reframe(model)
    conn.close()
    return 0


def cmd_build(args) -> int:
    return build_mod.main([str(args.db)] if args.db else [])


def cmd_audit(args) -> int:
    return audit_mod.main([str(args.db)] if args.db else [])


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
        conn = connect(db, autobuild=False)
    except (FileNotFoundError, sqlite3.Error):
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
            help=r["label"],
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
        _add_model_flags(sp, db)
        sp.set_defaults(func=fn)

    sp = sub.add_parser("why", help="the citation chain behind every term")
    sp.add_argument("model")
    sp.set_defaults(func=cmd_why)

    sub.add_parser("build", help="compile the YAML corpus").set_defaults(func=cmd_build)
    sub.add_parser("audit", help="run the corpus gates").set_defaults(func=cmd_audit)

    sp = sub.add_parser("gui", help="serve the calculator")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8200)
    sp.set_defaults(func=cmd_gui)

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


if __name__ == "__main__":
    raise SystemExit(main())
