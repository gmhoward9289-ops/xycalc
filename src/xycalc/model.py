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

import math
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import yaml

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


# One decimal point, optional en-US thousands separators. `[0-9.]+` used to
# accept "1.2.3" (Python then threw; JS parseFloat silently returned 1.2) and
# to reject the comma in format_quantity's "3,000 iops". parse and format have
# to be inverses or the calculator's own scrub-commit corrupts the answer.
_AMOUNT = re.compile(
    r"\s*((?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?|\.[0-9]+)"
    r"\s*([a-zA-Z/%]*)\s*"
)


def _split_amount(text: str) -> tuple[float, str]:
    m = _AMOUNT.fullmatch(text)
    if not m:
        raise ModelError(f"cannot read a size from {text!r}")
    return float(m.group(1).replace(",", "")), m.group(2)


def parse_number(text: str | float | int) -> float:
    """Scalar half of parse_bytes: strip thousands separators, one decimal.

    format_quantity(3000, "iops") is "3,000 iops"; this reads it back as 3000
    rather than 3. Units are ignored — the number is what the model wants.
    """
    if isinstance(text, (int, float)):
        return float(text)
    try:
        value, _unit = _split_amount(str(text))
    except ModelError:
        raise ModelError(f"cannot read a number from {text!r}") from None
    return value


def parse_bytes(text: str | float | int) -> float:
    """'500GB' -> 5e11. Decimal by default, because that is what db.stats()
    reports and what a vendor's sizing table means; KiB/MiB/GiB are honoured
    when written explicitly."""
    if isinstance(text, (int, float)):
        return float(text)
    value, unit_raw = _split_amount(str(text))
    unit = unit_raw.lower()
    if not unit:
        return value
    if unit not in _UNITS:
        raise ModelError(f"unknown unit {unit_raw!r} in {text!r}")
    return value * _UNITS[unit]


# Mirrors build.py's own DATA computation rather than importing it, so this
# module has no dependency on the build pipeline -- a scenario only ever
# references models that build.py has already loaded into the database.
_SCENARIOS_PATH = Path(__file__).parent.parent.parent / "data" / "scenarios.yaml"


# Units whose fractional part is noise. "1,280.00 ops/s" implies a precision
# the model does not have, and half a ticket does not exist.
_INTEGRAL_UNITS = {"iops", "count", "ops/s", "tickets"}


def format_quantity(n: float, unit: str) -> str:
    """Render a figure in the unit it is actually in.

    Every model before EBS output bytes, so the step formatter called
    format_bytes unconditionally and nobody noticed. The first model with a
    different unit rendered "4,000 IOPS" as "+ 4,000 B". Units are not
    decoration here — the corpus keeps dataSize and storageSize apart as
    separate parameters precisely so quantities cannot be confused, and the
    display had been quietly undoing that.
    """
    if unit == "bytes":
        return format_bytes(n)
    if unit == "percent":
        return f"{n:,.1f}%"
    if unit in _INTEGRAL_UNITS:
        return f"{n:,.0f} {unit}"
    return f"{n:,.2f} {unit}"


def _sensitivity_sentence(terms: list[TermSensitivity]) -> str:
    """Human ranking: 'the band is 80% decompression into cache, 20% …'."""
    if not terms:
        return "no coefficient band contributes to the answer's spread"
    parts = []
    for t in terms:
        pct = int(round(t.share * 100))
        if pct <= 0:
            continue
        parts.append(f"{pct}% {t.label.lower()}")
    if not parts:
        return "no coefficient band contributes to the answer's spread"
    return "the band is " + ", ".join(parts)


def format_bytes(n: float) -> str:
    """One decimal, rounding half away from zero.

    Not `f"{v:,.1f}"`. Python's format rounds half to EVEN, so 1.25 TB renders
    as "1.2 TB" while the web UI's toLocaleString renders the same float as
    "1.3 TB" — and 1.25 TB is precisely what this corpus's first worked example
    produces. One figure displayed two ways across two surfaces is the kind of
    drift a project about trustworthy numbers cannot have.
    """
    for unit, size in (("TB", 1000**4), ("GB", 1000**3), ("MB", 1000**2)):
        if abs(n) >= size:
            v = n / size
            v = math.copysign(math.floor(abs(v) * 10 + 0.5) / 10, v)
            return f"{v:,.1f} {unit}"
    return f"{n:,.0f} B"


@dataclass
class Term:
    key: str
    label: str
    role: str
    apply: str
    input_key: str | None
    input_key_b: str | None
    optional: bool
    when_input: str | None
    unless_input: str | None
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
class TermSensitivity:
    """One coefficient's contribution to the answer's spread.

    `answer_at_coeff_lo` / `answer_at_coeff_hi` are the point answers produced
    by pinning this coefficient to its cited lo or hi (as a point band) while
    every other coefficient sits at mode. For `divide_by_fraction` the lo
    coefficient is the *high* answer — that inversion lives in `evaluate`, not
    here. `span` is the absolute difference of those two answers.
    """

    key: str
    label: str
    span: float
    share: float
    answer_at_coeff_lo: float
    answer_at_coeff_hi: float
    coeff_lo: float
    coeff_hi: float


@dataclass
class Sensitivity:
    """Ranked one-at-a-time coefficient sweep. Inputs stay fixed."""

    terms: list[TermSensitivity]
    total_span: float
    unit: str
    sentence: str
    measure_next_key: str | None
    measure_next_label: str | None


