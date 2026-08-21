"""Build the xycalc database from the YAML corpus.

    python -m xycalc.build

Rebuilds from scratch every time. The database is a build artifact and is
gitignored; the YAML under data/ is the source of truth, so git history records
what was learned and when.

Two gates are enforced here at load time, before the schema's NOT NULL
constraints ever get a chance to fire, so the error message names the file and
the row rather than a column:

  1. A coefficient citing a source slug that does not exist fails the build.
     No number without a citation.
  2. A coefficient without `applies_to` fails the build. An infrastructure
     figure with no version attached is not a fact.

The local/ overlay
------------------
Everything under local/ is loaded after data/ and merged into the same tables.
local/ is gitignored, which is what lets a deployment feed its own production
observations into the models without publishing them and without forking this
code. A checkout with no local/ builds the public corpus and says so.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

import yaml

PKG = Path(__file__).parent
ROOT = PKG.parent.parent
DATA = ROOT / "data"
LOCAL = ROOT / "local"
SCHEMA = PKG / "schema.sql"

# ROOT points at the repo only under an editable install; a plain `pip install`
# puts this file in site-packages, where there is no data/ and nowhere sane to
# write. $XYCALC_DB is the escape hatch, and the API's only override.
_ENV_DB = os.environ.get("XYCALC_DB")
DEFAULT_DB = Path(_ENV_DB).expanduser() if _ENV_DB else ROOT / "xycalc.db"

# Key in the meta table. The value is a sha256 of schema.sql at build time.
SCHEMA_HASH_KEY = "schema_hash"


def schema_hash() -> str:
    """Fingerprint of the schema this build was compiled against."""
    return hashlib.sha256(SCHEMA.read_bytes()).hexdigest()


class BuildError(Exception):
    pass


def _read(path: Path) -> dict:
    # encoding is explicit because Windows defaults to cp1252 and COOPER runs
    # Windows. YAML is UTF-8 by spec, so this is the correct read everywhere.
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def collect(*relative: str) -> list[tuple[Path, dict, str]]:
    """Every YAML at data/<rel> then local/<rel>, in filename order.

    Returns (path, document, origin) so a row can record whether it came from
    the published corpus or from a deployment's private overlay. A directory
    that does not exist is not an error -- most systems have no observations
    yet, and a corpus with no local/ is the normal case.
    """
    out: list[tuple[Path, dict, str]] = []
    for root, origin in ((DATA, "corpus"), (LOCAL, "local")):
        for rel in relative:
            base = root / rel
            paths = (
                sorted(base.glob("*.yaml"))
                if base.is_dir()
                else ([base] if base.is_file() else [])
            )
            for p in paths:
                out.append((p, _read(p), origin))
    return out


def _band(row: dict, ctx: str) -> tuple[float, float, float]:
    """lo/mode/hi from either a point `value` or an explicit band.

    A documented constant is a band whose three values agree. An estimate that
    arrives as a bare number is a mistake worth catching: the whole reason the
    answer ships as a range is that most of these figures are not constants.
    """
    has_value = "value" in row
    has_band = any(k in row for k in ("value_lo", "value_mode", "value_hi"))
    if has_value and has_band:
        raise BuildError(
            f"{ctx}: supply `value` or value_lo/value_mode/value_hi, not both"
        )
    if has_value:
        v = float(row["value"])
        return v, v, v
    try:
        lo = float(row["value_lo"])
        mode = float(row["value_mode"])
        hi = float(row["value_hi"])
    except KeyError as e:
        raise BuildError(
            f"{ctx}: needs either `value` or all of value_lo/value_mode/value_hi "
            f"(missing {e})"
        ) from None
    if not (lo <= mode <= hi):
        raise BuildError(f"{ctx}: band out of order: {lo} / {mode} / {hi}")
    return lo, mode, hi


class Builder:
    def __init__(self, conn: sqlite3.Connection):
        self.c = conn
        self.source: dict[str, int] = {}
        self.system: dict[str, int] = {}
        self.parameter: dict[str, int] = {}
        self.coefficient: dict[str, int] = {}
        self.model: dict[str, int] = {}
        self.observation: dict[str, int] = {}
        self.guide: dict[str, int] = {}
        self.counts: dict[str, int] = {}
        self.local_rows = 0

    # -- helpers ----------------------------------------------------------

    def ins(self, table: str, **cols) -> int:
        cols = {k: v for k, v in cols.items() if v is not None}
        names = ",".join(cols)
        marks = ",".join("?" * len(cols))
        cur = self.c.execute(
            f"INSERT INTO {table} ({names}) VALUES ({marks})", list(cols.values())
        )
        self.counts[table] = self.counts.get(table, 0) + 1
        return cur.lastrowid

    def _ref(self, table: str, index: dict[str, int], slug: str, ctx: str) -> int:
        """Resolve a slug, failing loudly on typos rather than on a NULL."""
        if slug not in index:
            known = ", ".join(sorted(index)[:6]) or "(none loaded)"
            raise BuildError(
                f"{ctx}: unknown {table} '{slug}'. Known {table}s include: {known}"
            )
        return index[slug]

    def _unique_slug(
        self, index: dict[str, int], slug: str, kind: str, ctx: str
    ) -> None:
        if slug in index:
            raise BuildError(f"{ctx}: duplicate {kind} slug")

    def src(self, slug: str, ctx: str) -> int:
        if not slug:
            raise BuildError(
                f"{ctx}: no source. Every number in this corpus cites one."
            )
        return self._ref("source", self.source, slug, ctx)

    # -- loaders ----------------------------------------------------------

    def sources(self):
        for path, doc, _ in collect("sources.yaml", "sources"):
            for row in doc.get("sources", []):
                ctx = f"{path.name}:{row.get('slug', '?')}"
                self._unique_slug(self.source, row["slug"], "source", ctx)
                self.source[row["slug"]] = self.ins(
                    "source",
                    slug=row["slug"],
                    title=row["title"],
                    publisher=row["publisher"],
                    url=row.get("url"),
                    version=row.get("version"),
                    published_on=row.get("published_on"),
                    retrieved_on=row["retrieved_on"],
                    source_type=row["source_type"],
                    notes=row.get("notes"),
                )

    def systems(self):
        for path, doc, _ in collect("systems.yaml"):
            for row in doc.get("systems", []):
                ctx = f"{path.name}:{row.get('slug', '?')}"
                self._unique_slug(self.system, row["slug"], "system", ctx)
                self.system[row["slug"]] = self.ins(
                    "system",
                    slug=row["slug"],
                    label=row["label"],
                    category=row["category"],
                    notes=row.get("notes"),
                )

    def parameters(self):
        for path, doc, _ in collect("parameters.yaml"):
            for row in doc.get("parameters", []):
                ctx = f"{path.name}:{row.get('slug', '?')}"
                self._unique_slug(self.parameter, row["slug"], "parameter", ctx)
                self.parameter[row["slug"]] = self.ins(
                    "parameter",
                    slug=row["slug"],
                    label=row["label"],
                    unit=row["unit"],
                    dimension=row["dimension"],
                    notes=row.get("notes"),
                )

    def coefficients(self):
        for path, doc, origin in collect("coefficients"):
            for row in doc.get("coefficients", []):
                ctx = f"{path.name}:{row.get('slug', '?')}"
                self._unique_slug(self.coefficient, row["slug"], "coefficient", ctx)

                # Gate 2, checked here rather than left to NOT NULL so the
                # message can say which figure and why it matters.
                if not row.get("applies_to"):
                    raise BuildError(
                        f"{ctx}: no `applies_to`. Which versions is this true "
                        f"for? An unversioned infrastructure figure is a lie -- "
                        f"defaults move between releases and hardware "
                        f"generations."
                    )

                lo, mode, hi = _band(row, ctx)
                self.coefficient[row["slug"]] = self.ins(
                    "coefficient",
                    slug=row["slug"],
                    parameter_id=self._ref(
                        "parameter", self.parameter, row["parameter"], ctx
                    ),
                    system_id=self._ref("system", self.system, row["system"], ctx),
                    applies_to=row["applies_to"],
                    value_lo=lo,
                    value_mode=mode,
                    value_hi=hi,
                    confidence=row["confidence"],
                    source_id=self.src(row.get("source"), ctx),
                    quote=row.get("quote"),
                    valid_from=row.get("valid_from"),
                    valid_to=row.get("valid_to"),
                    notes=row.get("notes"),
                )
                if origin == "local":
                    self.local_rows += 1

    def models(self):
        for path, doc, _ in collect("models"):
            for row in doc.get("models", []):
                ctx = f"{path.name}:{row.get('slug', '?')}"
                self._unique_slug(self.model, row["slug"], "model", ctx)
                mid = self.ins(
                    "model",
                    slug=row["slug"],
                    question=row["question"],
                    system_id=self._ref("system", self.system, row["system"], ctx),
                    output_parameter_id=self._ref(
                        "parameter", self.parameter, row["output"], ctx
                    ),
                    summary=row.get("summary"),
                    reframe=row.get("reframe"),
                    notes=row.get("notes"),
                )
                self.model[row["slug"]] = mid

                for i, inp in enumerate(row.get("inputs", [])):
                    self.ins(
                        "model_input",
                        model_id=mid,
                        key=inp["key"],
                        label=inp["label"],
                        unit=inp["unit"],
                        required=1 if inp.get("required", True) else 0,
                        default_value=inp.get("default"),
                        help=inp.get("help"),
                        sequence=i,
                    )

                input_keys = {i["key"] for i in row.get("inputs", [])}
                for i, term in enumerate(row.get("terms", [])):
                    tctx = f"{ctx}/{term.get('key', '?')}"
                    apply = term["apply"]
                    if apply in (
                        "input",
                        "divide_by_input",
                        "multiply_by_input",
                        "add_fraction_from_input",
                    ):
                        if term["input_key"] not in input_keys:
                            raise BuildError(
                                f"{tctx}: reads input '{term['input_key']}', "
                                f"which the model does not declare"
                            )
                        coeff_id = None
                    elif apply == "cap_at_throughput":
                        if term.get("input_key") not in input_keys:
                            raise BuildError(
                                f"{tctx}: cap_at_throughput needs input "
                                f"'{term.get('input_key')}', which is not declared"
                            )
                        coeff_id = self._ref(
                            "coefficient",
                            self.coefficient,
                            term["coefficient"],
                            tctx,
                        )
                    else:
                        coeff_id = self._ref(
                            "coefficient",
                            self.coefficient,
                            term["coefficient"],
                            tctx,
                        )
                    self.ins(
                        "model_term",
                        model_id=mid,
                        key=term["key"],
                        label=term["label"],
                        role=term["role"],
                        apply=apply,
                        input_key=term.get("input_key"),
                        coefficient_id=coeff_id,
                        optional=1 if term.get("optional") else 0,
                        when_input=term.get("when_input"),
                        unless_input=term.get("unless_input"),
                        rationale=term["rationale"],
                        sequence=i,
                    )

    def observations(self):
        for path, doc, origin in collect("observations"):
            for row in doc.get("observations", []):
                ctx = f"{path.name}:{row.get('slug', '?')}"
                self._unique_slug(self.observation, row["slug"], "observation", ctx)
                self.observation[row["slug"]] = self.ins(
                    "observation",
                    slug=row["slug"],
                    system_id=self._ref("system", self.system, row["system"], ctx),
                    parameter_id=self._ref(
                        "parameter", self.parameter, row["parameter"], ctx
                    ),
                    value=float(row["value"]),
                    unit=row["unit"],
                    workload=row.get("workload"),
                    machine_class=row.get("machine_class"),
                    system_version=row.get("system_version"),
                    observed_on=row.get("observed_on"),
                    origin=origin,
                    source_id=self.src(row.get("source"), ctx),
                    notes=row.get("notes"),
                )
                if origin == "local":
                    self.local_rows += 1

    def guides(self):
        """Calculator-tab structure. Figures are slugs, not literals.

        Walks every observation/coefficient/model/source ref so a typo fails
        at build with the file and slug, rather than as a blank cell on the
        exported page.
        """
        for path, doc, origin in collect("guides"):
            for row in doc.get("guides", []):
                ctx = f"{path.name}:{row.get('slug', '?')}"
                slug = row.get("slug")
                if not slug:
                    raise BuildError(f"{ctx}: guide needs a slug")
                if slug in self.guide:
                    raise BuildError(f"{ctx}: duplicate guide slug")
                spec = {k: v for k, v in row.items() if k != "slug"}
                self._check_guide_spec(spec, ctx)
                self.guide[slug] = self.ins(
                    "guide",
                    slug=slug,
                    spec_json=json.dumps(spec, sort_keys=True),
                    origin=origin,
                )
                if origin == "local":
                    self.local_rows += 1

    def _check_guide_spec(self, node, ctx: str) -> None:
        if isinstance(node, dict):
            if "observation" in node:
                slug = node["observation"]
                if slug not in self.observation:
                    raise BuildError(
                        f"{ctx}: unknown observation '{slug}'. Known include: "
                        f"{', '.join(sorted(self.observation)[:6]) or '(none loaded)'}"
                    )
            if "coefficient" in node:
                slug = node["coefficient"]
                if slug not in self.coefficient:
                    raise BuildError(
                        f"{ctx}: unknown coefficient '{slug}'. Known include: "
                        f"{', '.join(sorted(self.coefficient)[:6]) or '(none loaded)'}"
                    )
            kind = node.get("kind")
            if kind in {"table", "series"}:
                for i, row in enumerate(node.get("rows") or []):
                    self._check_guide_spec(row, f"{ctx}/row[{i}]")
                for item in node.get("relative") or []:
                    self._check_guide_spec(item, ctx)
                for item in node.get("derive") or []:
                    self._check_guide_spec(item, ctx)
                return
            if kind == "format":
                self._check_guide_spec(node.get("values") or {}, f"{ctx}/values")
                return
            for key, val in node.items():
                if key in ("observation", "coefficient", "kind"):
                    continue
                if key in ("model", "ticket_model") and isinstance(val, str):
                    if val not in self.model:
                        raise BuildError(f"{ctx}: unknown model '{val}'")
                if key in ("source", "ticket_source") and isinstance(val, str):
                    if val not in self.source:
                        raise BuildError(f"{ctx}: unknown source '{val}'")
                self._check_guide_spec(val, ctx)
        elif isinstance(node, list):
            for item in node:
                self._check_guide_spec(item, ctx)

    def validations(self):
        """Recompute every validation case against the model as it stands now.

        Cases record inputs and a measured actual; the prediction is never
        stored in YAML. If it were, a model change would leave the recorded
        error untouched and the corpus would report an accuracy it no longer
        has.
        """
        from .model import Model  # local import: model.py reads the built DB

        for path, doc, origin in collect("validation"):
            for row in doc.get("validation", []):
                ctx = f"{path.name}:{row.get('case', '?')}"
                slug = row["model"]
                if slug not in self.model:
                    raise BuildError(f"{ctx}: unknown model '{slug}'")
                model = Model.load(self.c, slug)
                result = model.evaluate(row["inputs"])

                # Most measurements observe an intermediate quantity, not the
                # model's final output. Comparing them anyway measures the gap
                # between two different questions.
                at_term = row.get("at_term")
                if at_term:
                    step = next(
                        (s for s in result.steps if s.term.key == at_term), None
                    )
                    if step is None:
                        raise BuildError(
                            f"{ctx}: at_term '{at_term}' is not a term of "
                            f"{slug}. Terms: "
                            f"{', '.join(s.term.key for s in result.steps)}"
                        )
                    lo, mode, hi = step.lo, step.mode, step.hi
                else:
                    lo, mode, hi = result.lo, result.mode, result.hi

                actual = float(row["actual"])
                obs_slug = row.get("observation")
                observation_id = (
                    self._ref("observation", self.observation, obs_slug, ctx)
                    if obs_slug
                    else None
                )
                self.ins(
                    "validation",
                    model_id=self.model[slug],
                    case_slug=row["case"],
                    observation_id=observation_id,
                    inputs_json=json.dumps(row["inputs"], sort_keys=True),
                    at_term=at_term,
                    predicted_lo=lo,
                    predicted_mode=mode,
                    predicted_hi=hi,
                    actual=actual,
                    within_band=1 if lo <= actual <= hi else 0,
                    error_pct=((mode - actual) / actual * 100) if actual else 0.0,
                    notes=row.get("notes"),
                )
                if origin == "local":
                    self.local_rows += 1

    def run(self):
        self.sources()
        self.systems()
        self.parameters()
        self.coefficients()
        self.models()
        self.observations()
        self.guides()
        self.validations()


def build(db_path: Path = DEFAULT_DB) -> Path:
    # The default's parent is the repo root, but an $XYCALC_DB path may point
    # into a directory that does not exist yet.
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))

    b = Builder(conn)
    try:
        b.run()
    except BuildError:
        conn.close()
        db_path.unlink(missing_ok=True)
        raise
    except sqlite3.IntegrityError as e:
        conn.close()
        db_path.unlink(missing_ok=True)
        raise BuildError(f"constraint failed: {e}") from e
    except (KeyError, TypeError, ValueError) as e:
        conn.close()
        db_path.unlink(missing_ok=True)
        raise BuildError(f"malformed corpus: {e!r}") from e

    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?)",
        (SCHEMA_HASH_KEY, schema_hash()),
    )
    conn.commit()

    print(f"built {db_path}")
    for table in sorted(b.counts):
        print(f"  {table:<16} {b.counts[table]:>5,}")

    if b.local_rows:
        print(f"\n  {b.local_rows} row(s) merged from local/ (not published)")
    else:
        print("\n  no local/ overlay: public corpus only")

    conn.close()
    return db_path


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    target = Path(argv[0]) if argv else DEFAULT_DB
    try:
        build(target)
    except BuildError as e:
        print(f"build failed: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
