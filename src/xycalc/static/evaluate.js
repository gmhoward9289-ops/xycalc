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

  // One decimal point, optional en-US thousands separators. `[0-9.]+` used to
  // accept "1.2.3" (parseFloat silently returned 1.2) and to reject the comma
  // in formatQuantity's "3,000 iops". parse and format have to be inverses or
  // the calculator's own scrub-commit corrupts the answer.
  const AMOUNT = /^\s*((?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?|\.[0-9]+)\s*([a-zA-Z/%]*)\s*$/;

  function splitAmount(text) {
    const m = AMOUNT.exec(String(text));
    if (!m) throw new ModelError("cannot read a size from '" + text + "'");
    return { value: parseFloat(m[1].replace(/,/g, "")), unit: m[2] };
  }

  function parseNumber(text) {
    if (typeof text === "number") return text;
    try {
      return splitAmount(text).value;
    } catch (e) {
      throw new ModelError("cannot read a number from '" + text + "'");
    }
  }

  // parse_bytes(). Decimal by default: that is what db.stats() reports and
  // what a vendor sizing table means. KiB/MiB/GiB honoured when written.
  function parseBytes(text) {
    if (typeof text === "number") return text;
    const parts = splitAmount(text);
    const unit = parts.unit.toLowerCase();
    if (!unit) return parts.value;
    if (!(unit in UNITS)) throw new ModelError("unknown unit '" + parts.unit + "' in '" + text + "'");
    return parts.value * UNITS[unit];
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
      try {
        if (spec.unit === "bytes") {
          out[key] = parseBytes(raw);
        } else {
          try {
            out[key] = parseNumber(raw);
          } catch (e) {
            throw new ModelError(model.slug + ": '" + key + "' is not a number");
          }
        }
      } catch (e) {
        if (e instanceof ModelError) throw e;
        throw new ModelError(model.slug + ": '" + key + "' is not a number");
      }
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

      let skipReason = null;
      if (term.when_input && supplied[term.when_input] === undefined) {
        skipReason = "'" + term.when_input + "' not supplied";
      } else if (term.unless_input && supplied[term.unless_input] !== undefined) {
        skipReason = "'" + term.unless_input + "' supplied";
      }
      if (skipReason) {
        steps.push({ term, contribution: "—", lo, mode, hi, skipped: true, skip_reason: skipReason });
        continue;
      }

      const inUnit = declaredUnits[term.input_key] || term.unit || model.output_unit;
      const push = (contribution) =>
        steps.push({ term, contribution, lo, mode, hi, skipped: false, skip_reason: null });

      if (term.apply === "add_product_of_inputs") {
        const a = supplied[term.input_key];
        const b = supplied[term.input_key_b];
        if (a === undefined && b === undefined) {
          if (term.optional) {
            steps.push({ term, contribution: "—", lo, mode, hi, skipped: true, skip_reason: "not supplied" });
            continue;
          }
          throw new ModelError(
            model.slug + ": inputs '" + term.input_key + "' and '" +
            term.input_key_b + "' are required"
          );
        }
        if (a === undefined || b === undefined) {
          const missing = a === undefined ? term.input_key : term.input_key_b;
          throw new ModelError(
            model.slug + ": '" + term.input_key + "' and '" + term.input_key_b +
            "' must be supplied together (missing '" + missing + "')"
          );
        }
        const product = a * b;
        lo += product; mode += product; hi += product;
        const aUnit = declaredUnits[term.input_key] || "count";
        const bUnit = declaredUnits[term.input_key_b] || model.output_unit;
        push("+ " + formatQuantity(a, aUnit) + " × " + formatQuantity(b, bUnit));
        continue;
      }

      if (term.apply === "input" || term.apply === "divide_by_input" || term.apply === "multiply_by_input" || term.apply === "add_fraction_from_input") {
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
        } else if (term.apply === "divide_by_input") {
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
        } else if (term.apply === "multiply_by_input") {
          if (!v) {
            throw new ModelError(
              model.slug + ": '" + term.input_key + "' cannot be zero — " +
              "multiplying by it would zero the answer"
            );
          }
          lo *= v; mode *= v; hi *= v;
          push("x " + formatQuantity(v, inUnit));
        } else {
          // add_fraction_from_input: a caller-supplied percentage, not a
          // cited fraction -- same "one value, no band inversion" reasoning
          // as divide_by_input above.
          const factor = 1 + v / 100;
          lo *= factor; mode *= factor; hi *= factor;
          push("+ " + formatG(v) + "%");
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
        // be visible, so the step says so. Only when THIS bound collapsed a
        // real band; a scalar input may already have made lo === hi.
        const wasPoint = lo === hi;
        if (term.apply === "floor_at") {
          lo = Math.max(lo, clo); mode = Math.max(mode, cmode); hi = Math.max(hi, chi);
          contribution = "≥ " + formatQuantity(cmode, inUnit);
        } else {
          lo = Math.min(lo, clo); mode = Math.min(mode, cmode); hi = Math.min(hi, chi);
          contribution = "≤ " + formatQuantity(cmode, inUnit);
        }
        if (lo === hi && !wasPoint) contribution += " (band collapsed)";

      } else if (term.apply === "set_from_coefficient") {
        lo = clo; mode = cmode; hi = chi;
        contribution = "= " + formatQuantity(cmode, inUnit) +
          (clo !== chi ? " (" + formatQuantity(clo, inUnit) + "–" + formatQuantity(chi, inUnit) + ")" : "");

      } else if (term.apply === "cap_at_throughput") {
        const ioSize = supplied[term.input_key];
        if (ioSize === undefined) {
          throw new ModelError(model.slug + ": input '" + term.input_key + "' required for throughput crossover");
        }
        if (!ioSize) {
          throw new ModelError(
            model.slug + ": '" + term.input_key + "' cannot be zero — throughput crossover is undefined"
          );
        }
        const capLo = clo * 1024 / ioSize;
        const capMode = cmode * 1024 / ioSize;
        const capHi = chi * 1024 / ioSize;
        const wasPoint = lo === hi;
        lo = Math.min(lo, capLo); mode = Math.min(mode, capMode); hi = Math.min(hi, capHi);
        contribution = "≤ " + formatQuantity(capMode, model.output_unit) +
          " (" + formatG(cmode) + " MiB/s ÷ " + formatG(ioSize) + " KiB/op)";
        if (lo === hi && !wasPoint) contribution += " (band collapsed)";

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
    for (const g of corpus.scenario_golden || []) {
      let got;
      try {
        got = chainEvaluate(corpus, g.scenario, g.inputs);
      } catch (e) {
        failures.push({ vector: g, reason: "chain threw: " + e.message });
        continue;
      }
      if (got.steps.length !== g.steps.length) {
        failures.push({
          vector: g,
          reason: "chain step count js " + got.steps.length + " vs python " + g.steps.length,
        });
        continue;
      }
      for (let i = 0; i < g.steps.length; i++) {
        const a = got.steps[i], b = g.steps[i];
        if (a.kind !== b.kind || a.slug !== b.slug) {
          failures.push({ vector: g, reason: "step " + i + ": js " + a.kind + "/" + a.slug + " vs python " + b.kind + "/" + b.slug });
          continue;
        }
        for (const end of ["lo", "mode", "hi"]) {
          if (b[end] === undefined) continue;
          const tol = Math.max(Math.abs(b[end]), 1) * 1e-12;
          if (!(Math.abs(a[end] - b[end]) <= tol)) {
            failures.push({ vector: g, reason: a.slug + " " + end + ": js " + a[end] + " vs python " + b[end] });
          }
        }
        for (const pk of ["pick_lo", "pick_mode", "pick_hi"]) {
          if (b[pk] !== undefined && a[pk] !== b[pk]) {
            failures.push({ vector: g, reason: a.slug + " " + pk + ": js " + a[pk] + " vs python " + b[pk] });
          }
        }
        if (b.volume_gib !== undefined) {
          const tol = Math.max(Math.abs(b.volume_gib), 1) * 1e-12;
          if (!(Math.abs(a.volume_gib - b.volume_gib) <= tol)) {
            failures.push({ vector: g, reason: a.slug + " volume_gib: js " + a.volume_gib + " vs python " + b.volume_gib });
          }
        }
      }
    }
    return failures;
  }

  function gp3VolumeSpec(volumeBytes) {
    const gib = volumeBytes / (1024 ** 3);
    const maxIops = Math.min(80000, 500 * gib);
    return {
      volume_bytes: volumeBytes,
      volume_gib: gib,
      baseline_iops: 3000,
      max_provisionable_iops: maxIops,
      baseline_throughput_mibps: 125,
      max_throughput_mibps: 2000,
    };
  }

  function attachInstanceEbs(spec, instance) {
    if (!instance) return spec;
    const out = Object.assign({}, spec);
    out.instance_name = instance.name;
    if (instance.ebs_bandwidth_gbps == null) return out;
    out.instance_ebs_bandwidth_gbps = instance.ebs_bandwidth_gbps;
    out.instance_ebs_throughput_mibps = instance.ebs_bandwidth_gbps * 125;
    out.usable_throughput_mibps = Math.min(out.max_throughput_mibps, out.instance_ebs_throughput_mibps);
    return out;
  }

  function selectInstance(result, catalog, family, ceilingBytes) {
    let pool = catalog.filter((i) => !family || i.name.toLowerCase().indexOf(family.toLowerCase()) === 0);
    if (!pool.length) throw new ModelError("no instances in catalog matching family '" + family + "'");
    if (ceilingBytes != null) {
      pool = pool.filter((i) => i.ram_bytes <= ceilingBytes);
      if (!pool.length) {
        throw new ModelError("ceiling " + ceilingBytes + " bytes excludes every instance matching family '" + family + "'");
      }
    }
    const pick = (need) => {
      const fits = pool.filter((i) => i.ram_bytes >= need);
      if (!fits.length) return null;
      return fits.reduce((a, b) => (a.ram_bytes < b.ram_bytes ? a : b));
    };
    const largest = pool.reduce((a, b) => (a.ram_bytes > b.ram_bytes ? a : b));
    return {
      required_lo: result.lo,
      required_mode: result.mode,
      required_hi: result.hi,
      pick_lo: pick(result.lo),
      pick_mode: pick(result.mode),
      pick_hi: pick(result.hi),
      largest_in_pool: largest,
      exceeds_pool: result.hi > largest.ram_bytes,
    };
  }

  function sumScenarioBytes(inputs, keys) {
    let total = 0;
    for (const key of keys) {
      const raw = inputs[key];
      if (raw === undefined || raw === null || raw === "") continue;
      total += parseBytes(raw);
    }
    return total;
  }

  function presentModel(model, result) {
    return {
      kind: "model",
      slug: model.slug,
      model: model.slug,
      question: model.question,
      unit: result.unit,
      answer: { lo: result.lo, mode: result.mode, hi: result.hi },
      inputs: result.inputs,
      steps: result.steps.map((s) => ({
        key: s.term.key,
        label: s.term.label,
        role: s.term.role,
        contribution: s.contribution,
        running: { lo: s.lo, mode: s.mode, hi: s.hi },
        skipped: s.skipped,
        skip_reason: s.skip_reason,
        rationale: s.term.rationale,
        coefficient: s.term.coefficient,
        confidence: s.term.confidence,
        applies_to: s.term.applies_to,
        source: s.term.source,
        source_title: s.term.source_title,
        source_url: s.term.source_url,
        quote: s.term.quote,
      })),
      constraints: result.constraints.map((t) => ({
        key: t.key,
        label: t.label,
        value: t.coeff_mode,
        unit: t.unit,
        rationale: t.rationale,
        source: t.source,
        source_url: t.source_url,
      })),
      validation: model.validation,
      reframe: model.reframe,
      notes: model.notes,
    };
  }

  function buildInstanceSizingSummary(presented, inputs) {
    const summary = {};
    let host, inst, azure, gp3, ebs;
    for (const s of presented) {
      if (s.kind === "model" && s.model === "mongodb.host-ram") host = s;
      else if (s.kind === "lookup" && s.gp3) gp3 = s;
      else if (s.kind === "lookup" && s.pick) {
        if ((s.slug || "").indexOf("azure-vm") === 0) azure = s;
        else if (!inst || s.slug === "aws-ec2.instance-select") inst = s;
      }
      else if (s.kind === "model" && s.model === "ebs.iops-to-provision") ebs = s;
    }
    if (host && host.answer) {
      summary.ram = { lo: host.answer.lo, mode: host.answer.mode, hi: host.answer.hi, unit: host.unit };
    }
    if (inst && inst.pick) {
      const pick = inst.pick;
      const vcpu = (spec) => (spec == null ? null : spec.vcpu);
      summary.cpu = {
        lo: vcpu(pick.pick_lo),
        mode: vcpu(pick.pick_mode),
        hi: vcpu(pick.pick_hi),
        unit: "vcpu",
        instance_lo: pick.pick_lo && pick.pick_lo.name,
        instance_mode: pick.pick_mode && pick.pick_mode.name,
        instance_hi: pick.pick_hi && pick.pick_hi.name,
      };
    }
    if (azure && azure.pick) {
      const pick = azure.pick;
      const name = (spec) => (spec == null ? null : spec.name);
      summary.azure = {
        lo: name(pick.pick_lo),
        mode: name(pick.pick_mode),
        hi: name(pick.pick_hi),
        exceeds_pool: pick.exceeds_pool,
      };
    }
    if (gp3 && gp3.gp3) {
      const spec = gp3.gp3;
      const disk = {
        volume_gib: spec.volume_gib,
        baseline_iops: spec.baseline_iops,
        max_provisionable_iops: spec.max_provisionable_iops,
        baseline_throughput_mibps: spec.baseline_throughput_mibps,
        max_throughput_mibps: spec.max_throughput_mibps,
      };
      if (ebs && ebs.answer) {
        disk.provisioned_iops = { lo: ebs.answer.lo, mode: ebs.answer.mode, hi: ebs.answer.hi };
        disk.provisioned_iops_assumed_mean = !!(ebs.assumed_inputs && ebs.assumed_inputs.average_iops != null);
      }
      if (spec.instance_name) disk.instance_name = spec.instance_name;
      if (spec.instance_ebs_bandwidth_gbps != null) {
        disk.instance_ebs_bandwidth_gbps = spec.instance_ebs_bandwidth_gbps;
        disk.usable_throughput_mibps = spec.usable_throughput_mibps;
      }
      summary.disk = disk;
    }
    const current = {};
    if (inputs.current_ram) current.ram = parseBytes(inputs.current_ram);
    if (inputs.current_vcpu) current.vcpu = parseNumber(inputs.current_vcpu);
    if (inputs.current_disk_iops) current.disk_iops = parseNumber(inputs.current_disk_iops);
    if (inputs.current_disk_throughput) current.disk_throughput_mibps = parseNumber(inputs.current_disk_throughput);
    if (Object.keys(current).length) summary.current = current;
    return summary;
  }

  // Mirrors model.py::chain_evaluate. Lookups (instance catalog, gp3 catalog
  // numbers) are data in the export blob, not a third implementation of the
  // arithmetic — the golden scenario vector still pins the composed bands.
  function chainEvaluate(corpus, scenarioSlug, inputs) {
    const scenario = (corpus.scenarios || []).find((s) => s.slug === scenarioSlug);
    if (!scenario) throw new ModelError("no scenario '" + scenarioSlug + "'");
    if (scenario.disabled) throw new ModelError(scenarioSlug + ": not yet modeled");
    const bySlug = {};
    for (const m of corpus.models) bySlug[m.slug] = m;
    const catalogs = corpus.instance_catalogs || { "aws-ec2": corpus.instance_catalog || [] };
    const coeffMode = corpus.coefficient_mode || {};
    const ceiling = corpus.default_instance_ceiling_bytes;
    const supplied = Object.assign({}, inputs);
    const out = [];
    const modelResults = {};
    let previous = null;

    const fillDefaults = (step) => {
      const assumed = {};
      const defaults = step.defaults || {};
      for (const key of Object.keys(defaults)) {
        if (supplied[key] === undefined || supplied[key] === null || supplied[key] === "") {
          supplied[key] = defaults[key];
          assumed[key] = defaults[key];
        }
      }
      const fromCoeff = step.defaults_from_coefficient || {};
      for (const key of Object.keys(fromCoeff)) {
        if (supplied[key] !== undefined && supplied[key] !== null && supplied[key] !== "") continue;
        const slug = fromCoeff[key];
        if (!(slug in coeffMode)) {
          throw new ModelError((step.model || step.lookup) + ": default coefficient '" + slug + "' is not in the corpus");
        }
        supplied[key] = coeffMode[slug];
        assumed[key] = coeffMode[slug];
      }
      return assumed;
    };

    let lastBytesStep = null;
    for (const s of scenario.steps) {
      if ((s.kind || "model") !== "model") continue;
      const when = s.when_input;
      if (when && !supplied[when] && !(s.defaults && when in s.defaults) && !(s.defaults_from_coefficient && when in s.defaults_from_coefficient)) {
        continue;
      }
      const model = bySlug[s.model];
      if (model && model.output_unit === "bytes") lastBytesStep = s;
    }

    for (const step of scenario.steps) {
      const kind = step.kind || "model";
      const when = step.when_input;
      if (when && !supplied[when]) continue;

      if (kind === "model") {
        const assumed = fillDefaults(step);
        const model = bySlug[step.model];
        if (!model) throw new ModelError("no model '" + step.model + "' in corpus");
        const feed = step.feed || {};
        const ownKeys = {};
        for (const i of model.inputs) ownKeys[i.key] = true;
        let composed;
        if (!Object.keys(feed).length) {
          const scoped = {};
          for (const k of Object.keys(supplied)) if (ownKeys[k]) scoped[k] = supplied[k];
          composed = evaluate(model, scoped);
        } else {
          if (!previous) throw new ModelError(step.model + ": feed references 'previous' but this is the first step");
          const fedKeys = Object.keys(feed).filter((k) => feed[k] === "previous");
          const run = (bandValue) => {
            const merged = {};
            for (const k of Object.keys(supplied)) if (ownKeys[k]) merged[k] = supplied[k];
            for (const k of fedKeys) merged[k] = bandValue;
            return evaluate(model, merged);
          };
          const rLo = run(previous.lo), rMode = run(previous.mode), rHi = run(previous.hi);
          if (!(rLo.lo <= rMode.mode && rMode.mode <= rHi.hi)) {
            throw new ModelError(
              step.model + ": chained band inverted (lo=" + rLo.lo + ", mode=" + rMode.mode + ", hi=" + rHi.hi + ") -- refusing to report a band that would read as more confident than it is"
            );
          }
          composed = {
            model: model.slug,
            lo: rLo.lo,
            mode: rMode.mode,
            hi: rHi.hi,
            unit: rMode.unit,
            steps: rMode.steps,
            constraints: rMode.constraints,
            inputs: rMode.inputs,
          };
        }
        const presented = presentModel(model, composed);
        presented.chained = !!Object.keys(feed).length;
        if (Object.keys(assumed).length) presented.assumed_inputs = assumed;
        presented.lo = composed.lo;
        presented.mode = composed.mode;
        presented.hi = composed.hi;
        out.push(presented);
        previous = composed;
        modelResults[model.slug] = composed;
      } else if (kind === "lookup") {
        const lookup = step.lookup;
        if (lookup === "gp3_spec") {
          const keys = step.sum_inputs || ["storage_size"];
          let total = sumScenarioBytes(supplied, keys);
          if (step.sum_model) {
            const prior = modelResults[step.sum_model];
            if (!prior) throw new ModelError("gp3_spec: sum_model '" + step.sum_model + "' has not run yet");
            total += prior.mode;
          }
          if (total <= 0) {
            throw new ModelError("gp3_spec: need at least one on-disk size input among " + keys.join(", "));
          }
          let modeInst = null;
          for (let i = out.length - 1; i >= 0; i--) {
            if (out[i].pick && out[i].pick.pick_mode && out[i].pick.pick_mode.ebs_bandwidth_gbps != null) {
              modeInst = out[i].pick.pick_mode;
              break;
            }
          }
          if (!modeInst) {
            for (let i = out.length - 1; i >= 0; i--) {
              if (out[i].pick && out[i].pick.pick_mode) { modeInst = out[i].pick.pick_mode; break; }
            }
          }
          const spec = attachInstanceEbs(gp3VolumeSpec(total), modeInst);
          out.push({
            kind: "lookup",
            slug: "ebs.gp3-spec",
            lookup: "ebs.gp3-spec",
            chained: false,
            gp3: spec,
            volume_gib: spec.volume_gib,
            baseline_iops: spec.baseline_iops,
          });
          continue;
        }
        if (lookup !== "instance_select") throw new ModelError("unknown lookup kind '" + lookup + "'");
        if (!previous) throw new ModelError("instance_select: no previous step's band to pick against");
        const system = step.system || "aws-ec2";
        const catalog = catalogs[system] || corpus.instance_catalog || [];
        const pick = selectInstance(previous, catalog, step.family, ceiling === 0 ? null : ceiling);
        const slug = system + ".instance-select";
        out.push({
          kind: "lookup",
          slug: slug,
          lookup: slug,
          chained: true,
          pick: pick,
          pick_lo: pick.pick_lo ? pick.pick_lo.name : null,
          pick_mode: pick.pick_mode ? pick.pick_mode.name : null,
          pick_hi: pick.pick_hi ? pick.pick_hi.name : null,
          lo: previous.lo,
          mode: previous.mode,
          hi: previous.hi,
        });
      } else {
        throw new ModelError("unknown scenario step kind '" + kind + "'");
      }
    }

    const presented = out;
    return {
      scenario: scenario.slug,
      label: scenario.label,
      summary: scenario.summary,
      see_also: scenario.see_also || [],
      steps: presented,
      sizing_summary: buildInstanceSizingSummary(presented, inputs),
    };
  }

  return {
    ModelError: ModelError,
    parseBytes: parseBytes,
    parseNumber: parseNumber,
    formatBytes: formatBytes,
    formatQuantity: formatQuantity,
    formatG: formatG,
    evaluate: evaluate,
    headroom: headroom,
    checkGolden: checkGolden,
    chainEvaluate: chainEvaluate,
    selectInstance: selectInstance,
  };
})();

if (typeof module !== "undefined" && module.exports) module.exports = XY;