@dataclass
class Model:
    slug: str
    question: str
    system: str
    summary: str | None
    reframe: str | None
    notes: str | None
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
                input_key_b=r["input_key_b"],
                optional=bool(r["optional"]),
                when_input=r["when_input"],
                unless_input=r["unless_input"],
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
                "SELECT t.key, t.label, t.role, t.apply, t.input_key, t.input_key_b, "
                "       t.optional, t.when_input, t.unless_input, t.rationale, "
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
            notes=row["notes"] if "notes" in row.keys() else None,
            output_unit=row["out_unit"],
            output_parameter=row["out_param"],
            inputs=inputs,
            terms=terms,
        )

    @classmethod
    def all(cls, conn: sqlite3.Connection) -> list[str]:
        return [r[0] for r in conn.execute("SELECT slug FROM model ORDER BY slug")]

    # -- evaluation -------------------------------------------------------

    def evaluate(
        self,
        values: dict,
        *,
        coeff_bands: dict[str, tuple[float, float, float]] | None = None,
    ) -> Result:
        """Run the model. `values` maps input keys to sizes or numbers.

        `coeff_bands` optionally replaces a term's cited (lo, mode, hi) for
        that run. Sensitivity analysis pins every other coefficient at mode
        and walks one term across its band through this argument — the apply
        arithmetic, including fraction inversion, stays on this path.
        """
        supplied = self._coerce_inputs(values)
        declared_units = {i["key"]: i["unit"] for i in self.inputs}
        lo = mode = hi = 0.0
        steps: list[Step] = []
        constraints: list[Term] = []

        for term in self.terms:
            if term.role == "constraint":
                constraints.append(term)
                continue

            skip_reason = self._term_skip_reason(term, supplied)
            if skip_reason:
                steps.append(
                    Step(term, "—", lo, mode, hi, True, skip_reason)
                )
                continue

            in_unit = declared_units.get(term.input_key) or term.unit or self.output_unit

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
                steps.append(
                    Step(term, f"+ {format_quantity(v, in_unit)}", lo, mode, hi)
                )
                continue

            if term.apply == "divide_by_input":
                v = supplied.get(term.input_key)
                if v is None:
                    if term.optional:
                        steps.append(Step(term, "—", lo, mode, hi, True, "not supplied"))
                        continue
                    raise ModelError(f"{self.slug}: input '{term.input_key}' required")
                if not v:
                    raise ModelError(
                        f"{self.slug}: '{term.input_key}' cannot be zero — "
                        f"dividing by it would report an infinite ceiling"
                    )
                # No band inversion here. Dividing by a FRACTION inverts the
                # band because the fraction itself carries lo/mode/hi; a
                # caller-supplied scalar has one value, so all three ends move
                # together.
                lo, mode, hi = lo / v, mode / v, hi / v
                steps.append(
                    Step(term, f"÷ {format_quantity(v, in_unit)}", lo, mode, hi)
                )
                continue

            if term.apply == "multiply_by_input":
                v = supplied.get(term.input_key)
                if v is None:
                    if term.optional:
                        steps.append(Step(term, "—", lo, mode, hi, True, "not supplied"))
                        continue
                    raise ModelError(f"{self.slug}: input '{term.input_key}' required")
                if not v:
                    raise ModelError(
                        f"{self.slug}: '{term.input_key}' cannot be zero — "
                        f"multiplying by it would zero the answer"
                    )
                lo, mode, hi = lo * v, mode * v, hi * v
                steps.append(
                    Step(term, f"x {format_quantity(v, in_unit)}", lo, mode, hi)
                )
                continue

            if term.apply == "add_fraction_from_input":
                v = supplied.get(term.input_key)
                if v is None:
                    if term.optional:
                        steps.append(Step(term, "—", lo, mode, hi, True, "not supplied"))
                        continue
                    raise ModelError(f"{self.slug}: input '{term.input_key}' required")
                # A caller-supplied percentage, not a cited fraction, so no
                # band inversion: it is one number, and all three ends move
                # together exactly as divide_by_input's scalar case does.
                factor = 1 + v / 100
                lo, mode, hi = lo * factor, mode * factor, hi * factor
                steps.append(Step(term, f"+ {v:g}%", lo, mode, hi))
                continue

            if term.apply == "add_product_of_inputs":
                a = supplied.get(term.input_key)
                b = supplied.get(term.input_key_b)
                if a is None and b is None:
                    if term.optional:
                        steps.append(
                            Step(term, "—", lo, mode, hi, True, "not supplied")
                        )
                        continue
                    raise ModelError(
                        f"{self.slug}: inputs '{term.input_key}' and "
                        f"'{term.input_key_b}' are required"
                    )
                if a is None or b is None:
                    missing = term.input_key if a is None else term.input_key_b
                    raise ModelError(
                        f"{self.slug}: '{term.input_key}' and "
                        f"'{term.input_key_b}' must be supplied together "
                        f"(missing '{missing}')"
                    )
                product = a * b
                lo, mode, hi = lo + product, mode + product, hi + product
                a_unit = declared_units.get(term.input_key) or "count"
                b_unit = declared_units.get(term.input_key_b) or self.output_unit
                steps.append(
                    Step(
                        term,
                        f"+ {format_quantity(a, a_unit)} × "
                        f"{format_quantity(b, b_unit)}",
                        lo,
                        mode,
                        hi,
                    )
                )
                continue

            clo, cmode, chi = term.coeff_lo, term.coeff_mode, term.coeff_hi
            if coeff_bands and term.key in coeff_bands:
                clo, cmode, chi = coeff_bands[term.key]

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
                contribution = f"+ {format_quantity(cmode, self.output_unit)}"

            elif term.apply in ("floor_at", "cap_at"):
                # A bound applies to each end of the band independently, which
                # can COLLAPSE it: if a floor sits above hi, all three ends meet
                # and the answer stops looking uncertain when it still is. That
                # is honest -- the bound really does determine the value -- but
                # it must be visible, so the step records whether it happened.
                # Only annotate when THIS bound collapsed a real band; a scalar
                # input already made lo == hi before we got here.
                was_point = lo == hi
                if term.apply == "floor_at":
                    lo, mode, hi = max(lo, clo), max(mode, cmode), max(hi, chi)
                    contribution = f"≥ {format_quantity(cmode, in_unit)}"
                else:
                    lo, mode, hi = min(lo, clo), min(mode, cmode), min(hi, chi)
                    contribution = f"≤ {format_quantity(cmode, in_unit)}"
                if lo == hi and not was_point:
                    contribution += " (band collapsed)"

            elif term.apply == "set_from_coefficient":
                lo, mode, hi = clo, cmode, chi
                contribution = f"= {format_quantity(cmode, in_unit)}" + (
                    f" ({format_quantity(clo, in_unit)}–{format_quantity(chi, in_unit)})"
                    if clo != chi
                    else ""
                )

            elif term.apply == "cap_at_throughput":
                io_size = supplied.get(term.input_key)
                if io_size is None:
                    raise ModelError(
                        f"{self.slug}: input '{term.input_key}' required for "
                        f"throughput crossover"
                    )
                if not io_size:
                    raise ModelError(
                        f"{self.slug}: '{term.input_key}' cannot be zero — "
                        f"throughput crossover is undefined"
                    )
                cap_lo = clo * 1024 / io_size
                cap_mode = cmode * 1024 / io_size
                cap_hi = chi * 1024 / io_size
                was_point = lo == hi
                lo, mode, hi = (
                    min(lo, cap_lo),
                    min(mode, cap_mode),
                    min(hi, cap_hi),
                )
                # The input is I/O size; the cap is IOPS (or whatever the
                # model outputs). Label it with the output unit, not in_unit.
                contribution = (
                    f"≤ {format_quantity(cap_mode, self.output_unit)} "
                    f"({cmode:g} MiB/s ÷ {io_size:g} KiB/op)"
                )
                if lo == hi and not was_point:
                    contribution += " (band collapsed)"

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

    def sensitivity(self, values: dict) -> Sensitivity:
        """Rank coefficients by how much each one moves the answer.

        Inputs stay fixed. Each coefficient with a real lo..hi band is walked
        from lo to hi as a point value while every other coefficient sits at
        its mode. The rank is the absolute span of the resulting answers —
        the same `evaluate` path the sizing answer uses, so fraction terms
        invert rather than having that arithmetic re-derived here.
        """
        ranked: list[TermSensitivity] = []
        for term in self.terms:
            if not self._sensitivity_candidate(term, values):
                continue
            pins = self._point_bands_at_mode(except_key=term.key)
            r_lo = self.evaluate(
                values,
                coeff_bands={**pins, term.key: (term.coeff_lo, term.coeff_lo, term.coeff_lo)},
            )
            r_hi = self.evaluate(
                values,
                coeff_bands={**pins, term.key: (term.coeff_hi, term.coeff_hi, term.coeff_hi)},
            )
            ranked.append(
                TermSensitivity(
                    key=term.key,
                    label=term.label,
                    span=abs(r_hi.mode - r_lo.mode),
                    share=0.0,
                    answer_at_coeff_lo=r_lo.mode,
                    answer_at_coeff_hi=r_hi.mode,
                    coeff_lo=term.coeff_lo,
                    coeff_hi=term.coeff_hi,
                )
            )

        ranked.sort(key=lambda t: (-t.span, t.key))
        total = sum(t.span for t in ranked)
        if total:
            for t in ranked:
                t.share = t.span / total

        contributing = [t for t in ranked if t.span > 0]
        sentence = _sensitivity_sentence(contributing)
        top = contributing[0] if contributing else None
        return Sensitivity(
            terms=ranked,
            total_span=total,
            unit=self.output_unit,
            sentence=sentence,
            measure_next_key=top.key if top else None,
            measure_next_label=top.label if top else None,
        )

    def _sensitivity_candidate(self, term: Term, values: dict) -> bool:
        if term.coefficient is None or term.role == "constraint":
            return False
        if term.coeff_lo is None or term.coeff_hi is None:
            return False
        if term.coeff_lo == term.coeff_hi:
            return False
        supplied = self._coerce_inputs(values)
        if self._term_skip_reason(term, supplied):
            return False
        return True

    def _point_bands_at_mode(
        self, *, except_key: str | None = None
    ) -> dict[str, tuple[float, float, float]]:
        pins: dict[str, tuple[float, float, float]] = {}
        for term in self.terms:
            if term.coefficient is None or term.role == "constraint":
                continue
            if term.coeff_mode is None:
                continue
            if term.key == except_key:
                continue
            pins[term.key] = (term.coeff_mode, term.coeff_mode, term.coeff_mode)
        return pins

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
            try:
                if spec["unit"] == "bytes":
                    out[key] = parse_bytes(raw)
                else:
                    try:
                        out[key] = parse_number(raw)
                    except ModelError:
                        raise ModelError(
                            f"{self.slug}: input '{key}' is not a number ({raw!r})"
                        ) from None
            except (TypeError, ValueError):
                raise ModelError(
                    f"{self.slug}: input '{key}' is not a number ({raw!r})"
                ) from None
        return out

    @staticmethod
    def _term_skip_reason(term: Term, supplied: dict) -> str | None:
        if term.when_input and term.when_input not in supplied:
            return f"'{term.when_input}' not supplied"
        if term.unless_input and term.unless_input in supplied:
            return f"'{term.unless_input}' supplied"
        return None


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


