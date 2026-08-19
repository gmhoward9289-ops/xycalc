// The band arithmetic, in JavaScript, so a page with no server behind it can
// still show its work.
//
// This is a SECOND implementation of `model.py::Model.evaluate` and it exists
// under protest. One set of numbers computed two ways is exactly the drift
// this project was built to refuse -- so the two are pinned together by golden
// vectors: `xycalc export` writes what Python produced for a set of inputs
// into the corpus blob, `tests/test_export.py` runs this file under node and
// fails if a single figure or contribution string differs, and the page itself
// re-checks those vectors on load and refuses to render if they do not match.
// A silent divergence is the failure mode that matters; all three gates exist
// to make it a loud one.
//
// No imports, no globals beyond `XY`. Loaded by <script> in the exported page
// and by require() under node.

const XY = (() => {
  "use strict";

  const UNITS = {
    b: 1, kb: 1e3, mb: 1e6, gb: 1e9, tb: 1e12,
    kib: 1024, mib: 1024 ** 2, gib: 1024 ** 3, tib: 1024 ** 4,
  };

  class ModelError extends Error {}

  // parse_bytes(). Decimal by default: that is what db.stats() reports and
  // what a vendor sizing table means. KiB/MiB/GiB honoured when written.
  function parseBytes(text) {
    if (typeof text === "number") return text;
    const m = /^\s*([0-9.]+)\s*([a-zA-Z]*)\s*$/.exec(String(text));
    if (!m) throw new ModelError("cannot read a size from '" + text + "'");
    const value = parseFloat(m[1]);
    const unit = m[2].toLowerCase();
    if (!unit) return value;
    if (!(unit in UNITS)) throw new ModelError("unknown unit '" + m[2] + "' in '" + text + "'");
    return value * UNITS[unit];
  }

  // Python's ",.Nf": fixed decimals with thousands separators. Locale pinned to
  // en-US rather than the visitor's, because these strings are compared against
  // Python's output character for character and a French browser would render
  // "1,6 TB".
  function fixed(n, digits) {
    return n.toLocaleString("en-US", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
  }

  // Python's "%g": six significant digits, trailing zeros stripped, exponential
  // outside [1e-4, 1e6). Used for coefficients in the contribution column, so
  // "x 2.5" does not become "x 2.50000".
  function formatG(x) {
    if (!isFinite(x)) return String(x);
    if (x === 0) return "0";
    const parts = x.toExponential(5).split("e");
    const exp = Number(parts[1]);
    const strip = (s) => (s.indexOf(".") < 0 ? s : s.replace(/\.?0+$/, ""));
    if (exp < -4 || exp >= 6) {
      const sign = exp < 0 ? "-" : "+";
      return strip(parts[0]) + "e" + sign + String(Math.abs(exp)).padStart(2, "0");
    }
    return strip(x.toFixed(Math.max(0, 5 - exp)));
  }

  // format_bytes(). One decimal, rounding half AWAY from zero -- not half to
  // even. 1.25 TB is what this corpus's first worked example produces, and it
  // must render "1.3 TB" here and in the terminal.
  function formatBytes(n) {
    const scales = [["TB", 1e12], ["GB", 1e9], ["MB", 1e6]];
    for (const pair of scales) {
      const unit = pair[0], size = pair[1];
      if (Math.abs(n) >= size) {
        const v = n / size;
        const r = Math.sign(v) * (Math.floor(Math.abs(v) * 10 + 0.5) / 10);
        return fixed(r, 1) + " " + unit;
      }
    }
    return fixed(n, 0) + " B";
  }

  // Units whose fractional part is noise. Half a ticket does not exist.
  const INTEGRAL_UNITS = new Set(["iops", "count", "ops/s", "tickets"]);

  function formatQuantity(n, unit) {
    if (unit === "bytes") return formatBytes(n);
    if (unit === "percent") return fixed(n, 1) + "%";
    if (INTEGRAL_UNITS.has(unit)) return fixed(n, 0) + " " + unit;
    return fixed(n, 2) + " " + unit;
  }

  function coerceInputs(model, values) {
    const declared = {};
    for (const i of model.inputs) declared[i.key] = i;
    for (const key of Object.keys(values)) {
      if (!(key in declared)) {
        throw new ModelError(
          model.slug + ": unknown input '" + key + "'. Accepts: " +
          (Object.keys(declared).join(", ") || "(none)")
        );
      }
    }
    const out = {};
    for (const key of Object.keys(declared)) {
      const spec = declared[key];
      let raw = values[key];
      if (raw === undefined || raw === null || raw === "") raw = spec.default_value;
      if (raw === undefined || raw === null || raw === "") {
        if (spec.required) throw new ModelError(model.slug + ": input '" + key + "' is required");
        continue;
      }
      out[key] = spec.unit === "bytes" ? parseBytes(raw) : parseFloat(raw);
      if (Number.isNaN(out[key])) throw new ModelError(model.slug + ": '" + key + "' is not a number");
    }
    return out;
  }

  // Mirrors Model.evaluate(). Terms run in the order the corpus wrote them;
  // `role` only groups them for display.
  function evaluate(model, values) {
    const supplied = coerceInputs(model, values);
    const declaredUnits = {};
    for (const i of model.inputs) declaredUnits[i.key] = i.unit;

    let lo = 0, mode = 0, hi = 0;
    const steps = [];
    const constraints = [];

    for (const term of model.terms) {
      if (term.role === "constraint") { constraints.push(term); continue; }

      const inUnit = declaredUnits[term.input_key] || term.unit || model.output_unit;
      const push = (contribution) =>
        steps.push({ term, contribution, lo, mode, hi, skipped: false, skip_reason: null });

      if (term.apply === "input" || term.apply === "divide_by_input") {
        const v = supplied[term.input_key];
        if (v === undefined) {
          if (term.optional) {
            steps.push({ term, contribution: "—", lo, mode, hi, skipped: true, skip_reason: "not supplied" });
            continue;
          }
          throw new ModelError(model.slug + ": input '" + term.input_key + "' required");
        }
        if (term.apply === "input") {
          lo += v; mode += v; hi += v;
          push("+ " + formatQuantity(v, inUnit));
        } else {
          if (!v) {
            throw new ModelError(
              model.slug + ": '" + term.input_key + "' cannot be zero — " +
              "dividing by it would report an infinite ceiling"
            );
          }
          // No band inversion: a caller-supplied scalar has one value, so all
          // three ends move together. Only a FRACTION carrying its own
          // lo/mode/hi inverts -- see divide_by_fraction below.
          lo /= v; mode /= v; hi /= v;
          push("÷ " + formatQuantity(v, inUnit));
        }
        continue;
      }

      const clo = term.coeff_lo, cmode = term.coeff_mode, chi = term.coeff_hi;
      let contribution;

      if (term.apply === "multiply") {
        lo *= clo; mode *= cmode; hi *= chi;
        contribution = "x " + formatG(cmode) + (clo !== chi ? " (" + formatG(clo) + "–" + formatG(chi) + ")" : "");

      } else if (term.apply === "divide_by_fraction") {
        // The inversion. A low usable fraction means a HIGH requirement, so the
        // top of the band comes from the bottom of the fraction. Backwards here
        // yields a band that is wrong in the reassuring direction.
        if (!clo || !chi) throw new ModelError(term.key + ": fraction cannot be zero");
        lo /= (chi / 100); mode /= (cmode / 100); hi /= (clo / 100);
        contribution = "÷ " + formatG(cmode) + "%" +
          (clo !== chi ? " (" + formatG(clo) + "–" + formatG(chi) + "%)" : "");

      } else if (term.apply === "add_bytes") {
        lo += clo; mode += cmode; hi += chi;
        contribution = "+ " + formatQuantity(cmode, model.output_unit);

      } else if (term.apply === "floor_at" || term.apply === "cap_at") {
        // A bound applies to each end independently and can COLLAPSE the band.
        // Honest -- the bound really does determine the value -- but it has to
        // be visible, so the step says so.
        if (term.apply === "floor_at") {
          lo = Math.max(lo, clo); mode = Math.max(mode, cmode); hi = Math.max(hi, chi);
          contribution = "≥ " + formatQuantity(cmode, inUnit);
        } else {
          lo = Math.min(lo, clo); mode = Math.min(mode, cmode); hi = Math.min(hi, chi);
          contribution = "≤ " + formatQuantity(cmode, inUnit);
        }
        if (lo === hi) contribution += " (band collapsed)";

      } else if (term.apply === "add_fraction") {
        lo *= (1 + clo / 100); mode *= (1 + cmode / 100); hi *= (1 + chi / 100);
        contribution = "+ " + formatG(cmode) + "%";

      } else {
        throw new ModelError(term.key + ": unknown apply '" + term.apply + "'");
      }

      push(contribution);
    }

    return { model: model.slug, lo, mode, hi, unit: model.output_unit, steps, constraints, inputs: supplied };
  }

  // headroom(). Reported against the whole band on purpose: `available` above
  // the mode but below the high end is the interesting case, and one number
  // hides it.
  function headroom(result, available) {
    let verdict;
    if (available >= result.hi) verdict = "covered across the whole band";
    else if (available >= result.mode) verdict = "covers the mode but not the high end";
    else if (available >= result.lo) verdict = "below the mode — undersized unless the estimates are generous";
    else verdict = "below the entire band — undersized";
    return {
      available: available,
      required_lo: result.lo,
      required_mode: result.mode,
      required_hi: result.hi,
      utilisation_mode_pct: available ? (result.mode / available) * 100 : Infinity,
      utilisation_hi_pct: available ? (result.hi / available) * 100 : Infinity,
      margin_mode: available - result.mode,
      verdict: verdict,
    };
  }

  // The self-check. Runs every golden vector Python wrote into the blob and
  // returns the ones that disagree. An empty array is the only acceptable
  // result; the page renders a refusal rather than a number otherwise.
  function checkGolden(corpus) {
    const bySlug = {};
    for (const m of corpus.models) bySlug[m.slug] = m;
    const failures = [];
    for (const g of corpus.golden || []) {
      const model = bySlug[g.model];
      if (!model) { failures.push({ vector: g, reason: "no model '" + g.model + "' in corpus" }); continue; }
      let got;
      try {
        got = evaluate(model, g.inputs);
      } catch (e) {
        failures.push({ vector: g, reason: "threw: " + e.message });
        continue;
      }
      for (const end of ["lo", "mode", "hi"]) {
        // Relative tolerance, not equality: Python and V8 both use IEEE 754
        // doubles and the operations run in the same order, so this should be
        // exact -- but pinning to the last bit would turn a harmless
        // re-association into a red page. 1e-12 is far tighter than any
        // difference that could change a rendered figure.
        const a = got[end], b = g[end];
        const tol = Math.max(Math.abs(b), 1) * 1e-12;
        if (!(Math.abs(a - b) <= tol)) {
          failures.push({ vector: g, reason: end + ": js " + a + " vs python " + b });
        }
      }
      if (g.contributions) {
        const gotSteps = got.steps.map((s) => s.contribution).join(" | ");
        const wantSteps = g.contributions.join(" | ");
        if (gotSteps !== wantSteps) {
          failures.push({ vector: g, reason: 'contributions: js "' + gotSteps + '" vs python "' + wantSteps + '"' });
        }
      }
    }
    return failures;
  }

  return {
    ModelError: ModelError,
    parseBytes: parseBytes,
    formatBytes: formatBytes,
    formatQuantity: formatQuantity,
    formatG: formatG,
    evaluate: evaluate,
    headroom: headroom,
    checkGolden: checkGolden,
  };
})();

if (typeof module !== "undefined" && module.exports) module.exports = XY;
