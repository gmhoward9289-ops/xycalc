"""Evaluate a sizing model, and show its work.

A model is a sequence of terms read out of the database, not code. Adding a
system means adding YAML; nothing here knows what MongoDB is.

    FLOOR       summed into a base -- the irreducible requirement
    AMPLIFIER   applied in order -- what raises it above the floor
    HEADROOM    added after -- what the tail costs rather than the mean
    CONSTRAINT  never enters the arithmetic; bounds and qualifies the answer

Every value carries a lo/mode/hi band the whole way through, because a point
estimate of a sizing question is a claim nobody can honestly make. The band
arithmetic has one trap worth naming: dividing by a *fraction* inverts it. A
smaller usable-cache fraction means a LARGER requirement, so the high end of
the result comes from the low end of the fraction. Getting that backwards
would quietly report a reassuring band that is wrong in the dangerous
direction.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

_UNITS = {
    "b": 1,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
}


class ModelError(Exception):
    pass


def parse_bytes(text: str | float | int) -> float:
    """'500GB' -> 5e11. Decimal by default, because that is what db.stats()
    reports and what a vendor's sizing table means; KiB/MiB/GiB are honoured
    when written explicitly."""
    if isinstance(text, (int, float)):
        return float(text)
    m = re.fullmatch(r"\s*([0-9.]+)\s*([a-zA-Z]*)\s*", str(text))
    if not m:
        raise ModelError(f"cannot read a size from {text!r}")
    value, unit = float(m.group(1)), m.group(2).lower()
    if not unit:
        return value
    if unit not in _UNITS:
        raise ModelError(f"unknown unit {m.group(2)!r} in {text!r}")
    return value * _UNITS[unit]


def format_bytes(n: float) -> str:
    for unit, size in (("TB", 1000**4), ("GB", 1000**3), ("MB", 1000**2)):
        if abs(n) >= size:
            return f"{n / size:,.1f} {unit}"
    return f"{n:,.0f} B"


@dataclass
class Term:
    key: str
    label: str
    role: str
    apply: str
    input_key: str | None
    optional: bool
    rationale: str
    # Null for input terms, which take their value from the caller.
    coefficient: str | None
    coeff_lo: float | None
    coeff_mode: float | None
    coeff_hi: float | None
    unit: str | None
    confidence: str | None
    applies_to: str | None
    source: str | None
    source_title: str | None
    source_url: str | None
    quote: str | None


@dataclass
class Step:
    """One term's effect, with the running total after it. This is the whole
    audit trail the CLI and the web page render -- an answer nobody can take
    apart is not much better than a guess."""

    term: Term
    contribution: str          # human-readable: "x 3.00", "+ 12.0 GB"
    lo: float
    mode: float
    hi: float
    skipped: bool = False
    skip_reason: str | None = None


@dataclass
class Result:
    model: str
    lo: float
    mode: float
    hi: float
    unit: str
    steps: list[Step] = field(default_factory=list)
    constraints: list[Term] = field(default_factory=list)
    inputs: dict = field(default_factory=dict)


@dataclass
class Model:
    slug: str
    question: str
    system: str
    summary: str | None
    reframe: str | None
    output_unit: str
    output_parameter: str
    inputs: list[dict]
    terms: list[Term]

    # -- loading ----------------------------------------------------------

    @classmethod
    def load(cls, conn: sqlite3.Connection, slug: str) -> "Model":
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT m.*, sy.slug AS system_slug, p.unit AS out_unit, "
            "       p.slug AS out_param "
            "FROM model m "
            "JOIN system sy ON sy.id = m.system_id "
            "JOIN parameter p ON p.id = m.output_parameter_id "
            "WHERE m.slug = ?",
            (slug,),
        ).fetchone()
        if row is None:
            known = [r[0] for r in conn.execute("SELECT slug FROM model ORDER BY slug")]
            raise ModelError(
                f"no model '{slug}'. Available: {', '.join(known) or '(none)'}"
            )

        inputs = [
            dict(r)
            for r in conn.execute(
                "SELECT key, label, unit, required, default_value, help "
                "FROM model_input WHERE model_id = ? ORDER BY sequence",
                (row["id"],),
            )
        ]

        terms = [
            Term(
                key=r["key"],
                label=r["label"],
                role=r["role"],
                apply=r["apply"],
                input_key=r["input_key"],
                optional=bool(r["optional"]),
                rationale=r["rationale"],
                coefficient=r["coeff_slug"],
                coeff_lo=r["value_lo"],
                coeff_mode=r["value_mode"],
                coeff_hi=r["value_hi"],
                unit=r["unit"],
                confidence=r["confidence"],
                applies_to=r["applies_to"],
                source=r["source_slug"],
                source_title=r["source_title"],
                source_url=r["source_url"],
                quote=r["quote"],
            )
            for r in conn.execute(
                "SELECT t.key, t.label, t.role, t.apply, t.input_key, t.optional, "
                "       t.rationale, "
                "       c.slug AS coeff_slug, c.value_lo, c.value_mode, c.value_hi, "
                "       c.confidence, c.applies_to, c.quote, "
                "       p.unit, "
                "       s.slug AS source_slug, s.title AS source_title, "
                "       s.url AS source_url "
                "FROM model_term t "
                "LEFT JOIN coefficient c ON c.id = t.coefficient_id "
                "LEFT JOIN parameter p   ON p.id = c.parameter_id "
                "LEFT JOIN source s      ON s.id = c.source_id "
                "WHERE t.model_id = ? ORDER BY t.sequence",
                (row["id"],),
            )
        ]

        return cls(
            slug=row["slug"],
            question=row["question"],
            system=row["system_slug"],
            summary=row["summary"],
            reframe=row["reframe"],
            output_unit=row["out_unit"],
            output_parameter=row["out_param"],
            inputs=inputs,
            terms=terms,
        )

    @classmethod
    def all(cls, conn: sqlite3.Connection) -> list[str]:
        return [r[0] for r in conn.execute("SELECT slug FROM model ORDER BY slug")]

    # -- evaluation -------------------------------------------------------

    def evaluate(self, values: dict) -> Result:
        """Run the model. `values` maps input keys to sizes or numbers."""
        supplied = self._coerce_inputs(values)
        lo = mode = hi = 0.0
        steps: list[Step] = []
        constraints: list[Term] = []

        for term in self.terms:
            if term.role == "constraint":
                constraints.append(term)
                continue

            if term.apply == "input":
                v = supplied.get(term.input_key)
                if v is None:
                    if term.optional:
                        steps.append(
                            Step(term, "—", lo, mode, hi, True, "not supplied")
                        )
                        continue
                    raise ModelError(f"{self.slug}: input '{term.input_key}' required")
                lo, mode, hi = lo + v, mode + v, hi + v
                steps.append(Step(term, f"+ {format_bytes(v)}", lo, mode, hi))
                continue

            clo, cmode, chi = term.coeff_lo, term.coeff_mode, term.coeff_hi

            if term.apply == "multiply":
                lo, mode, hi = lo * clo, mode * cmode, hi * chi
                contribution = f"x {cmode:g}" + (
                    f" ({clo:g}–{chi:g})" if clo != chi else ""
                )

            elif term.apply == "divide_by_fraction":
                # The inversion. Low usable fraction -> high requirement.
                if not (clo and chi):
                    raise ModelError(f"{term.key}: fraction cannot be zero")
                lo, mode, hi = lo / (chi / 100), mode / (cmode / 100), hi / (clo / 100)
                contribution = f"÷ {cmode:g}%" + (
                    f" ({clo:g}–{chi:g}%)" if clo != chi else ""
                )

            elif term.apply == "add_bytes":
                lo, mode, hi = lo + clo, mode + cmode, hi + chi
                contribution = f"+ {format_bytes(cmode)}"

            elif term.apply == "add_fraction":
                lo, mode, hi = (
                    lo * (1 + clo / 100),
                    mode * (1 + cmode / 100),
                    hi * (1 + chi / 100),
                )
                contribution = f"+ {cmode:g}%"

            else:  # pragma: no cover - CHECK constraint keeps this unreachable
                raise ModelError(f"{term.key}: unknown apply '{term.apply}'")

            steps.append(Step(term, contribution, lo, mode, hi))

        return Result(
            model=self.slug,
            lo=lo,
            mode=mode,
            hi=hi,
            unit=self.output_unit,
            steps=steps,
            constraints=constraints,
            inputs=supplied,
        )

    def _coerce_inputs(self, values: dict) -> dict:
        declared = {i["key"]: i for i in self.inputs}
        for key in values:
            if key not in declared:
                raise ModelError(
                    f"{self.slug}: unknown input '{key}'. "
                    f"Accepts: {', '.join(declared) or '(none)'}"
                )
        out: dict = {}
        for key, spec in declared.items():
            raw = values.get(key, spec.get("default_value"))
            if raw is None:
                if spec["required"]:
                    raise ModelError(f"{self.slug}: input '{key}' is required")
                continue
            out[key] = parse_bytes(raw) if spec["unit"] == "bytes" else float(raw)
        return out


def headroom(result: Result, available: float) -> dict:
    """How much margin is left, and what it means.

    Deliberately reported against the whole band. `available` sitting above the
    mode but below the high end is the interesting case and the one a single
    number hides: it means the sizing works if every uncertain coefficient
    lands favourably, and not otherwise.
    """
    util_mode = result.mode / available * 100 if available else float("inf")
    util_hi = result.hi / available * 100 if available else float("inf")

    if available >= result.hi:
        verdict = "covered across the whole band"
    elif available >= result.mode:
        verdict = "covers the mode but not the high end"
    elif available >= result.lo:
        verdict = "below the mode — undersized unless the estimates are generous"
    else:
        verdict = "below the entire band — undersized"

    return {
        "available": available,
        "required_lo": result.lo,
        "required_mode": result.mode,
        "required_hi": result.hi,
        "utilisation_mode_pct": util_mode,
        "utilisation_hi_pct": util_hi,
        "margin_mode": available - result.mode,
        "verdict": verdict,
    }


def validation_status(conn: sqlite3.Connection, model_slug: str) -> dict:
    """Say plainly how much reality this model has been checked against.

    A model with no cases is unvalidated, and every surface -- CLI, API, web
    page -- has to say so. Silence would read as confidence.
    """
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT cases, within_band, mean_abs_error_pct "
        "FROM v_model_validation WHERE model = ?",
        (model_slug,),
    ).fetchone()
    if row is None or not row["cases"]:
        return {
            "validated": False,
            "cases": 0,
            "text": "unvalidated (n=0) — no observation has ever been checked "
            "against this model",
        }
    return {
        "validated": True,
        "cases": row["cases"],
        "within_band": row["within_band"],
        "mean_abs_error_pct": row["mean_abs_error_pct"],
        "text": (
            f"validated (n={row['cases']}, {row['within_band']} within band, "
            f"mean absolute error {row['mean_abs_error_pct']:.1f}%)"
        ),
    }