@dataclass
class InstanceSpec:
    name: str               # e.g. "r8i.24xlarge" -- read from `applies_to`,
                             # not `slug`, so it matches what `xycalc why`
                             # shows for the same coefficient.
    ram_bytes: float
    vcpu: float | None
    ebs_bandwidth_gbps: float | None
    source_title: str
    source_url: str | None


def instance_pick_rank(name: str) -> int:
    """Tie-break when two SKUs publish the same RAM.

    Unfiltered `instance_select` should keep naming r8i (current-gen) rather
    than r6i at 16–1,024 GiB. The size-to-instance scenario still names r6i
    via `family: r6i`. Lower rank wins.
    """
    n = name.lower()
    if n.startswith("r8i"):
        return 0
    if n.startswith("u7"):
        return 1
    if n.startswith("r6i"):
        return 2
    return 3


def load_instance_catalog(
    conn: sqlite3.Connection, system: str = "aws-ec2"
) -> list[InstanceSpec]:
    """The catalog `select_instance()` picks from.

    Grouped by `applies_to` rather than by coefficient slug. Slug is this
    corpus's internal identifier and can be anything; `applies_to` is the
    field the schema defines as "which variant this figure is true for," so
    using it here is what keeps the catalog from silently drifting apart from
    what a human reading `xycalc why aws.r8i...` for the same row would see.
    """
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT c.applies_to, p.slug AS param_slug, c.value_mode, "
        "       s.title AS source_title, s.url AS source_url "
        "FROM coefficient c "
        "JOIN system sy   ON sy.id = c.system_id "
        "JOIN parameter p ON p.id = c.parameter_id "
        "JOIN source s    ON s.id = c.source_id "
        "WHERE sy.slug = ?",
        (system,),
    ).fetchall()

    by_name: dict[str, dict] = {}
    for r in rows:
        name = r["applies_to"].split(",")[0].strip()
        entry = by_name.setdefault(
            name,
            {"source_title": r["source_title"], "source_url": r["source_url"]},
        )
        if r["param_slug"] == "host.ram_bytes":
            entry["ram_bytes"] = r["value_mode"]
        elif r["param_slug"] == "instance.vcpu_count":
            entry["vcpu"] = r["value_mode"]
        elif r["param_slug"] == "instance.ebs_bandwidth_gbps":
            entry["ebs_bandwidth_gbps"] = r["value_mode"]

    return sorted(
        (
            InstanceSpec(
                name=name,
                ram_bytes=e["ram_bytes"],
                vcpu=e.get("vcpu"),
                ebs_bandwidth_gbps=e.get("ebs_bandwidth_gbps"),
                source_title=e["source_title"],
                source_url=e["source_url"],
            )
            for name, e in by_name.items()
            if "ram_bytes" in e  # a row with only a vcpu coefficient is unusable
        ),
        key=lambda i: (i.ram_bytes, instance_pick_rank(i.name), i.name),
    )


