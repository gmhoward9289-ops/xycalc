"""Audit the corpus. Runs as its own CI job so "is every number sourced?" is a
visible check rather than a line buried in a test log.

    python -m xycalc.audit

Gates. The first two plus the unit check exit non-zero; validation never does.

  1. CITATION   every coefficient resolves to a real source.
  2. VERSION    every coefficient names what it applies to.
  3. VALIDATION every model reports its error against real observations, or
                declares itself unvalidated.
  4. UNITS      every observation.unit equals its parameter's unit.

Gate 3 reports and never fails on purpose. A model that has never been checked
against a running system is not broken — it is new, and that is the normal
state of every model on the day it is written. What would be broken is a
corpus that let it go unsaid. The number this prints should go up over time;
printing it every build is what keeps that honest.

It also prints the confidence mix without failing. `estimate` rows are the ones
resting on our own reasoning rather than on anyone's documentation, and a
sizing answer built mostly from those deserves to look different from one built
from vendor constants.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from .build import DEFAULT_DB, build
from .model import validation_status

BAR = "─" * 66


def _rows(conn: sqlite3.Connection, sql: str, *args) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(sql, args).fetchall()


def audit(db_path: Path = DEFAULT_DB, rebuild: bool = True) -> int:
    """Rebuild before auditing, always.

    Auditing a database that no longer matches the YAML is worse than not
    auditing: it reports a clean corpus that does not exist. This bit during
    development — an observation was imported, the audit was run, and it
    cheerfully reported the previous corpus's validation figures.

    The build takes well under a second. There is no saving here worth a
    wrong answer. `rebuild=False` exists only for tests that deliberately
    corrupt a built database.
    """
    if rebuild or not db_path.exists():
        build(db_path)
        print()

    conn = sqlite3.connect(db_path)
    failures: list[str] = []

    # -- gate 1: citation -------------------------------------------------
    orphans = _rows(
        conn,
        "SELECT c.slug FROM coefficient c "
        "LEFT JOIN source s ON s.id = c.source_id WHERE s.id IS NULL",
    )
    for r in orphans:
        failures.append(f"coefficient '{r['slug']}' cites a source that does not exist")

    obs_orphans = _rows(
        conn,
        "SELECT o.slug FROM observation o "
        "LEFT JOIN source s ON s.id = o.source_id WHERE s.id IS NULL",
    )
    for r in obs_orphans:
        failures.append(f"observation '{r['slug']}' cites a source that does not exist")

    # -- gate 2: version --------------------------------------------------
    unversioned = _rows(
        conn,
        "SELECT slug FROM coefficient "
        "WHERE applies_to IS NULL OR TRIM(applies_to) = ''",
    )
    for r in unversioned:
        failures.append(
            f"coefficient '{r['slug']}' has no applies_to — which versions is "
            f"it true for?"
        )

    # -- structural: a model with no floor is all multiplier and no base ---
    floorless = _rows(
        conn,
        "SELECT m.slug FROM model m "
        "WHERE NOT EXISTS (SELECT 1 FROM model_term t "
        "                  WHERE t.model_id = m.id AND t.role = 'floor')",
    )
    for r in floorless:
        failures.append(
            f"model '{r['slug']}' has no floor term — nothing for its "
            f"amplifiers to multiply"
        )

    # -- units: an observation in the wrong unit poisons error percentages --
    unit_mismatch = _rows(
        conn,
        "SELECT o.slug AS slug, o.unit AS obs_unit, "
        "       p.slug AS parameter, p.unit AS param_unit "
        "FROM observation o "
        "JOIN parameter p ON p.id = o.parameter_id "
        "WHERE o.unit != p.unit",
    )
    for r in unit_mismatch:
        failures.append(
            f"observation '{r['slug']}' unit '{r['obs_unit']}' does not match "
            f"parameter '{r['parameter']}' unit '{r['param_unit']}'"
        )

    # -- report -----------------------------------------------------------
    counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in (
            "source", "system", "parameter", "coefficient",
            "model", "observation", "guide",
        )
    }
    print("corpus")
    print(BAR)
    for name, n in counts.items():
        print(f"  {name:<14} {n:>5,}")

    print("\nconfidence mix")
    print(BAR)
    mix = _rows(
        conn,
        "SELECT confidence, COUNT(*) AS n FROM coefficient "
        "GROUP BY confidence ORDER BY n DESC",
    )
    total = sum(r["n"] for r in mix) or 1
    for r in mix:
        pct = r["n"] / total * 100
        print(f"  {r['confidence']:<14} {r['n']:>5,}   {pct:>5.1f}%")

    estimates = _rows(
        conn,
        "SELECT c.slug, p.label FROM coefficient c "
        "JOIN parameter p ON p.id = c.parameter_id "
        "WHERE c.confidence = 'estimate' ORDER BY c.slug",
    )
    if estimates:
        print(f"\n  {len(estimates)} coefficient(s) rest on our own reasoning:")
        for r in estimates:
            print(f"    - {r['slug']}  ({r['label']})")

    # -- gate 3: validation, reported, never fatal ------------------------
    print("\nvalidation")
    print(BAR)
    models = [r["slug"] for r in _rows(conn, "SELECT slug FROM model ORDER BY slug")]
    if not models:
        print("  (no models yet)")
    for slug in models:
        print(f"  {slug:<24} {validation_status(conn, slug)['text']}")

    local = conn.execute(
        "SELECT COUNT(*) FROM observation WHERE origin = 'local'"
    ).fetchone()[0]
    if local:
        print(f"\n  {local} observation(s) came from local/ and are not published")

    conn.close()

    print()
    if failures:
        print(f"AUDIT FAILED — {len(failures)} problem(s)", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("audit passed: every number cites a source and names its versions")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    return audit(Path(argv[0]) if argv else DEFAULT_DB)


if __name__ == "__main__":
    raise SystemExit(main())
