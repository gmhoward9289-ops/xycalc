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


# Mirrors build.py's own DATA computation rather than importing it, so this
# module has no dependency on the build pipeline -- a scenario only ever
# references models that build.py has already loaded into the database.
_SCENARIOS_PATH = Path(__file__).parent.parent.parent / "data" / "scenarios.yaml"


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
        declared_units = {i["key"]: i["unit"] for i in self.inputs}
        lo = mode = hi = 0.0
        steps: list[Step] = []
        constraints: list[Term] = []

        for term in self.terms:
            if term.role == "constraint":
                constraints.append(term)
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
                contribution = f"+ {format_quantity(cmode, self.output_unit)}"

            elif term.apply in ("floor_at", "cap_at"):
                # A bound applies to each end of the band independently, which
                # can COLLAPSE it: if a floor sits above hi, all three ends meet
                # and the answer stops looking uncertain when it still is. That
                # is honest -- the bound really does determine the value -- but
                # it must be visible, so the step records whether it happened.
                if term.apply == "floor_at":
                    lo, mode, hi = max(lo, clo), max(mode, cmode), max(hi, chi)
                    contribution = f"≥ {format_quantity(cmode, in_unit)}"
                else:
                    lo, mode, hi = min(lo, clo), min(mode, cmode), min(hi, chi)
                    contribution = f"≤ {format_quantity(cmode, in_unit)}"
                if lo == hi:
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


@dataclass
class InstanceSpec:
    name: str               # e.g. "r8i.24xlarge" -- read from `applies_to`,
                             # not `slug`, so it matches what `xycalc why`
                             # shows for the same coefficient.
    ram_bytes: float
    vcpu: float | None
    source_title: str
    source_url: str | None


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

    return sorted(
        (
            InstanceSpec(
                name=name,
                ram_bytes=e["ram_bytes"],
                vcpu=e.get("vcpu"),
                source_title=e["source_title"],
                source_url=e["source_url"],
            )
            for name, e in by_name.items()
            if "ram_bytes" in e  # a row with only a vcpu coefficient is unusable
        ),
        key=lambda i: i.ram_bytes,
    )


# Standing internal policy, not an AWS ceiling: r8i itself goes to 3,072 GiB
# (r8i.96xlarge), but the org has decided to cap recommendations at 1,536 GiB
# (== r8i.48xlarge exactly, in binary GiB) and require a family change above
# that, pending a decision on what that next family is. Set 2026-08-16.
# Shared by the CLI's `instance-select` command and the scenario chain's
# lookup step, so the two surfaces cannot silently disagree about the cutoff.
DEFAULT_INSTANCE_CEILING = "1536GiB"


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
    "r8i" or "u7i". Raises if the filtered pool is empty.

    `ceiling_bytes` is an operational policy cutoff, not a vendor fact --
    r8i itself goes to 3,072 GiB (r8i.96xlarge), but an org can decide to
    stop recommending within a family below its real technical ceiling and
    require a family change past that point instead. When set, instances
    above it are excluded from the pool before picking, so the existing
    `exceeds_pool` / "custom sizing" branch fires at the POLICY ceiling, not
    necessarily the family's actual maximum -- deliberately not a coefficient
    in aws-ec2.yaml, because it is a standing internal decision that changes
    when the next family is chosen, not a sourced AWS spec.
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
        return min(fits, key=lambda i: i.ram_bytes) if fits else None

    largest = max(pool, key=lambda i: i.ram_bytes)

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
    headroom: dict | None = None


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

    # Headroom is reported against the last MODEL step, not the last step
    # overall -- a scenario that ends in an instance-select lookup (a name,
    # not a band) has nothing for `available` to be measured against there.
    model_steps = [s for s in scenario["steps"] if s.get("kind", "model") == "model"]
    last_model_step = model_steps[-1] if model_steps else None

    for step in scenario["steps"]:
        kind = step.get("kind", "model")

        if kind == "model":
            model = Model.load(conn, step["model"])
            feed = step.get("feed") or {}

            if not feed:
                composed = model.evaluate(inputs)
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
                    merged = {k: v for k, v in inputs.items() if k in own_keys}
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
            )
            if available is not None and step is last_model_step:
                step_result.headroom = headroom(composed, available)
            out.append(step_result)
            previous = composed

        elif kind == "lookup":
            if step.get("lookup") != "instance_select":
                raise ModelError(f"unknown lookup kind '{step.get('lookup')}'")
            if previous is None:
                raise ModelError("instance_select: no previous step's band to pick against")
            catalog = load_instance_catalog(conn, "aws-ec2")
            ceiling = parse_bytes(step.get("max_ram", DEFAULT_INSTANCE_CEILING))
            pick = select_instance(
                previous,
                catalog,
                family=step.get("family"),
                ceiling_bytes=None if ceiling == 0 else ceiling,
            )
            out.append(
                ScenarioStepResult(
                    kind="lookup", slug="aws-ec2.instance-select", chained=True,
                    instance_pick=pick,
                )
            )

        else:
            raise ModelError(f"unknown scenario step kind '{kind}'")

    return out


# Below these, a model has been checked but not enough to lean on. The
# thresholds are ours, not anyone's standard, and they are deliberately
# unflattering: one case from one machine is an anecdote, and an average error
# of a quarter is not a sizing tool, it is a direction.
THIN_CASES = 3
THIN_ERROR_PCT = 25.0


def validation_status(conn: sqlite3.Connection, model_slug: str) -> dict:
    """Say plainly how much reality this model has been checked against.

    Three states rather than two. A binary validated/unvalidated flag turns
    "checked once, badly" into the same green tick as "checked repeatedly and
    accurate", which is precisely the reassurance this project exists not to
    give. `grade` is what the surfaces colour by, so the CLI, the API and the
    page cannot disagree about how encouraging a result is.
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
    thin = cases < THIN_CASES or err > THIN_ERROR_PCT
    detail = (
        f"n={cases}, {row['within_band']} within band, "
        f"mean absolute error {err:.1f}%"
    )
    if thin:
        why = []
        if cases < THIN_CASES:
            why.append("too few cases to generalise")
        if err > THIN_ERROR_PCT:
            why.append("the error is large enough to need decomposing")
        text = f"thinly validated ({detail}) — {', and '.join(why)}"
    else:
        text = f"validated ({detail})"

    return {
        "validated": True,
        "grade": "thin" if thin else "reasonable",
        "cases": cases,
        "within_band": row["within_band"],
        "mean_abs_error_pct": err,
        "text": text,
    }