# Standing internal policy, not an AWS ceiling. Until 2026-08-21 this sat at
# 1,536 GiB (r8i.48xlarge) pending a next-family decision; U7i is now in the
# aws-ec2 catalog (see data/coefficients/aws-ec2.yaml), so the default cap is
# the largest *cited* SKU (u7inh-32tb.480xlarge, 32,768 GiB). Shared by the
# CLI's `instance-select` command and the scenario chain's lookup step, so
# the two surfaces cannot silently disagree about the cutoff. Pass --max-ram
# 1536GiB to restore the old org cap, or 0 to lift it entirely.
DEFAULT_INSTANCE_CEILING = "32768GiB"


def select_instance(
    result: Result,
    catalog: list[InstanceSpec],
    family: str | None = None,
    ceiling_bytes: float | None = None,
) -> dict:
    """Which named instance is the smallest one covering a RAM requirement --
    evaluated separately at the low, mode, and high end of `result`'s band.

    Deliberately per band-end rather than against `result.mode` alone, for
    the same reason `headroom()` reports against the whole band: collapsing a
    lo/mode/hi requirement to one number before picking an instance hides
    exactly the uncertainty the rest of this corpus exists to keep visible.
    An instance that covers the mode but not the high end is a real,
    nameable risk -- "the 12xlarge fits if the estimate is right, the
    24xlarge fits regardless" -- and printing only the mode's answer would
    erase that distinction.

    `family` filters the catalog by name prefix (case-insensitive), e.g.
    "r8i", "Esv5", or "Esv6". Raises if the filtered pool is empty.

    `ceiling_bytes` is an operational policy cutoff, not a vendor fact --
    an org can stop recommending below the catalog's real technical ceiling
    (historically 1,536 GiB while U7i was undecided). When set, instances
    above it are excluded from the pool before picking, so the existing
    `exceeds_pool` / "custom sizing" branch fires at the POLICY ceiling, not
    necessarily the family's actual maximum -- deliberately not a coefficient
    in aws-ec2.yaml, because it is a standing internal decision, not a
    sourced AWS spec.
    """
    pool = [
        i for i in catalog if not family or i.name.lower().startswith(family.lower())
    ]
    if not pool:
        raise ModelError(f"no instances in catalog matching family '{family}'")
    if ceiling_bytes is not None:
        capped = [i for i in pool if i.ram_bytes <= ceiling_bytes]
        if not capped:
            raise ModelError(
                f"ceiling {ceiling_bytes:g} bytes excludes every instance "
                f"matching family '{family}'"
            )
        pool = capped

    def pick(need: float) -> InstanceSpec | None:
        fits = [i for i in pool if i.ram_bytes >= need]
        return (
            min(fits, key=lambda i: (i.ram_bytes, instance_pick_rank(i.name), i.name))
            if fits
            else None
        )

    largest = max(pool, key=lambda i: (i.ram_bytes, -instance_pick_rank(i.name), i.name))

    return {
        "required_lo": result.lo,
        "required_mode": result.mode,
        "required_hi": result.hi,
        "pick_lo": pick(result.lo),
        "pick_mode": pick(result.mode),
        "pick_hi": pick(result.hi),
        "largest_in_pool": largest,
        "exceeds_pool": result.hi > largest.ram_bytes,
    }


def load_scenarios() -> list[dict]:
    """Every declared scenario, in file order. Read straight from YAML rather
    than through the build/SQLite pipeline: a scenario has no coefficient or
    citation of its own to audit, only references to models that are already
    audited on their own, so putting it through the same schema/build/audit
    machinery as a sourced figure would buy nothing and cost a migration."""
    if not _SCENARIOS_PATH.is_file():
        return []
    with _SCENARIOS_PATH.open(encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    return doc.get("scenarios", [])


def get_scenario(slug: str) -> dict:
    for s in load_scenarios():
        if s["slug"] == slug:
            return s
    known = [s["slug"] for s in load_scenarios() if not s.get("disabled")]
    raise ModelError(f"no scenario '{slug}'. Available: {', '.join(known) or '(none)'}")


def _coeff_chart_row(conn: sqlite3.Connection, slug: str) -> sqlite3.Row | None:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        "SELECT c.slug, c.value_lo, c.value_mode, c.value_hi, "
        "       s.slug AS source, s.url AS source_url "
        "FROM coefficient c JOIN source s ON s.id = c.source_id "
        "WHERE c.slug = ?",
        (slug,),
    ).fetchone()


def nvd_publication_chart(conn: sqlite3.Connection) -> dict | None:
    """Cited annual CVE counts for the instance-sizing chart.

    Returns None if the corpus is missing any of the NVD annual rows — the
    UI then omits the chart rather than inventing a series.
    """
    annual = []
    for year in (2023, 2024, 2025):
        row = _coeff_chart_row(conn, f"nvd.cves-published-{year}")
        if row is None:
            return None
        point = {"year": year, "count": int(row["value_mode"])}
        vendor = _coeff_chart_row(conn, f"nvd.microsoft-cves-published-{year}")
        if vendor is not None:
            point["microsoft"] = int(vendor["value_mode"])
            point["microsoft_source"] = vendor["source"]
            point["microsoft_source_url"] = vendor["source_url"]
        annual.append(point)
    growth = _coeff_chart_row(conn, "nvd.cve-yoy-growth-pct")
    cumulative = _coeff_chart_row(conn, "nvd.cves-cumulative-through-2025")
    latest = _coeff_chart_row(conn, "nvd.cves-published-2025")
    if not (growth and cumulative and latest):
        return None
    return {
        "annual": annual,
        "cumulative_2025": int(cumulative["value_mode"]),
        "growth_pct": {
            "lo": growth["value_lo"],
            "mode": growth["value_mode"],
            "hi": growth["value_hi"],
        },
        "source": latest["source"],
        "source_url": latest["source_url"],
        "microsoft_note": (
            "Microsoft is plotted only for years Jerry Gamblin's reviews "
            "publish a vendor table (2025: 1,255). The 2023–2024 reviews do "
            "not; Patch Tuesday and BeyondTrust totals count different things "
            "and are not mixed in."
        ),
    }


def scenario_corpus_gap(conn: sqlite3.Connection, scenario: dict) -> str | None:
    """Return why a scenario cannot run yet, or None when every step's model is built."""
    for step in scenario.get("steps", []):
        if step.get("kind", "model") != "model":
            continue
        slug = step["model"]
        try:
            Model.load(conn, slug)
        except ModelError:
            return (
                f"{slug} is not in the built corpus yet — run "
                f"`xycalc build` and restart the server"
            )
    return None


def describe_scenarios(conn: sqlite3.Connection) -> list[dict]:
    """The scenario picker payload — shared by the live API and the static export."""
    out = []
    for s in load_scenarios():
        entry = {
            "slug": s["slug"],
            "label": s["label"],
            "summary": s.get("summary"),
            "default": bool(s.get("default")),
            "disabled": bool(s.get("disabled")),
            "note": s.get("note"),
            "see_also": s.get("see_also", []),
            "extra_inputs": s.get("extra_inputs", []),
            "steps": s.get("steps", []),
            "input_sections": s.get("input_sections", []),
        }
        gap = None if entry["disabled"] else scenario_corpus_gap(conn, s)
        if gap:
            entry["disabled"] = True
            entry["default"] = False
            entry["note"] = gap
        if not entry["disabled"]:
            form_inputs = scenario_form_inputs(conn, s)
            input_map = {i["key"]: i for i in form_inputs}
            for extra in entry.get("extra_inputs", []):
                input_map[extra["key"]] = extra
            entry["inputs"] = form_inputs
            sections = []
            for sec in s.get("input_sections", []):
                sections.append(
                    {
                        "title": sec["title"],
                        "inputs": [
                            input_map[k]
                            for k in sec["keys"]
                            if k in input_map
                        ],
                    }
                )
            if sections:
                entry["input_sections"] = sections
            first_model = next(
                (
                    st["model"]
                    for st in s["steps"]
                    if st.get("kind", "model") == "model"
                ),
                None,
            )
            if first_model:
                m = Model.load(conn, first_model)
                entry["question"] = m.question
                entry["unit"] = m.output_unit
            if s.get("slug") == "mongodb.size-to-instance":
                chart = nvd_publication_chart(conn)
                if chart:
                    entry["nvd_chart"] = chart
        out.append(entry)
    return out


def scenario_form_inputs(conn: sqlite3.Connection, scenario: dict) -> list[dict]:
    """Inputs the scenario form should collect.

    Every model step whose inputs are not fed from a previous step's band,
    plus any scenario-level extra_inputs. Duplicates are dropped in step order.
    """
    fed: set[str] = set()
    for step in scenario.get("steps", []):
        if step.get("kind", "model") != "model":
            continue
        for key, source in (step.get("feed") or {}).items():
            if source == "previous":
                fed.add(key)

    seen: set[str] = set()
    out: list[dict] = []

    form_models = scenario.get("form_models")
    if form_models:
        for entry in form_models:
            if isinstance(entry, str):
                slug, only = entry, None
            else:
                slug, only = entry["model"], entry.get("inputs")
            model = Model.load(conn, slug)
            for inp in model.inputs:
                if inp["key"] in fed or inp["key"] in seen:
                    continue
                if only is not None and inp["key"] not in only:
                    continue
                seen.add(inp["key"])
                out.append(inp)
    else:
        for step in scenario.get("steps", []):
            if step.get("kind", "model") != "model":
                continue
            model = Model.load(conn, step["model"])
            skip_form = set(step.get("defaults_from_coefficient") or {})
            skip_form.update(step.get("defaults") or {})
            for inp in model.inputs:
                if inp["key"] in fed or inp["key"] in seen or inp["key"] in skip_form:
                    continue
                seen.add(inp["key"])
                out.append(inp)

    for inp in scenario.get("extra_inputs", []):
        if inp["key"] in seen:
            continue
        seen.add(inp["key"])
        out.append(inp)
    return out


@dataclass
class ScenarioStepResult:
    """One step of a chain, with enough attached to render exactly what a
    standalone `evaluate()` call would: a step is a `Result` reader never
    sees anything less-audited just because it arrived by chain rather than by
    typing."""

    kind: str  # "model" | "lookup"
    slug: str
    chained: bool  # True if this step's band came from the previous step's
                   # band rather than from caller-supplied input
    model: "Model | None" = None
    result: Result | None = None
    instance_pick: dict | None = None
    gp3_spec: dict | None = None
    headroom: dict | None = None
    assumed_inputs: dict | None = None
    assumed_note: str | None = None
    family: str | None = None


def gp3_volume_spec(volume_bytes: float) -> dict:
    """gp3 baseline and provisionable ceilings for a volume size.

    Figures match the documented coefficients in data/coefficients/ebs.yaml:
    3,000 IOPS and 125 MiB/s included; up to 80,000 IOPS at 500 per GiB of
    volume; up to 2,000 MiB/s throughput. EBS sizes volumes in GiB.
    """
    gib = volume_bytes / (1024**3)
    max_iops = min(80_000.0, 500.0 * gib)
    return {
        "volume_bytes": volume_bytes,
        "volume_gib": gib,
        "baseline_iops": 3000.0,
        "max_provisionable_iops": max_iops,
        "baseline_throughput_mibps": 125.0,
        "max_throughput_mibps": 2000.0,
    }


def _attach_instance_ebs(spec: dict, instance: InstanceSpec | None) -> dict:
    """Qualify gp3 catalog ceilings against the RAM pick's own EBS pipe.

    80,000 IOPS / 2,000 MiB/s is what one gp3 volume can be configured for.
    r8i.large's published EBS bandwidth is 10 Gbps (1,250 MiB/s) — the
    volume catalog number is only reachable on a larger size.
    """
    if instance is None:
        return spec
    spec = dict(spec)
    spec["instance_name"] = instance.name
    if instance.ebs_bandwidth_gbps is None:
        return spec
    spec["instance_ebs_bandwidth_gbps"] = instance.ebs_bandwidth_gbps
    spec["instance_ebs_throughput_mibps"] = instance.ebs_bandwidth_gbps * 125.0
    spec["usable_throughput_mibps"] = min(
        spec["max_throughput_mibps"], spec["instance_ebs_throughput_mibps"]
    )
    return spec


def _sum_scenario_bytes(inputs: dict, keys: list[str]) -> float:
    total = 0.0
    for key in keys:
        raw = inputs.get(key)
        if raw is None or raw == "":
            continue
        total += parse_bytes(raw)
    return total


def build_instance_sizing_summary(
    steps: list[ScenarioStepResult], inputs: dict
) -> dict:
    """Roll RAM, CPU, and gp3 disk into one panel for the instance-sizing scenario."""
    host: ScenarioStepResult | None = None
    inst: ScenarioStepResult | None = None
    azure: ScenarioStepResult | None = None
    r6i: ScenarioStepResult | None = None
    gp3: ScenarioStepResult | None = None
    ebs: ScenarioStepResult | None = None
    for s in steps:
        if s.kind == "model" and s.slug == "mongodb.host-ram":
            host = s
        elif s.kind == "lookup" and s.gp3_spec is not None:
            gp3 = s
        elif s.kind == "lookup" and s.instance_pick is not None:
            if s.slug.startswith("azure-vm"):
                azure = s
            elif (s.family or "") == "r6i":
                r6i = s
            elif inst is None or (s.slug == "aws-ec2.instance-select" and not s.family):
                inst = s
        elif s.kind == "model" and s.slug == "ebs.iops-to-provision":
            ebs = s

    summary: dict = {}
    if host and host.result:
        r = host.result
        summary["ram"] = {
            "lo": r.lo,
            "mode": r.mode,
            "hi": r.hi,
            "unit": r.unit,
        }

    if inst and inst.instance_pick:
        pick = inst.instance_pick

        def vcpu(spec) -> float | None:
            return None if spec is None else spec.vcpu

        summary["cpu"] = {
            "lo": vcpu(pick["pick_lo"]),
            "mode": vcpu(pick["pick_mode"]),
            "hi": vcpu(pick["pick_hi"]),
            "unit": "vcpu",
            "instance_lo": None if pick["pick_lo"] is None else pick["pick_lo"].name,
            "instance_mode": None if pick["pick_mode"] is None else pick["pick_mode"].name,
            "instance_hi": None if pick["pick_hi"] is None else pick["pick_hi"].name,
        }

    if azure and azure.instance_pick:
        ap = azure.instance_pick

        def azure_name(spec) -> str | None:
            return None if spec is None else spec.name

        summary["azure"] = {
            "lo": azure_name(ap["pick_lo"]),
            "mode": azure_name(ap["pick_mode"]),
            "hi": azure_name(ap["pick_hi"]),
            "exceeds_pool": ap["exceeds_pool"],
        }

    if r6i and r6i.instance_pick:
        rp = r6i.instance_pick

        def r6i_name(spec) -> str | None:
            return None if spec is None else spec.name

        largest = rp.get("largest_in_pool")
        summary["r6i"] = {
            "lo": r6i_name(rp["pick_lo"]),
            "mode": r6i_name(rp["pick_mode"]),
            "hi": r6i_name(rp["pick_hi"]),
            "exceeds_pool": rp["exceeds_pool"],
            "largest": None if largest is None else largest.name,
        }

    if gp3 and gp3.gp3_spec:
        spec = gp3.gp3_spec
        disk = {
            "volume_gib": spec["volume_gib"],
            "baseline_iops": spec["baseline_iops"],
            "max_provisionable_iops": spec["max_provisionable_iops"],
            "baseline_throughput_mibps": spec["baseline_throughput_mibps"],
            "max_throughput_mibps": spec["max_throughput_mibps"],
        }
        if ebs and ebs.result:
            disk["provisioned_iops"] = {
                "lo": ebs.result.lo,
                "mode": ebs.result.mode,
                "hi": ebs.result.hi,
            }
            disk["provisioned_iops_assumed_mean"] = bool(
                ebs.assumed_inputs and "average_iops" in ebs.assumed_inputs
            )
        if spec.get("instance_name"):
            disk["instance_name"] = spec["instance_name"]
        if spec.get("instance_ebs_bandwidth_gbps") is not None:
            disk["instance_ebs_bandwidth_gbps"] = spec["instance_ebs_bandwidth_gbps"]
            disk["usable_throughput_mibps"] = spec.get("usable_throughput_mibps")
        summary["disk"] = disk

    current: dict = {}
    for key, out_key in (
        ("current_ram", "ram"),
        ("current_vcpu", "vcpu"),
        ("current_disk_iops", "disk_iops"),
        ("current_disk_throughput", "disk_throughput_mibps"),
    ):
        raw = inputs.get(key)
        if raw is None or raw == "":
            continue
        if out_key == "ram":
            current[out_key] = parse_bytes(raw)
        else:
            current[out_key] = parse_number(raw)
    if current:
        summary["current"] = current

    return summary


def chain_evaluate(
    conn: sqlite3.Connection,
    scenario: dict,
    inputs: dict,
    available: float | None = None,
) -> list[ScenarioStepResult]:
    """Run every step of a scenario, feeding one step's whole band into the
    next rather than a single collapsed number.

    The rule, lifted from `select_instance()`: a chained input is evaluated
    once per band-end -- once with the previous step's lo, once with its mode,
    once with its hi -- and the composed band is
    ``lo = downstream(previous.lo).lo``, ``mode = downstream(previous.mode).mode``,
    ``hi = downstream(previous.hi).hi``. That composition is only honest when
    the downstream model is monotone non-decreasing in the fed input, which
    every `apply` kind in this corpus is today (see the module docstring's
    banding note); this function checks the result rather than trusting the
    assumption, and refuses to report a band that came out inverted.

    The step's rendered breakdown (`result.steps`, the per-term table) comes
    from the mode-band run. The lo/mode/hi run each other for the top-level
    answer only -- a reader wants one coherent breakdown to read alongside the
    band, not three.
    """
    out: list[ScenarioStepResult] = []
    previous: Result | None = None
    model_results: dict[str, Result] = {}
    supplied = dict(inputs)

    def _fill_defaults(step: dict) -> dict:
        assumed: dict = {}
        for key, raw in (step.get("defaults") or {}).items():
            if supplied.get(key) in (None, ""):
                supplied[key] = raw
                assumed[key] = raw
        for key, slug in (step.get("defaults_from_coefficient") or {}).items():
            if supplied.get(key) not in (None, ""):
                continue
            row = conn.execute(
                "SELECT value_mode FROM coefficient WHERE slug = ?", (slug,)
            ).fetchone()
            if row is None:
                raise ModelError(
                    f"{step.get('model', step.get('lookup'))}: "
                    f"default coefficient '{slug}' is not in the corpus"
                )
            supplied[key] = row[0]
            assumed[key] = row[0]
        return assumed

    # Headroom is reported against the last bytes-output MODEL step, not the
    # last step overall — a scenario that ends in instance-select or an IOPS
    # model has nothing for `available` RAM to be measured against there.
    last_bytes_step = None
    for s in scenario["steps"]:
        if s.get("kind", "model") != "model":
            continue
        when = s.get("when_input")
        if when and not supplied.get(when) and when not in (s.get("defaults") or {}) and when not in (s.get("defaults_from_coefficient") or {}):
            continue
        if Model.load(conn, s["model"]).output_unit == "bytes":
            last_bytes_step = s

    for step in scenario["steps"]:
        kind = step.get("kind", "model")

        when = step.get("when_input")
        if when and not supplied.get(when):
            continue

        if kind == "model":
            assumed = _fill_defaults(step)
            model = Model.load(conn, step["model"])
            feed = step.get("feed") or {}

            if not feed:
                own_keys = {i["key"] for i in model.inputs}
                scoped = {k: v for k, v in supplied.items() if k in own_keys}
                composed = model.evaluate(scoped)
            else:
                if previous is None:
                    raise ModelError(
                        f"{step['model']}: feed references 'previous' but this "
                        f"is the first step"
                    )
                fed_keys = [k for k, v in feed.items() if v == "previous"]
                # Only this step's OWN declared inputs are relevant -- `inputs`
                # is the caller's flat dict for the whole scenario, and an
                # earlier step's input key (storage_size) is not a key this
                # model recognises at all.
                own_keys = {i["key"] for i in model.inputs}

                def _run(band_value: float) -> Result:
                    merged = {k: v for k, v in supplied.items() if k in own_keys}
                    merged.update({k: band_value for k in fed_keys})
                    return model.evaluate(merged)

                r_lo, r_mode, r_hi = _run(previous.lo), _run(previous.mode), _run(previous.hi)
                if not (r_lo.lo <= r_mode.mode <= r_hi.hi):
                    raise ModelError(
                        f"{step['model']}: chained band inverted "
                        f"(lo={r_lo.lo!r}, mode={r_mode.mode!r}, hi={r_hi.hi!r}) -- "
                        f"refusing to report a band that would read as more "
                        f"confident than it is"
                    )
                composed = Result(
                    model=model.slug,
                    lo=r_lo.lo,
                    mode=r_mode.mode,
                    hi=r_hi.hi,
                    unit=r_mode.unit,
                    steps=r_mode.steps,
                    constraints=r_mode.constraints,
                    inputs=r_mode.inputs,
                )

            step_result = ScenarioStepResult(
                kind="model",
                slug=model.slug,
                chained=bool(feed),
                model=model,
                result=composed,
                assumed_inputs=assumed or None,
                assumed_note=(step.get("assumed_note") if assumed else None),
            )
            if available is not None and step is last_bytes_step:
                step_result.headroom = headroom(composed, available)
            out.append(step_result)
            previous = composed
            model_results[model.slug] = composed

        elif kind == "lookup":
            lookup = step.get("lookup")
            if lookup == "gp3_spec":
                keys = step.get("sum_inputs") or ["storage_size"]
                total = _sum_scenario_bytes(supplied, keys)
                sum_model = step.get("sum_model")
                if sum_model:
                    prior = model_results.get(sum_model)
                    if prior is None:
                        raise ModelError(
                            f"gp3_spec: sum_model '{sum_model}' has not run yet"
                        )
                    total += prior.mode
                if total <= 0:
                    raise ModelError(
                        "gp3_spec: need at least one on-disk size input "
                        f"among {', '.join(keys)}"
                    )
                mode_inst = None
                for prior in reversed(out):
                    if not prior.instance_pick:
                        continue
                    candidate = prior.instance_pick.get("pick_mode")
                    if candidate is not None and candidate.ebs_bandwidth_gbps is not None:
                        mode_inst = candidate
                        break
                if mode_inst is None:
                    for prior in reversed(out):
                        if prior.instance_pick and prior.instance_pick.get("pick_mode"):
                            mode_inst = prior.instance_pick["pick_mode"]
                            break
                out.append(
                    ScenarioStepResult(
                        kind="lookup",
                        slug="ebs.gp3-spec",
                        chained=False,
                        gp3_spec=_attach_instance_ebs(gp3_volume_spec(total), mode_inst),
                    )
                )
                continue

            if lookup != "instance_select":
                raise ModelError(f"unknown lookup kind '{lookup}'")
            if previous is None:
                raise ModelError("instance_select: no previous step's band to pick against")
            system = step.get("system") or "aws-ec2"
            catalog = load_instance_catalog(conn, system)
            ceiling = parse_bytes(step.get("max_ram", DEFAULT_INSTANCE_CEILING))
            pick = select_instance(
                previous,
                catalog,
                family=step.get("family"),
                ceiling_bytes=None if ceiling == 0 else ceiling,
            )
            out.append(
                ScenarioStepResult(
                    kind="lookup",
                    slug=f"{system}.instance-select",
                    chained=True,
                    instance_pick=pick,
                    family=step.get("family"),
                )
            )

        else:
            raise ModelError(f"unknown scenario step kind '{kind}'")

    return out


# Below these, a model has been checked but not enough to lean on. The
# thresholds are ours, not anyone's standard, and they are deliberately
# unflattering: one case from one machine is an anecdote, and an average error
# of a quarter is not a sizing tool, it is a direction. Zero hits inside the
# predicted band is not a pass either — a tight self-consistent formula can
# post a tiny MAE while every observation misses the band.
THIN_CASES = 3
THIN_ERROR_PCT = 25.0


def validation_status(conn: sqlite3.Connection, model_slug: str) -> dict:
    """Say plainly how much reality this model has been checked against.

    Three states rather than two. A binary validated/unvalidated flag turns
    "checked once, badly" into the same green tick as "checked repeatedly and
    accurate", which is precisely the reassurance this project exists not to
    give. `grade` is what the surfaces colour by, so the CLI, the API and the
    page cannot disagree about how encouraging a result is.

    `reasonable` (the UI's Validated badge) needs n, MAE, *and* at least one
    observation inside the predicted band. n and MAE alone used to promote
    mongodb.host-ram's default-split round-trips: n=3, MAE 0.8%, 0 in band.
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
            "grade": "none",
            "cases": 0,
            "text": "unvalidated (n=0) — no observation has ever been checked "
            "against this model",
        }

    cases = row["cases"]
    err = row["mean_abs_error_pct"] or 0.0
    within = int(row["within_band"] or 0)
    thin = (
        cases < THIN_CASES
        or err > THIN_ERROR_PCT
        or within == 0
    )
    detail = (
        f"n={cases}, {within} within band, "
        f"mean absolute error {err:.1f}%"
    )
    if thin:
        why = []
        if cases < THIN_CASES:
            why.append("too few cases to generalise")
        if err > THIN_ERROR_PCT:
            why.append("the error is large enough to need decomposing")
        if within == 0:
            why.append("none of the observations fell inside the predicted band")
        text = f"thinly validated ({detail}) — {', and '.join(why)}"
    else:
        text = f"validated ({detail})"

    return {
        "validated": True,
        "grade": "thin" if thin else "reasonable",
        "cases": cases,
        "within_band": within,
        "mean_abs_error_pct": err,
        "text": text,
    }


def lab_status(conn: sqlite3.Connection, model_slug: str) -> dict:
    """Short measured / still-needs copy from lab.yaml. Empty dict if absent."""
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT l.label, l.measured, l.still_needs "
        "FROM lab l JOIN model m ON m.id = l.model_id WHERE m.slug = ?",
        (model_slug,),
    ).fetchone()
    if row is None:
        return {}
    return {
        "label": row["label"],
        "measured": row["measured"],
        "still_needs": row["still_needs"],
    }
