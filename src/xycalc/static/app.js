// Calculator UI. Loaded by <script> after evaluate.js in the exported page
// and by require() under node for helper tests.
//
// The arithmetic lives in evaluate.js; this file is the page around it: tabs,
// scenario form, sweep chart, occupancy/cliff renderers, Simple mode. Pure
// helpers are exported so tests/test_export.py can pin them the same way it
// pins XY.

const XYCALC_APP = (() => {
  "use strict";

  const TABS = ["scenario", "math", "single", "flow", "occupancy", "cliff"];
  const MODES = ["basic", "advanced", "questions", "data"];
  const MODE_TABS = {
    basic: [],
    advanced: ["scenario"],
    questions: ["single"],
    data: ["math", "flow", "occupancy", "cliff"],
  };
  const MODE_DEFAULT_TAB = {
    advanced: "scenario",
    questions: "single",
    data: "math",
  };
  const SAMPLES = 96;

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function safeExternalUrl(raw) {
    if (!raw) return null;
    try {
      const u = new URL(String(raw), "https://example.invalid");
      if (u.protocol !== "http:" && u.protocol !== "https:") return null;
      return u.href;
    } catch (_) {
      return null;
    }
  }

  // Round tick values so the axis reads in figures a person would say out loud
  // (1, 2, 5, 10 ...) rather than in whatever the data range happened to be.
  function ticks(lo, hi, log, count) {
    const out = [];
    if (log && lo > 0) {
      const from = Math.floor(Math.log10(lo)), to = Math.ceil(Math.log10(hi));
      for (let e = from; e <= to; e++) {
        for (const m of [1, 2, 5]) {
          const v = m * Math.pow(10, e);
          if (v >= lo && v <= hi) out.push(v);
        }
      }
      while (out.length > count + 2) {
        for (let i = out.length - 2; i > 0; i -= 2) out.splice(i, 1);
        if (out.length <= count + 2) break;
      }
      return out;
    }
    const span = hi - lo;
    const raw = span / count;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const norm = raw / mag;
    const stepMul = norm > 5 ? 10 : norm > 2 ? 5 : norm > 1 ? 2 : 1;
    const step = stepMul * mag;
    for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) out.push(v);
    return out;
  }

  function nearestIndex(xs, x) {
    let best = 0, bestD = Infinity;
    for (let i = 0; i < xs.length; i++) {
      const d = Math.abs(Math.log(xs[i]) - Math.log(x));
      if (d < bestD) { bestD = d; best = i; }
    }
    return best;
  }

  function nearestPixelIndex(svg, clientX, chartWidth, xs, px) {
    const box = svg.getBoundingClientRect();
    const vx = ((clientX - box.left) / box.width) * chartWidth;
    let best = 0, bestD = Infinity;
    for (let i = 0; i < xs.length; i++) {
      const d = Math.abs(px(xs[i]) - vx);
      if (d < bestD) { bestD = d; best = i; }
    }
    return best;
  }

  function sweepBounds(centre, unit) {
    const from = centre ? centre / 10 : (unit === "bytes" ? 1e9 : 1);
    const to = centre ? centre * 10 : (unit === "bytes" ? 1e13 : 1e4);
    return { from: from, to: to };
  }

  function sweepGrid(from, to, samples, centre) {
    const grid = [];
    for (let i = 0; i < samples; i++) grid.push(from * Math.pow(to / from, i / (samples - 1)));
    if (centre) grid[nearestIndex(grid, centre)] = centre;
    return grid;
  }

  function scenarioInputList(s) {
    if (!s) return [];
    if (s.input_sections && s.input_sections.length) {
      const out = [];
      for (const sec of s.input_sections) {
        for (const inp of (sec.inputs || [])) out.push(inp);
      }
      return out;
    }
    return s.inputs || [];
  }

  function scenarioRequiredFieldsMissing(inputs, values) {
    return (inputs || []).filter((i) => i.required).some((i) => {
      const v = values[i.key];
      return v == null || !String(v).trim();
    });
  }

  function effectiveYScale(requested, yMin) {
    if (requested === "log" && yMin > 0) return "log";
    return "linear";
  }

  function chartLayout(W, H, L, R, T, B) {
    return { W: W, H: H, L: L, R: R, T: T, B: B, iw: W - L - R, ih: H - T - B };
  }

  function mapLogX(xLo, xHi, L, iw) {
    return (x) => L + (Math.log(x / xLo) / Math.log(xHi / xLo)) * iw;
  }

  function mapLogY(yMin, yMax, T, ih) {
    return (y) => T + ih - (Math.log(Math.max(y, yMin) / yMin) / Math.log(yMax / yMin)) * ih;
  }

  function mapLinY(yMin, yMax, T, ih) {
    return (y) => T + ih - ((y - yMin) / (yMax - yMin)) * ih;
  }

  function bindChartScrub(hit, svg, nearest, show, commit, sampleCount) {
    const count = sampleCount == null ? 0 : sampleCount;
    svg._scrub = {
      nearest: nearest,
      show: show,
      commit: commit,
      sampleCount: count,
      idx: svg._scrub && svg._scrub.idx != null ? svg._scrub.idx : 0,
    };
    const reveal = (i) => {
      const s = svg._scrub;
      if (!s.sampleCount) return;
      s.idx = Math.max(0, Math.min(s.sampleCount - 1, i));
      s.show(s.idx);
    };
    let dragging = false;
    if (commit) {
      hit.addEventListener("pointerdown", (e) => {
        dragging = true;
        svg.classList.add("dragging");
        try { hit.setPointerCapture(e.pointerId); } catch (_) {}
        reveal(svg._scrub.nearest(e.clientX));
      });
      hit.addEventListener("pointerup", (e) => {
        const i = svg._scrub.nearest(e.clientX);
        reveal(i);
        if (dragging && svg._scrub.commit) svg._scrub.commit(i);
        dragging = false;
        svg.classList.remove("dragging");
      });
      hit.addEventListener("pointercancel", () => {
        dragging = false;
        svg.classList.remove("dragging");
      });
    }
    hit.addEventListener("pointermove", (e) => reveal(svg._scrub.nearest(e.clientX)));
    if (svg.dataset.keyScrub !== "1") {
      svg.dataset.keyScrub = "1";
      svg.setAttribute("tabindex", "0");
      svg.addEventListener("keydown", (e) => {
        const s = svg._scrub;
        if (!s || !s.sampleCount) return;
        if (e.key === "ArrowLeft" || e.key === "ArrowDown") {
          e.preventDefault();
          reveal(s.idx - 1);
        } else if (e.key === "ArrowRight" || e.key === "ArrowUp") {
          e.preventDefault();
          reveal(s.idx + 1);
        } else if (e.key === "Home") {
          e.preventDefault();
          reveal(0);
        } else if (e.key === "End") {
          e.preventDefault();
          reveal(s.sampleCount - 1);
        } else if (e.key === "Enter" && s.commit) {
          e.preventDefault();
          s.commit(s.idx);
        }
      });
    }
    return reveal;
  }

  const SYSTEM_LABELS = {
    mongodb: "MongoDB",
    ebs: "AWS EBS",
    "azure-disks": "Azure disks",
    "nvme-ssd": "Local NVMe",
    nvd: "NVD / CVE growth",
    celery: "Celery / Redis",
    clickhouse: "ClickHouse",
  };
  const SYSTEM_ORDER = [
    "mongodb", "ebs", "azure-disks", "nvme-ssd", "nvd", "celery", "clickhouse",
  ];

  function systemLabel(system) {
    const key = String(system || "");
    return SYSTEM_LABELS[key] || (key ? key.replace(/-/g, " ") : "Other");
  }

  function shortModelTitle(model) {
    if (model && model.lab && model.lab.label) return model.lab.label;
    if (model && model.question) return model.question;
    return (model && model.slug) || "";
  }

  function modelMatchesQuery(model, raw) {
    const q = String(raw || "").trim().toLowerCase();
    if (!q) return true;
    if (!model) return false;
    const hay = [
      model.slug,
      model.question,
      model.system,
      shortModelTitle(model),
      systemLabel(model.system),
    ].join(" ").toLowerCase();
    return q.split(/\s+/).every((part) => hay.indexOf(part) >= 0);
  }

  function groupModelsBySystem(models) {
    const groups = {};
    for (const m of models || []) {
      const key = m.system || "other";
      if (!groups[key]) groups[key] = [];
      groups[key].push(m);
    }
    const keys = Object.keys(groups).sort((a, b) => {
      const ia = SYSTEM_ORDER.indexOf(a);
      const ib = SYSTEM_ORDER.indexOf(b);
      if (ia < 0 && ib < 0) return a < b ? -1 : a > b ? 1 : 0;
      if (ia < 0) return 1;
      if (ib < 0) return -1;
      return ia - ib;
    });
    return keys.map((key) => ({
      system: key,
      label: systemLabel(key),
      models: groups[key],
    }));
  }
  // Advanced scenario catalog: kind of question, not product. Empty groups
  // are omitted at render time. "core" stays open; the rest start in a drawer.
  const SCENARIO_KIND = {
    "mongodb.size-to-instance": { band: "hardware", group: "instance" },
    "mongodb.nvd-query-concurrency": { band: "runtime", group: "mongodb" },
    "ebs.microburst": { band: "hardware", group: "storage" },
    "storage.ebs-vs-nvme-at-io-size": { band: "hardware", group: "storage" },
    "clickhouse.parts-insert-ceiling": { band: "hardware", group: "database", sub: "clickhouse" },
    "redis.celery-broker": { band: "runtime", group: "redis" },
    "celery.queue-amplification": { band: "runtime", group: "celery" },
  };
  const SCENARIO_TAXONOMY = [
    {
      id: "hardware",
      label: "Hardware",
      groups: [
        { id: "instance", label: "Instance sizing", core: true },
        {
          id: "database",
          label: "Database internals",
          subs: [
            { id: "mongodb", label: "MongoDB" },
            { id: "clickhouse", label: "ClickHouse" },
          ],
        },
        { id: "storage", label: "Storage performance" },
        { id: "os", label: "OS performance" },
      ],
    },
    {
      id: "runtime",
      label: "Runtime",
      groups: [
        { id: "services", label: "Services" },
        { id: "celery", label: "Celery performance" },
        { id: "mongodb", label: "MongoDB performance" },
        { id: "redis", label: "Redis performance" },
      ],
    },
  ];

  function scenarioKind(s) {
    const slug = s && s.slug;
    const ui = s && s.ui;
    if (ui && ui.band && ui.group) {
      return { band: ui.band, group: ui.group, sub: ui.sub || null };
    }
    const mapped = (slug && SCENARIO_KIND[slug]) || { band: "runtime", group: "services" };
    return { band: mapped.band, group: mapped.group, sub: mapped.sub || null };
  }

  function groupByTaxonomy(list, kindFn) {
    const buckets = {};
    for (const item of list || []) {
      const k = kindFn(item);
      const key = k.band + "|" + k.group + "|" + (k.sub || "");
      if (!buckets[key]) buckets[key] = [];
      buckets[key].push(item);
    }
    const bands = [];
    for (const band of SCENARIO_TAXONOMY) {
      const groups = [];
      for (const g of band.groups) {
        const bare = buckets[band.id + "|" + g.id + "|"] || [];
        const subs = [];
        for (const sub of g.subs || []) {
          const items = buckets[band.id + "|" + g.id + "|" + sub.id] || [];
          if (items.length) subs.push({ id: sub.id, label: sub.label, items: items });
        }
        if (!bare.length && !subs.length) continue;
        groups.push({
          id: g.id,
          label: g.label,
          core: !!g.core,
          items: bare,
          subs: subs,
        });
      }
      if (groups.length) bands.push({ id: band.id, label: band.label, groups: groups });
    }
    return bands;
  }

  function groupScenarios(scenarios) {
    return groupByTaxonomy(scenarios, scenarioKind);
  }

  const MODEL_KIND = {
    "mongodb.wt-cache": { band: "hardware", group: "instance" },
    "mongodb.host-ram": { band: "hardware", group: "instance" },
    "mongodb.storage-from-doc-families": { band: "hardware", group: "instance" },
    "nvd.storage-from-vuln-growth": { band: "hardware", group: "instance" },
    "mongodb.ticket-throughput-ceiling": { band: "runtime", group: "mongodb" },
    "ebs.iops-to-provision": { band: "hardware", group: "storage" },
    "ebs.gp3-iops-at-io-size": { band: "hardware", group: "storage" },
    "nvme-ssd.random-read-at-io-size": { band: "hardware", group: "storage" },
    "azure.premium-v2-throughput-ceiling": { band: "hardware", group: "storage" },
    "clickhouse.parts-insert-ceiling": { band: "hardware", group: "database", sub: "clickhouse" },
    "celery.queue-amplification": { band: "runtime", group: "celery" },
    "celery.worker-prefetch": { band: "runtime", group: "celery" },
    "celery.redis-broker-maxmemory": { band: "runtime", group: "redis" },
  };

  function modelKind(m) {
    const slug = m && m.slug;
    const ui = m && m.ui;
    if (ui && ui.band && ui.group) {
      return { band: ui.band, group: ui.group, sub: ui.sub || null };
    }
    const mapped = (slug && MODEL_KIND[slug]) || { band: "runtime", group: "services" };
    return { band: mapped.band, group: mapped.group, sub: mapped.sub || null };
  }

  function groupModelsByKind(models) {
    return groupByTaxonomy(models, modelKind);
  }

  function scenarioSectionIsDrawer(sec) {
    const title = String((sec && sec.title) || "");
    if (/optional/i.test(title)) return true;
    if (/concurrency|celery|query regime/i.test(title)) return true;
    const inputs = (sec && sec.inputs) || [];
    return inputs.length > 0 && inputs.every((i) => !i.required);
  }
  const GRADE_RANK = { none: 0, thin: 1, reasonable: 2 };
  const GRADE_LABEL = { none: "Unvalidated", thin: "Thinly validated", reasonable: "Validated" };
  const GRADE_PILL = { none: "unvalidated", thin: "thinly validated", reasonable: "validated" };
  const PERMALINK_RESERVED = ["mode", "tab", "model", "scenario", "available", "sci"];
  const TAB_ALIASES = {
    "cache-cliff": "cliff",
    cache_cliff: "cliff",
    cliff: "cliff",
    "occupancy-bands": "occupancy",
    occupancy_bands: "occupancy",
    occupancy: "occupancy",
    scenario: "scenario",
    math: "math",
    "cited-math": "math",
    cited_math: "math",
    single: "single",
    flow: "flow",
  };
  const TAB_PUBLIC = { cliff: "cache-cliff" };

  function canonicalTab(tab) {
    if (tab == null || tab === "") return null;
    const key = String(tab);
    if (Object.prototype.hasOwnProperty.call(TAB_ALIASES, key)) return TAB_ALIASES[key];
    if (TABS.indexOf(key) >= 0) return key;
    return null;
  }

  function publicTab(tab) {
    const c = canonicalTab(tab);
    if (!c) return tab || "";
    return TAB_PUBLIC[c] || c;
  }

  function canonicalMode(mode) {
    if (mode === "simple" || mode === "basic") return "basic";
    if (mode === "questions" || mode === "advanced" || mode === "data") return mode;
    // Old Scientific view: cited math / flow live under Data now.
    if (mode === "scientific") return "data";
    return "basic";
  }

  function modeForTab(tab) {
    const c = canonicalTab(tab);
    if (!c) return null;
    for (let i = 0; i < MODES.length; i++) {
      const m = MODES[i];
      if ((MODE_TABS[m] || []).indexOf(c) >= 0) return m;
    }
    return null;
  }

  // Turn a parsed hash into the mode/tab the UI should show. Tab aliases
  // (cache-cliff → cliff) live here so boot cannot treat an inbound cliff
  // link as "no tab" and fall through to the Scenario default.
  function permalinkView(parsed) {
    if (!parsed) return null;
    const tab = canonicalTab(parsed.tab);
    let mode = parsed.mode ? canonicalMode(parsed.mode) : null;
    const fromTab = tab ? modeForTab(tab) : null;
    if (fromTab) mode = fromTab;
    else if (parsed.model) mode = "questions";
    else if (parsed.scenario) mode = "advanced";
    else if (!mode) mode = "basic";
    let nextTab = tab;
    if (!nextTab && parsed.model) nextTab = "single";
    else if (!nextTab && parsed.scenario) nextTab = "scenario";
    else if (!nextTab) nextTab = MODE_DEFAULT_TAB[mode] || null;
    if (mode === "basic") return { mode: "basic", tab: null };
    return { mode: mode, tab: nextTab || null };
  }

  function permalinkHref(hash, loc) {
    const h = String(hash || "");
    const frag = h.startsWith("#") ? h : (h ? "#" + h : "#");
    const path = loc && loc.pathname != null ? loc.pathname : "";
    const search = loc && loc.search != null ? loc.search : "";
    return path + search + frag;
  }

  // Hash is the canonical form landing tables should emit. Query-string
  // `?model=` is accepted on first paint so a table href that used the
  // issue-82 sketch still preselects a model.
  function permalinkFromLocation(loc) {
    const place = loc || {};
    return parsePermalink(place.hash) || parsePermalink(place.search);
  }

  function validationClause(v) {
    if (!v) return "";
    return v.text || v.summary || v.note || "";
  }

  function validationBannerInner(v) {
    if (!v || v.grade == null || v.grade === "") return "";
    const label = GRADE_LABEL[v.grade] || v.grade;
    return `<strong>${esc(label)}</strong> — ${esc(validationClause(v))}`;
  }

  function validationBannerHtml(v) {
    if (!v || v.grade == null || v.grade === "") return "";
    const cls = v.grade === "reasonable" ? " reasonable" : "";
    return `<div class="validation${cls} ${esc(v.grade)}">${validationBannerInner(v)}</div>`;
  }

  // Simple first paint must not look like a cited buy-size. The full chain
  // lives in Advanced; this is the line that cannot be missed if we refuse
  // to mini-render every quote here.
  const SIMPLE_HONESTY_LINE = "What are we missing?";

  // Bench findings a planner must see on the default MongoDB size path.
  // Sentences are pinned; do not invent numbers. Occupancy/cliff and EBS
  // ride the models in mongodb.size-to-instance. Tickets are not a chain
  // step — they attach only to that scenario (and the ticket model itself)
  // because 128 as a default is the dangerous-direction miss (#2).
  const SIZE_PATH_FOOTNOTES = {
    "mongodb.wt-cache": {
      id: "occupancy-cliff",
      text: "Occupancy / cache-cliff (FINDINGS 006 / FINDINGS 007): performance erodes before 1.0×; steepest slope is 0.8→1.0 (log–log ≈ −3.8). 80% is a hold target, 95% is the conscription cliff. Not a new GB coefficient.",
    },
    "mongodb.ticket-throughput-ceiling": {
      id: "tickets",
      text: "Tickets (FINDINGS 003, issue #2): formula is pinned-N. Idle 7.0 floor is 4, not 128 (wrong by up to 32×, dangerous direction). Under stall, ops/s stayed ~110 while tickets climbed 4→74; after load, N stuck at 64 for ≥611 s. Default 128 is not a measured ceiling.",
    },
    "ebs.iops-to-provision": {
      id: "ebs-peak-to-mean",
      text: "EBS peak-to-mean (issue #4, cooper-burst-2026-08-21): coefficient remains estimate 1.5–3–10 (factor 6.7), n=0, band not narrowed. Lab: batch median 1.59, bursty median 1.16, one window 12.59 > hi 10.",
    },
  };
  const SIZE_PATH_RELATED_FOOTNOTES = {
    "mongodb.size-to-instance": ["mongodb.ticket-throughput-ceiling"],
  };

  function footnoteForModel(slug) {
    return SIZE_PATH_FOOTNOTES[slug] || null;
  }

  function footnoteHtml(fn) {
    if (!fn || !fn.text) return "";
    return `<p class="size-path-footnote" data-footnote="${esc(fn.id)}">${esc(fn.text)}</p>`;
  }

  function cascadeStepFootnotesHtml(modelSlug) {
    return footnoteHtml(footnoteForModel(modelSlug));
  }

  function chainFootnoteSlugs(steps) {
    const slugs = [];
    const seen = {};
    for (const st of steps || []) {
      const slug = st && st.model;
      if (!slug || !SIZE_PATH_FOOTNOTES[slug] || seen[slug]) continue;
      seen[slug] = true;
      slugs.push(slug);
    }
    return slugs;
  }

  function relatedFootnoteSlugs(scenarioSlug) {
    return (SIZE_PATH_RELATED_FOOTNOTES[scenarioSlug] || []).slice();
  }

  function sizePathFootnoteSlugs(steps, scenarioSlug) {
    const slugs = chainFootnoteSlugs(steps);
    const seen = {};
    for (const s of slugs) seen[s] = true;
    for (const extra of relatedFootnoteSlugs(scenarioSlug)) {
      if (SIZE_PATH_FOOTNOTES[extra] && !seen[extra]) {
        seen[extra] = true;
        slugs.push(extra);
      }
    }
    return slugs;
  }

  function sizePathFootnotesHtml(steps, scenarioSlug) {
    return sizePathFootnoteSlugs(steps, scenarioSlug)
      .map((slug) => footnoteHtml(SIZE_PATH_FOOTNOTES[slug]))
      .join("");
  }

  function infoCardHtml(kicker, title, paragraphs) {
    const paras = (Array.isArray(paragraphs) ? paragraphs : [paragraphs])
      .filter((p) => p != null && String(p).trim() !== "")
      .map((p) => `<p>${esc(p)}</p>`)
      .join("");
    return `<div class="info-card">
      <div class="kicker">${esc(kicker)}</div>
      ${title ? `<div class="primary">${esc(title)}</div>` : ""}
      ${paras}
    </div>`;
  }

  function widestCitedTerm(model) {
    let best = null;
    let factor = 1;
    for (const t of (model && model.terms) || []) {
      if (!(t.coeff_lo > 0) || t.coeff_hi == null) continue;
      const f = t.coeff_hi / t.coeff_lo;
      if (f > factor) {
        factor = f;
        best = t;
      }
    }
    return best ? { term: best, factor: factor } : null;
  }

  function answerRangeFactor(result) {
    if (!result || !(result.lo > 0) || result.hi == null) return null;
    return result.hi / result.lo;
  }

  function modelAsideHtml(model, result, fmt) {
    if (!model) return "";
    const cards = [];
    const factor = answerRangeFactor(result);
    if (factor != null) {
      const band = fmt
        ? fmt(result.lo, result.unit) + " – " + fmt(result.hi, result.unit)
        : String(result.lo) + " – " + String(result.hi);
      const rangeNote = factor <= 2
        ? "Tight enough that lo and hi land in the same conversation."
        : "Wide — read the whole band, not the mode, as the answer.";
      const shown = factor >= 10 ? factor.toFixed(1) : factor.toFixed(2);
      cards.push(infoCardHtml("This answer's range", band, shown + "× from lo to hi. " + rangeNote));
    }
    const v = displayValidation(model.validation);
    if (v && v.grade) {
      cards.push(infoCardHtml(
        "Checked against reality",
        GRADE_LABEL[v.grade] || v.grade,
        validationClause(v),
      ));
    }
    const lab = model.lab || {};
    if (lab.measured) {
      cards.push(infoCardHtml("What we measured", lab.label || "", [
        lab.measured,
        lab.still_needs ? "Still needs: " + lab.still_needs : "",
      ]));
    }
    const wide = widestCitedTerm(model);
    if (wide && wide.factor > 1.01) {
      const t = wide.term;
      const band = [t.coeff_lo, t.coeff_mode, t.coeff_hi].filter((x) => x != null).join("–");
      const shown = wide.factor >= 10 ? wide.factor.toFixed(1) : wide.factor.toFixed(2);
      cards.push(infoCardHtml(
        "Widest cited coefficient",
        t.label || t.key,
        band + (t.unit ? " " + t.unit : "") + " · factor " + shown + "×. The sentence is under Show the math.",
      ));
    }
    const fn = footnoteForModel(model.slug);
    if (fn && fn.text) {
      cards.push(infoCardHtml("Related finding", "", fn.text));
    }
    return cards.length ? `<div class="info-stack">${cards.join("")}</div>` : "";
  }

  function basicAsideHtml() {
    const cards = [
      infoCardHtml(
        "What to type",
        "storageSize, not dataSize",
        "Compressed on-disk collection bytes (db.stats / collStats). dataSize is already uncompressed; totalSize includes indexes — those are a separate input. Records path is count × avg as a starting guess until you measure.",
      ),
      infoCardHtml(
        "Record defaults",
        "4 MB · vulns 14 MB",
        "Starting guesses only — not a cited coefficient. 4 MB is a typical BSON-sized document; 14 MB is near MongoDB's 16 MB document cap for fat vuln records. Override with storageSize ÷ count from collStats when you have it.",
      ),
    ];
    const occ = SIZE_PATH_FOOTNOTES["mongodb.wt-cache"];
    if (occ) cards.push(infoCardHtml("Occupancy / cache-cliff", "", occ.text));
    const tix = SIZE_PATH_FOOTNOTES["mongodb.ticket-throughput-ceiling"];
    if (tix) cards.push(infoCardHtml("Tickets", "", tix.text));
    const ebs = SIZE_PATH_FOOTNOTES["ebs.iops-to-provision"];
    if (ebs) cards.push(infoCardHtml("EBS peak-to-mean", "", ebs.text));
    return `<div class="info-stack">${cards.join("")}</div>`;
  }

  function chainModelValidations(steps) {
    const out = [];
    for (const st of steps || []) {
      if (st && st.kind === "model") out.push(st.validation);
    }
    return out;
  }

  function zeroInBand(v) {
    if (!v) return false;
    if (v.within_band === 0) return true;
    return /\b0 within band\b/.test(String(v.text || v.summary || ""));
  }

  // Display-time pin for #118: a stale export that still shipped
  // grade=reasonable with 0 in-band hits must not paint Validated on Simple.
  function displayValidation(v) {
    if (!v || v.grade == null) return v;
    if (v.grade === "reasonable" && zeroInBand(v)) {
      const copy = {};
      for (const k of Object.keys(v)) copy[k] = v[k];
      copy.grade = "thin";
      return copy;
    }
    return v;
  }

  function simpleWeakestValidation(steps) {
    return displayValidation(weakestValidation(chainModelValidations(steps)));
  }

  function simpleHonestyBlockHtml(kind) {
    if (kind === "scientific") {
      return `<span class="calc-tag" id="simple-honesty">Cited math below</span>`;
    }
    return `<button type="button" class="calc-tag calc-tag-miss" id="simple-open-scientific">${esc(SIMPLE_HONESTY_LINE)}</button>`;
  }

  function simpleGradeTagHtml(v) {
    if (!v || v.grade == null || v.grade === "") return "";
    const label = GRADE_LABEL[v.grade] || v.grade;
    const clause = validationClause(v);
    const cls = v.grade === "reasonable" ? " reasonable" : "";
    return `<span class="calc-tag calc-tag-grade ${esc(v.grade)}${cls}" title="${esc(clause)}"><strong>${esc(label)}</strong><span class="sr-only"> — ${esc(clause)}</span></span>`;
  }

  function simpleCatalogMissReason(pick, fmt) {
    if (!pick || !pick.largest_in_pool) {
      return "the catalog has no fit for this RAM band";
    }
    const largest = pick.largest_in_pool;
    const sku = largest.name || "largest SKU";
    const ramLabel = fmt && largest.ram_bytes != null ? fmt(largest.ram_bytes, "bytes") : "";
    const sized = ramLabel ? sku + ", " + ramLabel : sku;
    if (pick.exceeds_pool) {
      return "exceeds the " + sized + " band — the catalog has no fit";
    }
    return "the catalog has no fit (largest in pool: " + sized + ")";
  }

  function simplePickCardHtml(end, ram, pick, fmt) {
    const name = end.name;
    const spec = end.ram != null && ram && fmt ? fmt(end.ram, ram.unit) + " RAM" : "";
    const miss = name ? "" : simpleCatalogMissReason(pick, fmt);
    const title = name || "custom sizing";
    return `<div class="simple-pick${end.key === "mode" ? " mode-pick" : ""}">
      <div class="which">${esc(end.label)}</div>
      <div class="name">${esc(title)}</div>
      <div class="spec">${esc(spec)}</div>
      ${miss ? `<div class="miss">${esc(miss)}</div>` : ""}
    </div>`;
  }

  function skuOrCustom(name) {
    return name || "custom sizing";
  }

  function simpleCloudPicksHtml(s, ram, awsPick, fmt) {
    const aws = s && s.cpu;
    const azure = s && s.azure;
    if (!aws && !azure) return "";
    const cell = (name) => `<td><div class="name">${esc(skuOrCustom(name))}</div></td>`;
    const ends = [
      { key: "lo", label: "Low", aws: aws && aws.instance_lo, azure: azure && azure.lo },
      { key: "mode", label: "Typical", aws: aws && aws.instance_mode, azure: azure && azure.mode },
      { key: "hi", label: "High", aws: aws && aws.instance_hi, azure: azure && azure.hi },
    ];
    const body = ends.map((row) => `<tr>
      <th scope="row">${esc(row.label)}</th>
      ${aws ? cell(row.aws) : ""}
      ${azure ? cell(row.azure) : ""}
    </tr>`).join("");
    return `<table class="cloud-picks">
        <thead><tr><th></th>
          ${aws ? `<th>AWS <span class="note">r8i → U7i</span></th>` : ""}
          ${azure ? `<th>Azure <span class="note">Esv5 / Esv6</span></th>` : ""}
        </tr></thead>
        <tbody>${body}</tbody>
      </table>`;
  }

  function simpleRamHonestyOk(ramText, bannerHtml, weakest) {
    if (!ramText) return true;
    if (!weakest || weakest.grade == null) return false;
    const label = GRADE_LABEL[weakest.grade] || weakest.grade;
    const clause = validationClause(weakest);
    if (!clause) return false;
    const banner = String(bannerHtml || "");
    if (banner.indexOf(label) < 0) return false;
    if (banner.indexOf(clause) < 0 && banner.indexOf(esc(clause)) < 0) return false;
    if (zeroInBand(weakest) && /<strong>Validated<\/strong>/.test(banner)) return false;
    if (weakest.grade !== "reasonable" && /<strong>Validated<\/strong>/.test(banner)) return false;
    return true;
  }

  function simpleFirstPaintHtml(data, fmt, kind) {
    const s = (data && data.sizing_summary) || {};
    const ram = s.ram;
    const pick = (data && data.simple_instance_pick) || null;
    const weakest = simpleWeakestValidation(data && data.steps);
    const bannerHtml = simpleGradeTagHtml(weakest);
    const honestyHtml = simpleHonestyBlockHtml(kind);
    const footnotesHtml = sizePathFootnotesHtml(
      data && data.steps,
      "mongodb.size-to-instance",
    );
    let ramText = ram && fmt ? fmt(ram.mode, ram.unit) : "";
    if (ramText && !simpleRamHonestyOk(ramText, bannerHtml, weakest)) ramText = "";
    const picksHtml = simpleCloudPicksHtml(s, ram, pick, fmt);
    const r6 = s.r6i;
    const r6iHtml = r6
      ? `<div class="simple-family"><div class="which">r6i family</div>
         <div class="name">${esc(r6.mode || "custom sizing")}</div>
         <div class="spec">${r6.exceeds_pool
           ? "ceiling " + esc(r6.largest || "r6i.32xlarge") + " · 1,024 GiB"
           : ""}</div>
         ${r6.exceeds_pool
           ? `<div class="miss">this RAM band is above r6i — stay on r6i only below 1,024 GiB, or use the r8i/U7i pick</div>`
           : ""}</div>`
      : "";
    return {
      ramText: ramText,
      weakest: weakest,
      bannerHtml: bannerHtml,
      honestyHtml: honestyHtml,
      picksHtml: picksHtml,
      r6iHtml: r6iHtml,
      footnotesHtml: footnotesHtml,
      html: (ramText || "") + honestyHtml + bannerHtml + footnotesHtml + picksHtml + r6iHtml,
    };
  }

  function gradeSuffix(grade) {
    const phrase = GRADE_PILL[grade];
    return phrase ? " · " + phrase : "";
  }

  function weakestValidation(validations) {
    let worst = null;
    for (const v of validations || []) {
      if (!v || v.grade == null) continue;
      if (!worst || (GRADE_RANK[v.grade] ?? 0) < (GRADE_RANK[worst.grade] ?? 0)) worst = v;
    }
    return worst;
  }

  function occupancyMarkClass(pct, index) {
    const bits = ["mark"];
    if (index % 2) bits.push("below");
    if (pct <= 8) bits.push("edge-start");
    if (pct >= 92) bits.push("edge-end");
    return bits.join(" ");
  }

  function interpolateCrossingXs(xs, ys, yTarget) {
    if (!xs || !ys || yTarget == null || !isFinite(yTarget)) return [];
    const out = [];
    for (let i = 0; i < xs.length - 1; i++) {
      const a = ys[i], b = ys[i + 1];
      if (a == null || b == null || !isFinite(a) || !isFinite(b)) continue;
      if (a === yTarget) {
        out.push(xs[i]);
        continue;
      }
      if ((a - yTarget) * (b - yTarget) >= 0) continue;
      if (a === b) continue;
      const t = (yTarget - a) / (b - a);
      if (t <= 0 || t >= 1) continue;
      const x0 = xs[i], x1 = xs[i + 1];
      if (x0 > 0 && x1 > 0) out.push(x0 * Math.pow(x1 / x0, t));
      else out.push(x0 + t * (x1 - x0));
    }
    if (xs.length && ys[ys.length - 1] === yTarget) out.push(xs[xs.length - 1]);
    return out;
  }

  function coverageX(xs, ys, avail) {
    const crosses = interpolateCrossingXs(xs, ys, avail);
    if (crosses.length) return crosses[0];
    if (ys && ys.length && ys.every((y) => y != null && y <= avail)) return xs[xs.length - 1];
    return null;
  }

  function bandCoverageCaption(availLabel, xs, los, modes, his, avail, fmtX, inputLabel) {
    if (avail == null || !xs || xs.length < 2) return "";
    const modeX = coverageX(xs, modes, avail);
    const hiX = coverageX(xs, his, avail);
    const bits = [];
    if (modeX != null) bits.push("the mode to ~" + fmtX(modeX));
    if (hiX != null && hiX !== modeX) bits.push("the whole band to ~" + fmtX(hiX));
    const ofWhat = inputLabel ? " (" + inputLabel + ")" : "";
    if (bits.length) return availLabel + " covers " + bits.join(", ") + ofWhat;
    const maxReq = Math.max.apply(null, his);
    const minReq = Math.min.apply(null, los);
    if (avail >= maxReq) return availLabel + " covers the whole swept range" + ofWhat;
    if (avail < minReq) return availLabel + " sits below the whole band on this sweep";
    return "";
  }

  function serializePermalink(state) {
    const p = new URLSearchParams();
    if (state.mode) p.set("mode", state.mode);
    if (state.tab) p.set("tab", publicTab(state.tab));
    if (state.model) p.set("model", state.model);
    if (state.scenario) p.set("scenario", state.scenario);
    if (state.available) p.set("available", state.available);
    if (state.sci) p.set("sci", state.sci);
    const reserved = {};
    for (const k of PERMALINK_RESERVED) reserved[k] = true;
    for (const key of Object.keys(state.inputs || {})) {
      const v = state.inputs[key];
      if (v == null || v === "" || reserved[key]) continue;
      p.set(key, String(v));
    }
    return p.toString();
  }

  function parsePermalink(hash) {
    const raw = String(hash || "").replace(/^[?#]/, "");
    if (!raw) return null;
    const p = new URLSearchParams(raw);
    const out = { inputs: {} };
    let any = false;
    for (const key of PERMALINK_RESERVED) {
      const v = p.get(key);
      if (!v) continue;
      out[key] = key === "tab" ? (canonicalTab(v) || v) : v;
      any = true;
    }
    for (const pair of p.entries()) {
      if (PERMALINK_RESERVED.indexOf(pair[0]) >= 0) continue;
      if (pair[1] === "") continue;
      out.inputs[pair[0]] = pair[1];
      any = true;
    }
    return any ? out : null;
  }

  function normalizeTerm(s) {
    const t = s.term || s;
    return {
      role: t.role,
      label: t.label,
      skipped: s.skipped,
      skip_reason: s.skip_reason,
      contribution: s.contribution,
      mode: s.running ? s.running.mode : s.mode,
      rationale: t.rationale,
      confidence: t.confidence,
      source: t.source,
      source_url: t.source_url,
      applies_to: t.applies_to,
      quote: t.quote,
    };
  }

  function formatCitation(block, meta) {
    const lines = [];
    lines.push("xycalc: " + (block.question || "").trim());
    lines.push("");
    if (block.mode) {
      lines.push("Mode " + block.mode + (block.lo && block.hi ? "  band " + block.lo + " – " + block.hi : ""));
    }
    if (block.validation) lines.push("Validation: " + block.validation);
    const terms = block.terms || [];
    if (terms.length) {
      lines.push("");
      lines.push("Sources");
      for (const t of terms) {
        if (t.skipped) continue;
        const src = t.source_url ? t.source + " <" + t.source_url + ">" : (t.source || "");
        const bit = [t.label, t.contribution, src].filter(Boolean).join(" — ");
        lines.push("- " + bit);
        if (t.quote) lines.push("  \"" + String(t.quote).replace(/\s+/g, " ").trim() + "\"");
      }
    }
    lines.push("");
    lines.push(
      "Corpus " + (meta.digest || "") +
      " · xycalc " + (meta.version || "") +
      " · " + (meta.git || "")
    );
    return lines.join("\n");
  }

  // Advanced/corpus parseBytes treats a bare number as bytes. Simple users
  // mean GB for collection footprints — "50" → "50GB". Explicit units
  // (GB, GiB, TB, …) pass through.
  function normalizeSimpleSize(raw) {
    const t = String(raw || "").trim();
    if (!t) return t;
    if (/^[0-9.]+$/.test(t)) return t + "GB";
    return t;
  }

  // Per-document averages are usually bytes–KB–MB. Bare number = bytes;
  // explicit units pass through to parseBytes.
  function normalizeSimpleAvgBytes(raw) {
    const t = String(raw || "").trim();
    if (!t) return t;
    if (/^[0-9.]+$/.test(t)) return t;
    return t;
  }

  // Documents-path avg: people mean megabytes. Bare number = MB.
  const SIMPLE_DOC_AVG_DEFAULT = "4 MB";
  const SIMPLE_VULN_DOC_AVG_DEFAULT = "14 MB";

  function normalizeSimpleDocAvg(raw) {
    const t = String(raw || "").trim();
    if (!t) return t;
    if (/^[0-9.]+$/.test(t)) return t + "MB";
    return t;
  }

  function canonicalSimpleDocAvg(raw) {
    return normalizeSimpleDocAvg(raw).replace(/\s+/g, "").toUpperCase();
  }

  function simpleDocAvgIsDefault(raw, vulnDocs) {
    const want = vulnDocs ? SIMPLE_VULN_DOC_AVG_DEFAULT : SIMPLE_DOC_AVG_DEFAULT;
    return canonicalSimpleDocAvg(raw) === canonicalSimpleDocAvg(want);
  }

  function simpleDocProductStorage(countRaw, avgRaw) {
    const n = Number(String(countRaw || "").replace(/,/g, "").trim());
    const avg = normalizeSimpleDocAvg(avgRaw);
    if (!n || n < 0 || !isFinite(n) || !avg) return null;
    if (typeof XY === "undefined" || !XY.parseBytes || !XY.formatQuantity) return null;
    const bytes = n * XY.parseBytes(avg);
    if (!isFinite(bytes) || bytes <= 0) return null;
    return XY.formatQuantity(bytes, "bytes").replace(/\s+/g, "");
  }

  // IDs calculator.html must ship exactly once. bootSimple() throws if any
  // are missing; a duplicate id is an HTML uniqueness failure the export
  // tests catch before the page can boot against the wrong node.
  const SIMPLE_FORM_FIELD_IDS = [
    "simple-vulns",
    "simple-size-path",
    "simple-db-size",
    "simple-doc-count",
    "simple-doc-avg",
    "simple-doc-vulns",
    "simple-devices",
    "simple-device-avg",
    "simple-residual",
  ];
  const BASIC_DEFAULT_VULN_COUNT = "250000";
  const SIMPLE_SIZE_SLIDER_MIN_GB = 10;
  const SIMPLE_SIZE_SLIDER_MAX_GB = 32000;
  const SIMPLE_SIZE_SLIDER_STEPS = 80;

  function simpleSizeSliderGb(index) {
    const t = Math.max(0, Math.min(SIMPLE_SIZE_SLIDER_STEPS, Number(index))) / SIMPLE_SIZE_SLIDER_STEPS;
    const gb = SIMPLE_SIZE_SLIDER_MIN_GB * Math.pow(
      SIMPLE_SIZE_SLIDER_MAX_GB / SIMPLE_SIZE_SLIDER_MIN_GB,
      t,
    );
    if (gb < 20) return Math.round(gb);
    if (gb < 100) return Math.round(gb / 5) * 5;
    if (gb < 1000) return Math.round(gb / 10) * 10;
    if (gb < 10000) return Math.round(gb / 50) * 50;
    return Math.round(gb / 100) * 100;
  }

  function formatSimpleSizeSliderGb(gb) {
    if (gb >= 1000) {
      const tb = gb / 1000;
      const n = tb >= 10 ? Math.round(tb) : Math.round(tb * 10) / 10;
      return n + "TB";
    }
    return gb + "GB";
  }

  function simpleSizeSliderIndex(gb) {
    const g = Math.max(SIMPLE_SIZE_SLIDER_MIN_GB, Math.min(SIMPLE_SIZE_SLIDER_MAX_GB, Number(gb) || SIMPLE_SIZE_SLIDER_MIN_GB));
    const t = Math.log(g / SIMPLE_SIZE_SLIDER_MIN_GB) /
      Math.log(SIMPLE_SIZE_SLIDER_MAX_GB / SIMPLE_SIZE_SLIDER_MIN_GB);
    return Math.round(t * SIMPLE_SIZE_SLIDER_STEPS);
  }

  // Next calendar year is the cited YoY band applied to the last observed
  // annual count — not an NVD forecast. The coefficient notes say the band
  // brackets recent publication rates for a one-year add_fraction.
  function nvdNextYearProjection(chart) {
    const years = chart && chart.annual;
    const g = chart && chart.growth_pct;
    if (!years || !years.length || !g || g.mode == null) return null;
    const last = years[years.length - 1];
    if (!last || !(last.count > 0) || last.year == null) return null;
    const scale = (pct) => last.count * (1 + Number(pct) / 100);
    return {
      year: last.year + 1,
      lo: scale(g.lo),
      mode: scale(g.mode),
      hi: scale(g.hi),
    };
  }
  // Basic → mongodb.size-to-instance. DB size is today's footprint. Vuln
  // count defaults to the homepage 250000 with target == baseline so a 500 GB
  // box stays 500 GB instead of picking up three-year NVD growth.
  function simpleChainInputs(fields) {
    const src = fields || {};
    const path = String(src.path || "db").trim() || "db";
    let storageRaw = "";
    if (path === "docs") {
      storageRaw = simpleDocProductStorage(src.docs || src.docCount, src.docAvg);
      if (!storageRaw) return null;
    } else {
      storageRaw = String(src.storage || src.size || "").trim();
      if (!storageRaw) return null;
    }
    const vulns = String(src.vulns || BASIC_DEFAULT_VULN_COUNT).replace(/,/g, "").trim()
      || BASIC_DEFAULT_VULN_COUNT;
    const devices = String(src.devices || "").replace(/,/g, "").trim();
    const deviceAvgRaw = String(src.deviceAvg || "").trim();
    const residualRaw = String(src.residual || "").trim();
    const devicePairComplete = !!(devices && deviceAvgRaw);
    const inputs = {
      baseline_vuln_count: vulns,
      baseline_storage_size: normalizeSimpleSize(storageRaw),
      target_vuln_count: vulns,
    };
    if (devicePairComplete) {
      inputs.device_count = devices;
      inputs.device_avg_storage_bytes = normalizeSimpleAvgBytes(deviceAvgRaw);
    }
    if (residualRaw) {
      inputs.residual_storage_size = normalizeSimpleSize(residualRaw);
    }
    return inputs;
  }

  function scenarioChoiceValue(key, raw) {
    const t = String(raw == null ? "" : raw).trim();
    if (t) return t;
    if (key === "query_regime") return "fallback";
    if (key === "fallback_reason") return "mixed";
    return "";
  }

  function scenarioInputControlHtml(input, value) {
    if (!input || !input.key) return "";
    const current = scenarioChoiceValue(input.key, value);
    const optional = input.required ? "" : " <span class='help' style='display:inline'>optional</span>";
    const help = input.help ? `<div class="help">${esc(input.help)}</div>` : "";
    const id = `scn-in-${esc(input.key)}`;
    if (input.key === "query_regime") {
      const button = (val, label) => {
        const active = current === val;
        return `<button type="button" class="scenario-seg-btn${active ? " active" : ""}" data-segment-for="${esc(input.key)}" data-value="${esc(val)}" aria-pressed="${active ? "true" : "false"}">${esc(label)}</button>`;
      };
      return `<div class="field">
        <label>${esc(input.label)}${optional}</label>
        <div class="scenario-segmented" role="group" aria-label="${esc(input.label)}">
          ${button("fallback", "fallback")}
          ${button("aggregation", "aggregation")}
          <input type="hidden" id="${id}" data-key="${esc(input.key)}" value="${esc(current)}">
        </div>
        ${help}
      </div>`;
    }
    if (input.key === "fallback_reason") {
      const options = [
        ["mixed", "mixed"],
        ["limit", "limit"],
        ["allowlist", "allowlist"],
      ].map(([val, label]) =>
        `<option value="${esc(val)}"${current === val ? " selected" : ""}>${esc(label)}</option>`
      ).join("");
      return `<div class="field">
        <label for="${id}">${esc(input.label)}${optional}</label>
        <select id="${id}" data-key="${esc(input.key)}">${options}</select>
        ${help}
      </div>`;
    }
    const placeholder = input.unit === "bytes" ? "e.g. 500GB" : esc(input.unit);
    return `<div class="field">
      <label for="${id}">${esc(input.label)}${optional}</label>
      <input id="${id}" data-key="${esc(input.key)}" value="${esc(current)}" placeholder="${placeholder}" autocomplete="off">
      ${help}
    </div>`;
  }

  function scenarioSectionCopyHtml(title) {
    if (title !== "Concurrency and Celery" && title !== "Celery demand") return "";
    return `<div class="section-copy">
      <p>More Celery workers increase in-flight scans and broker occupancy. They do not raise the stall completion ceiling (investigation 004).</p>
      <p>scan_fanout is how many queries one task issues. It is not a COLSCAN latency multiplier. Measure ticket hold time L under your fallback workload and submit an observation.</p>
      <p>Allow-list misses never use v1/v2 aggregation; size fallback for that traffic.</p>
    </div>`;
  }

  function queryRegimeSizingNote(fallbackReason) {
    const reason = scenarioChoiceValue("fallback_reason", fallbackReason);
    if (reason === "allowlist") {
      return "Allow-list misses never use v1/v2 aggregation; size fallback for that traffic.";
    }
    return "The numeric sizing band is the same regardless of query regime; aggregation vs fallback is demand and copy (scan-heavy floor, allow-list), not a second cache formula.";
  }

  function parseLooseNumber(raw) {
    const t = String(raw == null ? "" : raw).trim();
    if (!t) return null;
    const n = Number(t.replace(/,/g, ""));
    return isFinite(n) ? n : null;
  }

  function countLabel(value, unit) {
    if (value == null || !isFinite(value)) return "";
    const rounded = Math.abs(value - Math.round(value)) < 1e-9
      ? Math.round(value).toLocaleString()
      : String(value);
    return `${rounded} ${unit}`;
  }

  function concurrencySummaryHtml(summary, inputs) {
    if (!summary || summary.in_flight == null) return "";
    const tickets = parseLooseNumber(inputs && inputs.tickets);
    const queueNote = tickets != null && summary.in_flight > tickets
      ? "in-flight scans exceed the ticket pool — arrivals queue; this is not extra ops/s."
      : "Bound concurrency so in-flight work stays near what tickets × (1/L) can admit.";
    return `<div class="summary-metric"><div class="kicker">In-flight demand</div>
      <div class="primary">${esc(countLabel(summary.in_flight, "in-flight scans"))}</div>
      <div class="sub">${esc(countLabel(summary.slots, "slots"))} × fanout ${esc(String(summary.fanout == null ? 1 : summary.fanout))}</div>
      <div class="help">More Celery workers increase in-flight scans and broker occupancy. They do not raise the stall completion ceiling (investigation 004).</div>
      <div class="help">scan_fanout is how many queries one task issues. It is not a COLSCAN latency multiplier. Measure ticket hold time L under your fallback workload and submit an observation.</div>
      <div class="help">${esc(queueNote)}</div>
    </div>`;
  }

  function attachUi() {
    if (typeof document === "undefined") return;
    if (!document.getElementById("corpus")) return;

    const $ = (id) => document.getElementById(id);
    const CORPUS = JSON.parse($("corpus").textContent);
    const MODELS = CORPUS.models;
    const fmt = XY.formatQuantity;
    // -- boot is gated at the bottom of this IIFE --------------------------------
    // Must run after every `let`/`const` below. Calling boot() up here used to
    // hit the TDZ on `currentScenario` and leave the scenario form permanently
    // hidden (radio checked, workspace never opened).

    function corpusMeta() {
      return {
        digest: CORPUS.corpus_digest,
        version: CORPUS.xycalc_version,
        git: CORPUS.xycalc_git,
      };
    }

    function currentTab() {
      return TABS.find((tab) => {
        const el = $("tab-" + tab);
        return el && !el.hidden;
      }) || "scenario";
    }

    let lastSimpleData = null;
    let writingHash = false;
    let hashTimer = null;
    let lastSingleCitation = "";
    let lastScenarioCitation = "";

    function currentMode() {
      for (let i = 0; i < MODES.length; i++) {
        if (document.body.classList.contains("mode-" + MODES[i])) return MODES[i];
      }
      return "basic";
    }

    function permalinkState() {
      const mode = currentMode();
      const state = { mode: mode, inputs: {} };
      if (mode === "basic") {
        const path = $("simple-size-path") && $("simple-size-path").value.trim();
        const vulns = $("simple-vulns") && $("simple-vulns").value.trim();
        const storage = $("simple-db-size") && $("simple-db-size").value.trim();
        const docs = $("simple-doc-count") && $("simple-doc-count").value.trim();
        const docAvg = $("simple-doc-avg") && $("simple-doc-avg").value.trim();
        const docVulns = $("simple-doc-vulns") && $("simple-doc-vulns").checked;
        const devices = $("simple-devices") && $("simple-devices").value.trim();
        const deviceAvg = $("simple-device-avg") && $("simple-device-avg").value.trim();
        const residual = $("simple-residual") && $("simple-residual").value.trim();
        if (vulns && vulns !== BASIC_DEFAULT_VULN_COUNT) state.inputs.vulns = vulns;
        if (path === "docs") state.inputs.path = "docs";
        if (storage) state.inputs.storage = storage;
        if (docs) state.inputs.docs = docs;
        if (docAvg && !simpleDocAvgIsDefault(docAvg, docVulns)) state.inputs.docAvg = docAvg;
        if (docVulns) state.inputs.docVulns = "1";
        if (devices) state.inputs.devices = devices;
        if (deviceAvg) state.inputs.deviceAvg = deviceAvg;
        if (residual) state.inputs.residual = residual;
        if (scientificIsOn()) state.sci = "1";
        return state;
      }
      if (scientificIsOn()) state.sci = "1";
      state.tab = currentTab();
      if (state.tab === "single") {
        state.model = $("model").value;
        document.querySelectorAll("#inputs input").forEach((el) => {
          if (el.value.trim()) state.inputs[el.dataset.key] = el.value.trim();
        });
        const avail = $("available").value.trim();
        if (avail) state.available = avail;
      } else if (state.tab === "scenario" && currentScenario) {
        state.scenario = currentScenario.slug;
        document.querySelectorAll("#scenario-inputs input, #scenario-inputs select").forEach((el) => {
          if (el.value.trim()) state.inputs[el.dataset.key] = el.value.trim();
        });
      }
      return state;
    }

    function writeHash() {
      if (writingHash) return;
      const next = "#" + serializePermalink(permalinkState());
      const href = permalinkHref(next, location);
      const cur = (location.pathname || "") + (location.search || "") + (location.hash || "");
      if (cur === href) return;
      if (location.hash === "" && next === "#") return;
      // Path + search + hash, never a bare "#...". A <base href> on the
      // hosting page would resolve a hash-only replaceState off /calculator/.
      history.replaceState(null, "", href);
    }

    function scheduleHash() {
      clearTimeout(hashTimer);
      hashTimer = setTimeout(writeHash, 120);
    }

    function applyPermalink(parsed) {
      if (!parsed) return false;
      const view = permalinkView(parsed);
      if (!view) return false;
      writingHash = true;
      try {
        setScientific(parsed.sci === "1", { hash: false, paint: false });
        if (view.mode === "basic") {
          setMode("basic", { persist: false, hash: false });
          const storageVal = parsed.inputs.storage || parsed.inputs.size;
          if (parsed.inputs.vulns && $("simple-vulns")) $("simple-vulns").value = parsed.inputs.vulns;
          if (storageVal && $("simple-db-size")) $("simple-db-size").value = storageVal;
          if (parsed.inputs.docs && $("simple-doc-count")) $("simple-doc-count").value = parsed.inputs.docs;
          const wantVulnDocs = parsed.inputs.docVulns === "1" || parsed.inputs.docVulns === "true";
          if ($("simple-doc-vulns")) $("simple-doc-vulns").checked = wantVulnDocs;
          if (parsed.inputs.docAvg && $("simple-doc-avg")) {
            $("simple-doc-avg").value = parsed.inputs.docAvg;
          } else if ($("simple-doc-avg")) {
            $("simple-doc-avg").value = wantVulnDocs
              ? SIMPLE_VULN_DOC_AVG_DEFAULT
              : SIMPLE_DOC_AVG_DEFAULT;
          }
          if (parsed.inputs.devices && $("simple-devices")) $("simple-devices").value = parsed.inputs.devices;
          if (parsed.inputs.deviceAvg && $("simple-device-avg")) $("simple-device-avg").value = parsed.inputs.deviceAvg;
          if (parsed.inputs.residual && $("simple-residual")) $("simple-residual").value = parsed.inputs.residual;
          setSimplePath(parsed.inputs.path === "docs" ? "docs" : "db", { calc: false });
          syncSimpleSizeSlider();
          calculateSimple();
          return true;
        }
        setMode(view.mode, { persist: false, hash: false });
        if (view.tab) setTab(view.tab, { hash: false });
        if (parsed.scenario) {
          pickScenario(parsed.scenario);
          for (const key of Object.keys(parsed.inputs)) {
            const el = $("scn-in-" + key);
            if (el) el.value = parsed.inputs[key];
          }
          syncScenarioSegmentedControls();
          maybeAuto();
        } else if (!parsed.model) {
          const scenarios = CORPUS.scenarios || [];
          const def = scenarios.find((s) => s.default && !s.disabled) || scenarios.find((s) => !s.disabled);
          if (def) pickScenario(def.slug);
        }
        if (parsed.model) {
          pickQuestion(parsed.model, { hash: false, collapse: true });
          for (const key of Object.keys(parsed.inputs)) {
            const el = $("in-" + key);
            if (el) el.value = parsed.inputs[key];
          }
          if (parsed.available) $("available").value = parsed.available;
          calculate({ quiet: true });
        }
        return true;
      } finally {
        writingHash = false;
        writeHash();
      }
    }

    function copyCitation(text) {
      const btn = document.activeElement;
      const target = btn && btn.getAttribute && btn.getAttribute("data-copy-cite") ? btn : $("copy-cite");
      const done = () => {
        if (!target) return;
        const prev = target.textContent;
        target.textContent = "Copied";
        setTimeout(() => { target.textContent = prev; }, 1600);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done).catch(() => done());
      } else {
        done();
      }
    }

    function boot() {
      $("provenance").textContent =
        CORPUS.models.length + " models · corpus " + CORPUS.corpus_digest +
        " · exported by xycalc " + CORPUS.xycalc_version +
        " · " + CORPUS.xycalc_git;
      $("model").innerHTML = MODELS.map((m) =>
        `<option value="${esc(m.slug)}">${esc(m.question)}${esc(gradeSuffix(m.validation && m.validation.grade))}</option>`
      ).join("");
      bootQuestionPicker();
      renderInputs();
      $("model").addEventListener("change", () => {
        syncQuestionPicker($("model").value);
        renderInputs();
        scheduleSingleCalc();
        scheduleHash();
      });
      $("go").addEventListener("click", calculate);
      $("sweep").addEventListener("change", drawChart);
      $("ylin").addEventListener("click", () => setScale("linear"));
      $("ylog").addEventListener("click", () => setScale("log"));
      $("available").addEventListener("input", () => { scheduleSingleCalc(); scheduleHash(); });
      $("inputs").addEventListener("input", () => { scheduleSingleCalc(); scheduleHash(); });
      document.querySelectorAll(".tab").forEach((btn) =>
        btn.addEventListener("click", () => setTab(btn.dataset.tab)));
      document.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && e.target.tagName === "INPUT") {
          if (currentMode() === "basic") calculateSimple();
          else if (!$("tab-single").hidden) calculate();
          else if (!$("tab-scenario").hidden) calculateScenario(false);
        }
      });
      document.addEventListener("click", (e) => {
        const el = e.target && e.target.nodeType === 1 ? e.target : e.target && e.target.parentElement;
        const btn = el && el.closest && el.closest("[data-copy-cite]");
        if (!btn) return;
        e.preventDefault();
        const which = btn.getAttribute("data-copy-cite");
        const text = which === "scenario" ? lastScenarioCitation : lastSingleCitation;
        if (text) copyCitation(text);
      });
      window.addEventListener("hashchange", () => {
        if (writingHash) return;
        applyPermalink(parsePermalink(location.hash));
      });
      bootMode();
      bootSimple();
      bootScenario();
      bootFlow();
      bootOccupancy();
      bootCacheCliff();
      const restored = applyPermalink(permalinkFromLocation(location));
      if (!restored) {
        const scenarios = CORPUS.scenarios || [];
        const def = scenarios.find((s) => s.default && !s.disabled) || scenarios.find((s) => !s.disabled);
        if (def) pickScenario(def.slug);
        scheduleSingleCalc();
      }
    }

    const MODE_KEY = "xycalc.calcMode";

    function bootMode() {
      let mode = "basic";
      try {
        const saved = localStorage.getItem(MODE_KEY);
        if (saved) mode = canonicalMode(saved);
      } catch (_) { /* private mode / blocked storage */ }
      setMode(mode, { persist: false, hash: false });
      $("mode-basic").addEventListener("click", () => setMode("basic"));
      $("mode-advanced").addEventListener("click", () => setMode("advanced"));
      $("mode-questions").addEventListener("click", () => setMode("questions"));
      $("mode-data").addEventListener("click", () => setMode("data"));
      const sciBtn = $("sci-toggle");
      if (sciBtn) sciBtn.addEventListener("click", () => setScientific(!scientificIsOn()));
      alignViewSubnav();
      window.addEventListener("resize", alignViewSubnav);
      if (document.fonts && document.fonts.ready) {
        document.fonts.ready.then(alignViewSubnav).catch(() => {});
      }
    }

    function alignViewSubnav() {
      const bar = document.querySelector(".mode-bar");
      const basic = $("mode-basic");
      const sub = document.querySelector(".view-subnav");
      if (!bar || !basic || !sub) return;
      const mid = basic.getBoundingClientRect().left + basic.getBoundingClientRect().width / 2;
      const indent = mid - bar.getBoundingClientRect().left;
      if (!(indent > 0)) return;
      sub.style.setProperty("--subnav-indent", indent.toFixed(1) + "px");
    }

    function scientificIsOn() {
      return document.body.classList.contains("sci-on");
    }

    function setScientific(on, opts) {
      const next = !!on;
      document.body.classList.toggle("sci-on", next);
      const btn = $("sci-toggle");
      if (btn) btn.setAttribute("aria-pressed", next ? "true" : "false");
      const simplePanel = $("simple-sci-panel");
      const scenarioPanel = $("scenario-sci-panel");
      if (simplePanel) simplePanel.hidden = !next;
      if (scenarioPanel) scenarioPanel.hidden = !next;
      if (next) maybeRenderScientificMath();
      if ((!opts || opts.paint !== false) && lastSimpleData && currentMode() === "basic") {
        renderSimpleResult(lastSimpleData);
      }
      if (!opts || opts.hash !== false) scheduleHash();
    }

    function setMode(mode, opts) {
      const persist = !opts || opts.persist !== false;
      const next = canonicalMode(mode);
      document.body.classList.remove("mode-basic", "mode-simple", "mode-scientific", "mode-advanced", "mode-questions", "mode-data");
      document.body.classList.add("mode-" + next);
      $("mode-basic").setAttribute("aria-pressed", next === "basic" ? "true" : "false");
      $("mode-advanced").setAttribute("aria-pressed", next === "advanced" ? "true" : "false");
      $("mode-questions").setAttribute("aria-pressed", next === "questions" ? "true" : "false");
      $("mode-data").setAttribute("aria-pressed", next === "data" ? "true" : "false");
      const subnav = document.querySelector(".view-subnav");
      if (subnav) subnav.setAttribute("aria-hidden", "false");
      const allowed = MODE_TABS[next] || [];
      if (allowed.length) {
        const cur = currentTab();
        if (allowed.indexOf(cur) < 0) setTab(allowed[0], { hash: false });
      }
      if (persist) {
        try { localStorage.setItem(MODE_KEY, next); }
        catch (_) { /* ignore */ }
      }
      if (next === "basic") scheduleSimpleCalc();
      if (next === "data" || scientificIsOn()) maybeRenderScientificMath();
      if (next === "questions" && STATE) drawChart();
      if (!opts || opts.hash !== false) scheduleHash();
    }

    function gbFromSimpleSizeText(raw) {
      const t = String(raw || "").trim();
      if (!t) return null;
      try {
        const bytes = XY.parseBytes(normalizeSimpleSize(t));
        if (!(bytes > 0)) return null;
        return bytes / 1e9;
      } catch (e) {
        return null;
      }
    }

    function syncSimpleSizeSlider() {
      const slider = $("simple-size-slider");
      const field = $("simple-db-size");
      if (!slider || !field) return;
      const gb = gbFromSimpleSizeText(field.value);
      if (gb == null) return;
      slider.value = String(simpleSizeSliderIndex(gb));
    }

    let simpleDocAvgEdited = false;

    function setSimplePath(path, opts) {
      const docs = path === "docs";
      const field = $("simple-size-path");
      if (field) field.value = docs ? "docs" : "db";
      const dbBtn = $("simple-path-db");
      const docsBtn = $("simple-path-docs");
      if (dbBtn) dbBtn.setAttribute("aria-pressed", docs ? "false" : "true");
      if (docsBtn) docsBtn.setAttribute("aria-pressed", docs ? "true" : "false");
      const slotDb = $("simple-slot-db");
      const slotDocs = $("simple-slot-docs");
      if (slotDb) slotDb.hidden = docs;
      if (slotDocs) slotDocs.hidden = !docs;
      if (!opts || opts.calc !== false) scheduleSimpleCalc();
    }

    function bootSimple() {
      for (const id of SIMPLE_FORM_FIELD_IDS) {
        const el = $(id);
        if (!el) throw new Error("Simple form missing #" + id);
        el.addEventListener("input", scheduleSimpleCalc);
      }
      const sizeField = $("simple-db-size");
      if (sizeField) sizeField.addEventListener("input", syncSimpleSizeSlider);
      const slider = $("simple-size-slider");
      if (!slider) throw new Error("Simple form missing #simple-size-slider");
      slider.addEventListener("input", () => {
        const gb = simpleSizeSliderGb(slider.value);
        $("simple-db-size").value = formatSimpleSizeSliderGb(gb);
        scheduleSimpleCalc();
      });
      syncSimpleSizeSlider();
      $("simple-path-db").addEventListener("click", () => setSimplePath("db"));
      $("simple-path-docs").addEventListener("click", () => setSimplePath("docs"));
      $("simple-doc-avg").addEventListener("input", () => { simpleDocAvgEdited = true; });
      $("simple-doc-vulns").addEventListener("change", () => {
        if (!simpleDocAvgEdited) {
          $("simple-doc-avg").value = $("simple-doc-vulns").checked
            ? SIMPLE_VULN_DOC_AVG_DEFAULT
            : SIMPLE_DOC_AVG_DEFAULT;
        }
        scheduleSimpleCalc();
      });
      $("simple-rail").addEventListener("click", (ev) => {
        if (ev.target && ev.target.id === "simple-open-scientific") {
          ev.preventDefault();
          setScientific(true);
          const math = $("simple-sci-math") || $("scientific-math");
          if (math) math.scrollIntoView({ behavior: "smooth", block: "start" });
        }
      });
      paintSimpleNvdChart();
      scheduleSimpleCalc();
      const aside = $("simple-aside");
      if (aside) aside.innerHTML = basicAsideHtml();
    }

    let pendingExpandMathUntil = 0;

    function expandScenarioMath() {
      const details = $("scenario-cascade") && $("scenario-cascade").querySelector("details");
      if (!details) return false;
      details.open = true;
      details.setAttribute("open", "");
      return true;
    }

    function openAdvancedFromSimple() {
      const vulns = ($("simple-vulns").value || "").trim();
      const path = ($("simple-size-path").value || "db").trim();
      const storageRaw = ($("simple-db-size").value || "").trim();
      const docs = ($("simple-doc-count").value || "").trim();
      const docAvg = ($("simple-doc-avg").value || "").trim();
      const devices = ($("simple-devices").value || "").trim();
      const deviceAvgRaw = ($("simple-device-avg").value || "").trim();
      const residualRaw = ($("simple-residual").value || "").trim();
      const derived = simpleChainInputs({
        path: path,
        vulns: vulns,
        storage: storageRaw,
        docs: docs,
        docAvg: docAvg,
        devices: devices,
        deviceAvg: deviceAvgRaw,
        residual: residualRaw,
      });
      pendingExpandMathUntil = Date.now() + 600;
      setMode("advanced");
      setTab("scenario");
      pickScenario("mongodb.size-to-instance");
      const storageForAdv = derived
        ? derived.baseline_storage_size
        : (storageRaw ? normalizeSimpleSize(storageRaw) : "");
      if (storageForAdv) {
        const el = $("scn-in-baseline_storage_size");
        if (el) el.value = storageForAdv;
      }
      if (vulns) {
        const b = $("scn-in-baseline_vuln_count");
        if (b) b.value = vulns.replace(/,/g, "");
        const t = $("scn-in-target_vuln_count");
        if (t) t.value = vulns.replace(/,/g, "");
      }
      if (devices) {
        const d = $("scn-in-device_count");
        if (d) d.value = devices.replace(/,/g, "");
      }
      if (deviceAvgRaw) {
        const a = $("scn-in-device_avg_storage_bytes");
        if (a) a.value = normalizeSimpleAvgBytes(deviceAvgRaw);
      }
      if (residualRaw) {
        const r = $("scn-in-residual_storage_size");
        if (r) r.value = normalizeSimpleSize(residualRaw);
      }
      calculateScenario(true, { expandMath: true });
      expandScenarioMath();
      setTimeout(expandScenarioMath, 0);
      setTimeout(expandScenarioMath, 160);
      setTimeout(expandScenarioMath, 400);
      $("scenario-cascade").scrollIntoView({ behavior: "smooth", block: "start" });
    }

    let simpleCalcRaf = null;
    function scheduleSimpleCalc() {
      if (simpleCalcRaf != null) return;
      simpleCalcRaf = requestAnimationFrame(() => {
        simpleCalcRaf = null;
        calculateSimple();
      });
    }

    function calculateSimple() {
      const vulns = ($("simple-vulns").value || "").trim();
      const path = ($("simple-size-path").value || "db").trim();
      const storageRaw = ($("simple-db-size").value || "").trim();
      const docs = ($("simple-doc-count").value || "").trim();
      const docAvg = ($("simple-doc-avg").value || "").trim();
      const devices = ($("simple-devices").value || "").trim();
      const deviceAvgRaw = ($("simple-device-avg").value || "").trim();
      const residualRaw = ($("simple-residual").value || "").trim();
      const err = $("simple-error");
      const status = $("simple-status");
      if (path === "docs") {
        if (!docs || !docAvg) {
          $("simple-result").hidden = true;
          err.hidden = true;
          status.textContent = "Enter a record count — sizing updates as you type.";
          return;
        }
      } else if (!storageRaw) {
        $("simple-result").hidden = true;
        err.hidden = true;
        status.textContent = "Enter DB size — sizing updates as you type.";
        return;
      }
      try {
        const inputs = simpleChainInputs({
          path: path,
          vulns: vulns,
          storage: storageRaw,
          docs: docs,
          docAvg: docAvg,
          devices: devices,
          deviceAvg: deviceAvgRaw,
          residual: residualRaw,
        });
        if (!inputs) {
          $("simple-result").hidden = true;
          err.hidden = true;
          status.textContent = path === "docs"
            ? "Enter a record count — sizing updates as you type."
            : "Enter DB size — sizing updates as you type.";
          return;
        }
        const data = applySimpleHostFloor(
          XY.chainEvaluate(CORPUS, "mongodb.size-to-instance", inputs));
        err.hidden = true;
        renderSimpleResult(data);
        const storage = inputs.baseline_storage_size;
        const residual = inputs.residual_storage_size;
        const notes = [];
        if (path === "docs") notes.push(`${docs} × ${docAvg} → ${storage}`);
        else if (storage !== storageRaw) notes.push(`storage as ${storage}`);
        if (residual && residual !== residualRaw) notes.push(`residual as ${residual}`);
        const as = notes.length ? ` (${notes.join("; ")})` : "";
        status.textContent = "Up to date" + as + " — change a field to recalculate.";
        syncSimpleSizeSlider();
        const note = document.getElementById("simple-vuln-note");
        if (note) {
          note.hidden = false;
          note.textContent = "Sizing today's footprint — no NVD growth (target count equals today).";
        }
        if (currentMode() === "basic" || currentMode() === "scientific") scheduleHash();
      } catch (e) {
        $("simple-result").hidden = true;
        err.hidden = false;
        err.textContent = e.message;
        status.textContent = "Fix the input to size.";
      }
    }

    // Smallest box this product runs: r8i.2xlarge = 64 GiB. Floor the Simple
    // answer so a tiny DB does not advertise a sub-floor host.
    const SIMPLE_HOST_FLOOR_BYTES = 64 * 1024 ** 3;

    function applySimpleHostFloor(data) {
      const s = data.sizing_summary || {};
      if (!s.ram) return data;
      const floor = SIMPLE_HOST_FLOOR_BYTES;
      const clamp = (n) => (n < floor ? floor : n);
      s.ram = {
        lo: clamp(s.ram.lo),
        mode: clamp(s.ram.mode),
        hi: clamp(s.ram.hi),
        unit: s.ram.unit,
      };
      // Re-pick against the floored band from the whole aws-ec2 catalog
      // (r8i, then cited U7i). An r8i-only filter made the homepage 500 GB
      // example report custom sizing on every card.
      const catalogs = CORPUS.instance_catalogs || {};
      const catalog = catalogs["aws-ec2"] || CORPUS.instance_catalog || [];
      const azureCatalog = catalogs["azure-vm"] || [];
      const ceiling = CORPUS.default_instance_ceiling_bytes;
      const band = { lo: s.ram.lo, mode: s.ram.mode, hi: s.ram.hi };
      const pick = XY.selectInstance
        ? XY.selectInstance(band, catalog, null, ceiling === 0 ? null : ceiling)
        : null;
      if (pick && s.cpu) {
        s.cpu.instance_lo = pick.pick_lo && pick.pick_lo.name;
        s.cpu.instance_mode = pick.pick_mode && pick.pick_mode.name;
        s.cpu.instance_hi = pick.pick_hi && pick.pick_hi.name;
        s.cpu.lo = pick.pick_lo && pick.pick_lo.vcpu;
        s.cpu.mode = pick.pick_mode && pick.pick_mode.vcpu;
        s.cpu.hi = pick.pick_hi && pick.pick_hi.vcpu;
      } else if (s.cpu) {
        // Fallback if selectInstance is not exported: at least name the floor SKU.
        if (s.ram.lo <= floor) s.cpu.instance_lo = s.cpu.instance_lo || "r8i.2xlarge";
        if (s.ram.mode <= floor) s.cpu.instance_mode = "r8i.2xlarge";
        if (s.ram.hi <= floor) s.cpu.instance_hi = s.cpu.instance_hi || "r8i.2xlarge";
      }
      const r6iPick = XY.selectInstance
        ? XY.selectInstance(band, catalog, "r6i", ceiling === 0 ? null : ceiling)
        : null;
      if (r6iPick) {
        s.r6i = {
          lo: r6iPick.pick_lo && r6iPick.pick_lo.name,
          mode: r6iPick.pick_mode && r6iPick.pick_mode.name,
          hi: r6iPick.pick_hi && r6iPick.pick_hi.name,
          exceeds_pool: r6iPick.exceeds_pool,
          largest: r6iPick.largest_in_pool && r6iPick.largest_in_pool.name,
        };
      }
      const azurePick = XY.selectInstance && azureCatalog.length
        ? XY.selectInstance(band, azureCatalog, null, ceiling === 0 ? null : ceiling)
        : null;
      if (azurePick) {
        s.azure = {
          lo: azurePick.pick_lo && azurePick.pick_lo.name,
          mode: azurePick.pick_mode && azurePick.pick_mode.name,
          hi: azurePick.pick_hi && azurePick.pick_hi.name,
          exceeds_pool: azurePick.exceeds_pool,
        };
      }
      data.sizing_summary = s;
      data.simple_instance_pick = pick || null;
      return data;
    }

    function renderSimpleResult(data) {
      const s = data.sizing_summary || {};
      const ram = s.ram;
      const paint = simpleFirstPaintHtml(
        data,
        fmt,
        document.body.classList.contains("sci-on") ? "scientific" : "basic",
      );
      $("simple-result").hidden = false;
      if (ram && paint.ramText) {
        $("simple-ram").textContent = paint.ramText;
        $("simple-ram-band").textContent =
          "band " + fmt(ram.lo, ram.unit) + " – " + fmt(ram.hi, ram.unit);
        if (ram.hi > ram.lo) {
          $("simple-bandbar").hidden = false;
          $("simple-bandends").hidden = false;
          const span = ram.hi - ram.lo;
          const modePct = ((ram.mode - ram.lo) / span) * 100;
          $("simple-bandfill").style.left = "0%";
          $("simple-bandfill").style.width = "100%";
          $("simple-bandmode").style.left = modePct + "%";
          $("simple-bandends").innerHTML =
            `<span>${esc(fmt(ram.lo, ram.unit))}</span><span>${esc(fmt(ram.hi, ram.unit))}</span>`;
        } else {
          $("simple-bandbar").hidden = true;
          $("simple-bandends").hidden = true;
        }
      } else {
        $("simple-ram").textContent = "—";
        $("simple-ram-band").textContent = "";
        $("simple-bandbar").hidden = true;
        $("simple-bandends").hidden = true;
      }

      $("simple-picks").innerHTML = paint.picksHtml;
      const family = $("simple-family");
      if (family) family.innerHTML = paint.r6iHtml || "";
      const slot = $("simple-honesty-slot");
      if (slot) slot.innerHTML = paint.honestyHtml + paint.bannerHtml;
      const val = $("simple-validation");
      if (val) val.hidden = true;
      paintSimpleNvdChart();
      lastSimpleData = data;
      maybeRenderScientificMath();
    }

    function scientificMathIsVisible() {
      if (scientificIsOn()) return true;
      if (currentMode() !== "data") return false;
      const panel = $("tab-math");
      return !!(panel && !panel.hidden);
    }

    function maybeRenderScientificMath() {
      if (!scientificMathIsVisible()) return;
      renderScientificMath(lastSimpleData);
    }

    function setTab(name, opts) {
      TABS.forEach((tab) => {
        const panel = $("tab-" + tab);
        const btn = $("tab-btn-" + tab);
        if (panel) panel.hidden = name !== tab;
        if (btn) btn.classList.toggle("active", name === tab);
      });
      if (name === "math") maybeRenderScientificMath();
      if (name === "single" && STATE) drawChart();
      if (!opts || opts.hash !== false) scheduleHash();
    }

    const SCENARIO_DEFAULTS = {
      "mongodb.size-to-instance": {
        baseline_vuln_count: "250000",
        baseline_storage_size: "100GB",
        target_vuln_count: "280000",
        index_size: "40GB",
        foreign_collections_size: "80GB",
      },
      "storage.ebs-vs-nvme-at-io-size": {
        io_size_kib: "64",
        provisioned_iops: "3000",
      },
    };
    let currentScenario = null;
    let scenarioCalcDirty = false; // eslint-disable-line no-unused-vars -- written on input; unread until a later packet

    function scenarioGrade(s) {
      const vals = [];
      for (const st of s.steps || []) {
        if ((st.kind || "model") !== "model" || !st.model) continue;
        const m = MODELS.find((x) => x.slug === st.model);
        if (m && m.validation) vals.push(m.validation);
      }
      return weakestValidation(vals);
    }

    function renderScenarioOption(s) {
      const weakest = !s.disabled ? scenarioGrade(s) : null;
      const pill = weakest ? `<span class="grade-pill">${esc(gradeSuffix(weakest.grade))}</span>` : "";
      if (s.disabled) {
        return `<div class="scenario-opt stub disabled" data-slug="${esc(s.slug)}" role="note">
          <span class="stub-badge">Not modeled</span>
          <span>
            <div class="label">${esc(s.label)}</div>
            <div class="sub">${esc(s.note || "")}</div>
          </span>
        </div>`;
      }
      return `<label class="scenario-opt" data-slug="${esc(s.slug)}">
          <input type="radio" name="scenario" value="${esc(s.slug)}">
          <span>
            <div class="label">${esc(s.label)}${s.default ? " <span class='help' style='display:inline'>(default)</span>" : ""}${pill}</div>
            <div class="sub">${esc(s.summary || "")}</div>
          </span>
        </label>`;
    }

    function setScenarioChooserCollapsed(collapsed) {
      const chooser = $("scenario-chooser");
      const btn = $("scenario-change");
      if (chooser) chooser.classList.toggle("collapsed", !!collapsed);
      if (btn) btn.textContent = collapsed ? "Change scenario" : "Hide list";
    }

    function fillScenarioCompact(s) {
      const title = $("scenario-compact-title");
      const sub = $("scenario-compact-sub");
      const compact = $("scenario-compact");
      if (!title || !sub || !compact || !s) return;
      const weakest = scenarioGrade(s);
      const pill = weakest ? ` <span class="grade-pill">${esc(gradeSuffix(weakest.grade))}</span>` : "";
      title.innerHTML = esc(s.label) + pill;
      sub.textContent = s.summary || "";
      compact.hidden = false;
    }

    function showStubNotice(slug) {
      const s = (CORPUS.scenarios || []).find((x) => x.slug === slug);
      if (!s?.disabled) return;
      document.querySelectorAll(".scenario-opt").forEach((el) => {
        el.classList.toggle("selected", el.dataset.slug === slug && el.classList.contains("stub"));
      });
      document.querySelectorAll(".scenario-opt input[type=radio]").forEach((el) => { el.checked = false; });
      $("scenario-workspace").hidden = true;
      const compact = $("scenario-compact");
      if (compact) compact.hidden = true;
      setScenarioChooserCollapsed(false);
      $("scenario-stub-notice").innerHTML = `
        <h3>${esc(s.label)} — not modeled yet</h3>
        <p>${esc(s.note || "No coefficients in the corpus yet.")} Pick a scenario above that is not marked <strong>Not modeled</strong>.</p>`;
      $("scenario-stub-notice").hidden = false;
    }

    function scenarioRadio(slug) {
      return document.querySelector(
        `.scenario-opt input[type=radio][value="${CSS.escape(slug)}"]`
      );
    }

    function wireScenarioPicker() {
      document.querySelectorAll(".scenario-opt:not(.stub):not(.disabled)").forEach((el) => {
        el.addEventListener("click", () => {
          const slug = el.dataset.slug;
          if (!slug) return;
          const radio = scenarioRadio(slug);
          if (radio) radio.checked = true;
          selectScenario(slug);
        });
      });
      document.querySelectorAll(".scenario-opt.stub").forEach((el) => {
        el.addEventListener("click", () => showStubNotice(el.dataset.slug));
      });
    }

    function renderScenarioGroup(g) {
      const cards = (g.items || []).map(renderScenarioOption).join("");
      const nested = (g.subs || []).map((sub) => `
        <h4 class="scenario-sub-title">${esc(sub.label)}</h4>
        <div class="scenario-list">${sub.items.map(renderScenarioOption).join("")}</div>`).join("");
      const body = `${cards ? `<div class="scenario-list">${cards}</div>` : ""}${nested}`;
      if (g.core) {
        return `<section class="scenario-kind" data-kind="${esc(g.id)}">
          <h3 class="scenario-kind-title">${esc(g.label)}</h3>
          ${body}
        </section>`;
      }
      return `<details class="scenario-kind-drawer" data-kind="${esc(g.id)}">
        <summary class="scenario-kind-title">${esc(g.label)}</summary>
        ${body}
      </details>`;
    }

    function renderScenarioCatalog(scenarios) {
      return groupScenarios(scenarios).map((band) => `
        <section class="scenario-band" data-band="${esc(band.id)}">
          <h2 class="scenario-band-title">${esc(band.label)}</h2>
          ${band.groups.map(renderScenarioGroup).join("")}
        </section>`).join("");
    }

    function bootScenario() {
      const scenarios = CORPUS.scenarios || [];
      $("scenario-picker").innerHTML = renderScenarioCatalog(scenarios);
      wireScenarioPicker();
      $("scn-recalc").addEventListener("click", () => calculateScenario(false));
      const change = $("scenario-change");
      if (change) {
        change.addEventListener("click", () => {
          const chooser = $("scenario-chooser");
          setScenarioChooserCollapsed(!(chooser && chooser.classList.contains("collapsed")));
        });
      }
    }

    function pickScenario(slug) {
      const radio = scenarioRadio(slug);
      if (radio) radio.checked = true;
      selectScenario(slug);
    }

    function paintSimpleNvdChart() {
      const slot = $("simple-nvd-chart");
      if (!slot || slot.dataset.ready === "1") return;
      slot.innerHTML = renderNvdChart(corpusNvdChart(), { fold: false, projectNext: true });
      slot.dataset.ready = "1";
    }

    function corpusNvdChart() {
      const scenarios = CORPUS.scenarios || [];
      const inst = scenarios.find((s) => s.slug === "mongodb.size-to-instance");
      return inst && inst.nvd_chart;
    }

    function renderNvdChart(chart, opts) {
      if (!chart) return "";
      const years = chart.annual;
      if (!years || !years.length) return "";
      const fold = !opts || opts.fold !== false;
      const proj = opts && opts.projectNext ? nvdNextYearProjection({ annual: years, growth_pct: chart.growth_pct }) : null;
      const ticks = proj ? years.concat([{ year: proj.year }]) : years;
      const W = 420, H = 168, L = 52, R = 14, T = 18, B = 40;
      const iw = W - L - R, ih = H - T - B;
      const nvdMax = Math.max(...years.map((a) => a.count), proj ? proj.hi : 0);
      const yMax = nvdMax * 1.12;
      const xAt = (i) => L + (ticks.length === 1 ? iw / 2 : (i / (ticks.length - 1)) * iw);
      const yAt = (v) => T + ih * (1 - v / yMax);
      const nvdPath = years.map((a, i) => `${i ? "L" : "M"}${esc(xAt(i).toFixed(1))} ${esc(yAt(a.count).toFixed(1))}`).join(" ");
      const dots = years.map((a, i) => `<circle class="nvd-dot" cx="${esc(xAt(i).toFixed(1))}" cy="${esc(yAt(a.count).toFixed(1))}" r="3.5"></circle>`).join("");
      const ms = years.map((a, i) => a.microsoft != null ? `<circle class="ms-dot" cx="${esc(xAt(i).toFixed(1))}" cy="${esc(yAt(a.microsoft).toFixed(1))}" r="4"></circle>` : "").join("");
      const xLabels = ticks.map((a, i) => `<text x="${esc(xAt(i).toFixed(1))}" y="${H - 18}" text-anchor="middle">${esc(a.year)}</text>`).join("");
      const g = chart.growth_pct;
      const sourceUrl = safeExternalUrl(chart.source_url);
      const src = sourceUrl ? `<a href="${esc(sourceUrl)}" target="_blank" rel="noopener">${esc(chart.source)}</a>` : esc(chart.source);
      let projSvg = "";
      let projNote = "";
      if (proj && g) {
        const i0 = years.length - 1;
        const i1 = ticks.length - 1;
        const fmtN = (n) => Math.round(n).toLocaleString("en-US");
        projSvg =
          `<line class="nvd-proj-band" x1="${esc(xAt(i1).toFixed(1))}" x2="${esc(xAt(i1).toFixed(1))}" y1="${esc(yAt(proj.hi).toFixed(1))}" y2="${esc(yAt(proj.lo).toFixed(1))}"></line>` +
          `<path class="nvd-proj" d="M${esc(xAt(i0).toFixed(1))} ${esc(yAt(years[i0].count).toFixed(1))} L${esc(xAt(i1).toFixed(1))} ${esc(yAt(proj.mode).toFixed(1))}"></path>` +
          `<circle class="nvd-proj-dot" cx="${esc(xAt(i1).toFixed(1))}" cy="${esc(yAt(proj.mode).toFixed(1))}" r="3.5"></circle>`;
        projNote = ` ${proj.year} dashed = cited YoY band ${esc(g.lo)}–${esc(g.mode)}–${esc(g.hi)}% on ${esc(years[i0].year)}'s count (${fmtN(proj.lo)}–${fmtN(proj.mode)}–${fmtN(proj.hi)}) — not an NVD forecast. This page still sizes today's record count.`;
      }
      const svg = `<svg class="nvd-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="CVEs published per year">
            <g class="axis">${xLabels}<text class="axis-title" x="12" y="${T + ih / 2}" text-anchor="middle" transform="rotate(-90 12 ${T + ih / 2})">CVEs published per year</text></g>
            <path class="nvd-line" d="${nvdPath}"></path>${dots}${ms}${projSvg}
          </svg>`;
      const meta = `<p class="nvd-meta">Cumulative through 2025: <strong>${esc((chart.cumulative_2025 || 0).toLocaleString())}</strong>.
            YoY band: <strong>${esc(g.lo)}–${esc(g.mode)}–${esc(g.hi)}%</strong>. Source: ${src}.
            ${chart.microsoft_note ? esc(chart.microsoft_note) : ""}${projNote}</p>`;
      const body = `<div class="nvd-layout">${svg}${meta}</div>`;
      if (!fold) {
        return `<div class="nvd-fold"><div class="nvd-title">CVE publication (cited)</div>${body}</div>`;
      }
      return `<details class="nvd-fold">
        <summary>CVE publication growth</summary>
        ${body}
      </details>`;
    }

    function scenarioInputCount(s) {
      return scenarioInputList(s).length;
    }

    function renderCitationSummary(data) {
      const modelSteps = (data.steps || []).filter((st) => st.kind === "model" && st.answer);
      if (!modelSteps.length) return "";
      const primary = modelSteps[0];
      const floor = (primary.steps || []).find((t) => t.role === "floor" && !t.skipped);
      const kicker = floor ? floor.label : (primary.model || "Result");
      const constraints = (primary.constraints || []).slice(0, 4).map((c) => `
          <div class="summary-metric"><div class="kicker">${esc(c.label)}</div>
          <div class="primary">${esc(fmt(c.value, c.unit))}</div>
          ${c.rationale ? `<div class="help">${esc(c.rationale)}</div>` : ""}</div>`).join("");
      return `<div class="panel sizing-summary"><h2>What the measurement says</h2>
        <p class="help">No inputs — this scenario cites a fixed corpus finding. A formula with your offered rate is not modeled yet.</p>
        <div class="summary-grid">
          <div class="summary-metric"><div class="kicker">${esc(kicker)}</div>
            <div class="primary">${esc(fmt(primary.answer.mode, primary.unit))}</div>
            <div class="help">band ${esc(fmt(primary.answer.lo, primary.unit))} – ${esc(fmt(primary.answer.hi, primary.unit))}</div>
          </div>${constraints}
        </div></div>`;
    }

    function noteScenarioFieldChange() {
      scenarioCalcDirty = true;
      maybeAuto();
      scheduleHash();
    }

    function syncScenarioSegmentedControls() {
      document.querySelectorAll("#scenario-inputs .scenario-segmented").forEach((group) => {
        const hidden = group.querySelector('input[type="hidden"]');
        if (!hidden) return;
        group.querySelectorAll("button[data-value]").forEach((btn) => {
          const active = btn.dataset.value === hidden.value;
          btn.classList.toggle("active", active);
          btn.setAttribute("aria-pressed", active ? "true" : "false");
        });
      });
    }

    function wireScenarioFormInputs() {
      document.querySelectorAll("#scenario-inputs input, #scenario-inputs select").forEach((el) => {
        if (el.type === "hidden") return;
        el.addEventListener("input", noteScenarioFieldChange);
        el.addEventListener("change", noteScenarioFieldChange);
      });
      syncScenarioSegmentedControls();
      document.querySelectorAll("#scenario-inputs .scenario-segmented").forEach((group) => {
        const hidden = group.querySelector('input[type="hidden"]');
        if (!hidden) return;
        group.querySelectorAll("button[data-value]").forEach((btn) => {
          btn.addEventListener("click", () => {
            hidden.value = btn.dataset.value;
            syncScenarioSegmentedControls();
            noteScenarioFieldChange();
          });
        });
      });
    }

    function renderQueryRegimeCard(inputs) {
      if (scenarioInputList(currentScenario).map((i) => i.key).indexOf("query_regime") < 0) {
        return "";
      }
      const regime = scenarioChoiceValue("query_regime", inputs && inputs.query_regime);
      const reason = scenarioChoiceValue("fallback_reason", inputs && inputs.fallback_reason);
      const reasonLabel = reason === "allowlist" ? "allow-list" : reason;
      const help = reason === "allowlist"
        ? "Allow-list misses never use v1/v2 aggregation; size fallback for that traffic."
        : regime === "fallback"
          ? "Fallback is the planning default for this path."
          : "Aggregation is the active path for query types that can stay on the pipeline.";
      return `<div class="summary-metric"><div class="kicker">Query regime</div>
        <div class="primary">${esc(regime)}</div>
        <div class="sub">reason ${esc(reasonLabel)}</div>
        <div class="help">${esc(help)}</div>
      </div>`;
    }

    function selectScenario(slug) {
      const s = (CORPUS.scenarios || []).find((x) => x.slug === slug);
      if (!s || s.disabled) return;
      currentScenario = s;
      $("scenario-stub-notice").hidden = true;
      document.querySelectorAll(".scenario-opt").forEach((el) =>
        el.classList.toggle("selected", el.dataset.slug === slug && !el.classList.contains("stub")));
      fillScenarioCompact(s);
      setScenarioChooserCollapsed(true);
      $("scenario-workspace").hidden = false;
      $("scenario-summary").innerHTML = "";
      $("scenario-cascade").innerHTML = "";
      $("scn-error").hidden = true;

      try {
        const hasInputs = scenarioInputCount(currentScenario) > 0;
        $("scenario-form-panel").hidden = !hasInputs;
        $("scenario-nvd-chart").innerHTML = hasInputs ? renderNvdChart(currentScenario.nvd_chart, { projectNext: true }) : "";
        if (!hasInputs) {
          $("scenario-inputs").innerHTML = "";
          maybeAuto();
          return;
        }
        const defaults = SCENARIO_DEFAULTS[slug] || {};
        const fields = (currentScenario.input_sections || []).map((sec) => {
          const inner = `${scenarioSectionCopyHtml(sec.title)}
          <div class="input-grid">${(sec.inputs || []).map((i) =>
            scenarioInputControlHtml(i, defaults[i.key])
          ).join("")}
          </div>`;
          if (scenarioSectionIsDrawer(sec)) {
            return `<details class="input-section-drawer"><summary>${esc(sec.title)}</summary>
          ${inner}</details>`;
          }
          return `<div class="input-section"><h3>${esc(sec.title)}</h3>
          ${inner}</div>`;
        }).join("")
          || `<div class="input-grid">${(currentScenario.inputs || []).map((i) =>
            scenarioInputControlHtml(i, defaults[i.key])
          ).join("")}</div>`;
        $("scenario-inputs").innerHTML = fields;
        for (const key of Object.keys(defaults)) {
          const el = $(`scn-in-${key}`);
          if (el && !el.value) el.value = defaults[key];
        }
        wireScenarioFormInputs();
        $("scn-recalc-status").textContent = "Fill required fields — sizing updates as you type.";
        maybeAuto();
      } catch (err) {
        $("scenario-inputs").innerHTML = "";
        $("scenario-nvd-chart").innerHTML = "";
        $("scn-error").textContent = err.message || "could not build the scenario form";
        $("scn-error").hidden = false;
      }
    }

    function maybeAuto() {
      if (!currentScenario) return;
      const fields = scenarioInputList(currentScenario);
      const values = {};
      fields.forEach((i) => {
        const el = $("scn-in-" + i.key);
        values[i.key] = el ? el.value : null;
      });
      if (!scenarioRequiredFieldsMissing(fields, values)) calculateScenario(true);
    }

    function renderValidationBanner(v) {
      return validationBannerHtml(v);
    }

    function renderTermRows(steps, unit) {
      return (steps || []).map((s) => {
        const t = normalizeTerm(s);
        if (t.skipped) return `<tr class="skipped">
          <td><span class="role role-${esc(t.role)}">${esc(t.role)}</span><div class="term-label">${esc(t.label)}</div></td>
          <td class="num">—</td><td class="num">${esc(t.skip_reason)}</td></tr>`;
        const cite = t.source ? `
          <div class="cite">
            <span class="grade">${esc(t.confidence)}</span>
            ${t.source_url ? `<a href="${esc(t.source_url)}" target="_blank" rel="noopener">${esc(t.source)}</a>` : esc(t.source)}
            · ${esc(t.applies_to)}
          </div>` : "";
        const quote = t.quote ? `<details><summary>the sentence it was read from</summary>
          <blockquote>${esc(t.quote)}</blockquote></details>` : "";
        const running = t.mode == null ? "—" : esc(fmt(t.mode, unit));
        return `<tr>
          <td>
            <span class="role role-${esc(t.role)}">${esc(t.role)}</span>
            <div class="term-label">${esc(t.label)}</div>
            <div class="cite">${esc(t.rationale || "")}</div>
            ${cite}${quote}
          </td>
          <td class="num">${esc(t.contribution || "")}</td>
          <td class="num">${running}</td>
        </tr>`;
      }).join("");
    }

    function renderCascadeModelStep(st, i) {
      const table = (st.steps && st.steps.length)
        ? `<div class="scroll"><table><thead><tr>
             <th>Term</th><th style="text-align:right">Effect</th><th style="text-align:right">Running</th>
           </tr></thead><tbody>${renderTermRows(st.steps, st.unit)}</tbody></table></div>`
        : "";
      const constraints = (st.constraints || []).map((c) => `
        <div class="constraint-item">
          <span class="v">${esc(fmt(c.value, c.unit))}</span>
          <strong>${esc(c.label)}</strong>
          ${c.rationale ? `<p>${esc(c.rationale)}</p>` : ""}
          <div class="cite">${c.source_url ? `<a href="${esc(c.source_url)}" target="_blank" rel="noopener">${esc(c.source)}</a>` : esc(c.source || "")}</div>
        </div>`).join("");
      return `<div class="panel cascade-step">
        <div class="help">Step ${i + 1}${st.model ? " · " + esc(st.model) : ""}</div>
        <p>${esc(st.question || "")}</p>
        <div class="answer"><div class="value">${esc(fmt(st.answer.mode, st.unit))}</div>
        <div class="band">band ${esc(fmt(st.answer.lo, st.unit))} – ${esc(fmt(st.answer.hi, st.unit))}</div></div>
        ${renderValidationBanner(st.validation)}
        ${cascadeStepFootnotesHtml(st.model)}
        ${table}
        ${constraints}
      </div>`;
    }

    function renderCascadeLookupStep(st, i) {
      if (st.pick) {
        const pool = st.family
          ? (st.family === "r6i"
            ? "Smallest r6i covering host RAM. Family ceiling is r6i.32xlarge (1,024 GiB)."
            : "Smallest " + st.family + " SKU covering host RAM.")
          : (String(st.slug || "").indexOf("azure-vm") === 0
            ? "Smallest Azure VM covering host RAM."
            : "Smallest AWS SKU covering host RAM (r8i, then cited U7i).");
        const rows = [
          ["low end", st.pick.pick_lo],
          ["mode", st.pick.pick_mode],
          ["high end", st.pick.pick_hi],
        ].map(([label, spec]) => {
          if (!spec) {
            return `<tr><td>${esc(label)}</td><td>custom sizing</td><td class="num">over the largest in this pool</td></tr>`;
          }
          const ram = spec.ram_bytes != null ? fmt(spec.ram_bytes, "bytes") : "";
          const cpu = spec.vcpu != null ? spec.vcpu + " vCPU" : "";
          return `<tr><td>${esc(label)}</td><td>${esc(spec.name)}</td><td class="num">${esc([ram, cpu].filter(Boolean).join(" · "))}</td></tr>`;
        }).join("");
        const note = st.pick.exceeds_pool
          ? `<p class="help">${st.family === "r6i"
            ? "This band is above r6i.32xlarge — custom sizing on r6i, not a guessed 48xlarge."
            : "The high end exceeds this pool — custom sizing, not a guessed SKU."}</p>`
          : "";
        const modeName = (st.pick.pick_mode && st.pick.pick_mode.name) || "custom sizing";
        return `<div class="panel cascade-step">
          <div class="help">Step ${i + 1} · ${esc(st.family || st.slug || "instance pick")}</div>
          <p>${esc(pool)}</p>
          <div class="answer"><div class="value">${esc(modeName)}</div></div>
          ${note}
          <div class="scroll"><table><thead><tr><th>Band</th><th>SKU</th><th style="text-align:right">Spec</th></tr></thead><tbody>${rows}</tbody></table></div>
        </div>`;
      }
      if (st.gp3) {
        const g = st.gp3;
        return `<div class="panel cascade-step">
          <div class="help">Step ${i + 1} · gp3 volume spec</div>
          <p>gp3 baseline and provisionable ceilings for the on-disk size (collections + indexes + foreign).</p>
          <div class="answer"><div class="value">${g.volume_gib.toFixed(1)} GiB</div>
          <div class="band">${g.baseline_iops.toLocaleString()} IOPS included · ${g.baseline_throughput_mibps} MiB/s included</div></div>
          <p class="help">Max provisionable ${Math.round(g.max_provisionable_iops).toLocaleString()} IOPS · up to ${g.max_throughput_mibps} MiB/s${g.instance_name ? " · " + esc(g.instance_name) + " EBS pipe " + g.instance_ebs_bandwidth_gbps + " Gbps" : ""}.</p>
        </div>`;
      }
      return "";
    }

    function citationFromEvaluate(model, result) {
      const v = model.validation || {};
      return formatCitation({
        question: model.question,
        mode: fmt(result.mode, result.unit),
        lo: fmt(result.lo, result.unit),
        hi: fmt(result.hi, result.unit),
        validation: (GRADE_LABEL[v.grade] || v.grade || "") + (v.text ? " — " + v.text : ""),
        terms: (result.steps || []).map((s) => {
          const t = normalizeTerm(s);
          return {
            skipped: t.skipped,
            label: t.label,
            contribution: t.contribution,
            source: t.source,
            source_url: t.source_url,
            quote: t.quote,
          };
        }),
      }, corpusMeta());
    }

    function citationFromChain(data) {
      const weakest = weakestValidation(
        (data.steps || []).filter((st) => st.kind === "model").map((st) => st.validation)
      );
      const modelSteps = (data.steps || []).filter((st) => st.kind === "model" && st.answer);
      const terms = [];
      for (const st of modelSteps) {
        for (const s of st.steps || []) {
          const t = normalizeTerm(s);
          terms.push({
            skipped: t.skipped,
            label: (st.model ? st.model + ": " : "") + t.label,
            contribution: t.contribution,
            source: t.source,
            source_url: t.source_url,
            quote: t.quote,
          });
        }
      }
      const primary = modelSteps[0];
      const question = (currentScenario && currentScenario.label) || (primary && primary.question) || data.scenario;
      let mode = "", lo = "", hi = "";
      if (data.sizing_summary && data.sizing_summary.ram) {
        const ram = data.sizing_summary.ram;
        mode = fmt(ram.mode, ram.unit);
        lo = fmt(ram.lo, ram.unit);
        hi = fmt(ram.hi, ram.unit);
      } else if (primary) {
        mode = fmt(primary.answer.mode, primary.unit);
        lo = fmt(primary.answer.lo, primary.unit);
        hi = fmt(primary.answer.hi, primary.unit);
      }
      return formatCitation({
        question: question,
        mode: mode,
        lo: lo,
        hi: hi,
        validation: weakest
          ? (GRADE_LABEL[weakest.grade] || weakest.grade) + (weakest.text ? " — " + weakest.text : "")
          : "",
        terms: terms,
      }, corpusMeta());
    }

    function renderCascadeDetailsHtml(data, open) {
      const steps = (data && data.steps) || [];
      return `<details class="cascade-wrap"${open ? " open" : ""}>
          <summary>Show the math · ${steps.length} steps</summary>
          ${steps.map((st, i) => st.kind === "model"
            ? renderCascadeModelStep(st, i)
            : renderCascadeLookupStep(st, i)).join("")}
        </details>`;
    }

    function renderScientificMath(data) {
      const html = (!data || !data.steps || !data.steps.length)
        ? "<p class=\"help\">Enter a DB size in Basic, or run a scenario in Advanced — cited steps appear here.</p>"
        : renderCascadeDetailsHtml(data, true);
      document.querySelectorAll(".sci-math-host").forEach((el) => {
        el.innerHTML = html;
      });
    }

    function calculateScenario(auto, opts) {
      if (!currentScenario) return;
      const inputs = {};
      document.querySelectorAll("#scenario-inputs input, #scenario-inputs select").forEach((el) => {
        if (el.value.trim()) inputs[el.dataset.key] = el.value.trim();
      });
      try {
        const data = XY.chainEvaluate(CORPUS, currentScenario.slug, inputs);
        $("scn-error").hidden = true;
        const s = data.sizing_summary || {};
        const inst = s.cpu?.instance_mode || "custom sizing";
        const hasQueryRegime = scenarioInputList(currentScenario).map((i) => i.key).indexOf("query_regime") >= 0;
        const regimeSizingNote = hasQueryRegime ? queryRegimeSizingNote(inputs && inputs.fallback_reason) : "";
        const instSizing = (s.ram || s.cpu)
          ? `<div class="summary-metric"><div class="kicker">Instance sizing</div>
             <div class="primary">${esc(inst)}</div>
             <div class="sub">${s.ram ? esc(fmt(s.ram.mode, s.ram.unit) + " RAM") : ""}${s.cpu?.mode != null ? " · " + s.cpu.mode + " vCPU" : ""}</div>
             ${regimeSizingNote ? `<div class="help">${esc(regimeSizingNote)}</div>` : ""}</div>`
          : "";
        const r6iSizing = s.r6i
          ? `<div class="summary-metric"><div class="kicker">r6i family</div>
             <div class="primary">${esc(s.r6i.mode || "custom sizing")}</div>
             <div class="sub">${s.r6i.exceeds_pool
               ? "ceiling " + esc(s.r6i.largest || "r6i.32xlarge") + " · 1,024 GiB"
               : "what you run today"}</div></div>`
          : "";
        const perf = s.disk?.provisioned_iops
          ? `<div class="summary-metric"><div class="kicker">Storage performance</div>
             <div class="primary">${Math.round(s.disk.provisioned_iops.mode).toLocaleString()} IOPS</div>
             <div class="help">peak-second · ${s.disk.baseline_throughput_mibps} MiB/s incl</div></div>`
          : s.disk
            ? `<div class="summary-metric"><div class="kicker">Storage performance</div>
               <div class="primary">${s.disk.baseline_iops.toLocaleString()} IOPS incl</div></div>`
            : "";
        const size = s.disk
          ? `<div class="summary-metric"><div class="kicker">Storage size</div>
             <div class="primary">${Math.round(s.disk.volume_gib).toLocaleString()} GiB</div>
             <div class="help">gp3 volume</div></div>`
          : "";
        const regime = renderQueryRegimeCard(inputs);
        const inFlight = concurrencySummaryHtml(s.concurrency, inputs);
        const funnelRows = ["nvd.storage-from-vuln-growth", "mongodb.wt-cache", "mongodb.host-ram"]
          .map((slug) => data.steps.find((st) => st.model === slug))
          .filter((st) => st && st.answer);
        const fmax = Math.max(0, ...funnelRows.map((st) => st.answer.mode));
        const funnel = funnelRows.length > 1 ? funnelRows.map((st) => `<div class="funnel-row"><div>${esc(st.model)}</div>
          <div class="funnel-track"><span style="width:${fmax ? (st.answer.mode / fmax * 100).toFixed(1) : 0}%"></span></div>
          <div>${esc(fmt(st.answer.mode, st.unit))}</div></div>`).join("") : "";
        const weakest = weakestValidation(
          (data.steps || []).filter((st) => st.kind === "model").map((st) => st.validation)
        );
        const chainBanner = renderValidationBanner(weakest);
        const pathNotes = sizePathFootnotesHtml(data.steps, currentScenario.slug);
        const citeBtn = `<div class="answer-tools"><button type="button" class="ghost" data-copy-cite="scenario">Copy as citation</button></div>`;
        const sizingBlock = (instSizing || r6iSizing || perf || size)
          ? `<div class="panel sizing-summary"><h2>What you need</h2>
          <div class="summary-grid">${instSizing}${regime}${r6iSizing}${perf}${size}${inFlight}</div>${funnel}${chainBanner}${pathNotes}${citeBtn}</div>`
          : renderCitationSummary(data)
            + ((regime || inFlight)
              ? `<div class="panel sizing-summary"><h2>What your inputs mean</h2><div class="summary-grid">${regime}${inFlight}</div></div>`
              : "")
            + chainBanner + pathNotes + citeBtn;
        $("scenario-summary").innerHTML = sizingBlock;
        lastScenarioCitation = citationFromChain(data);
        const citationOnly = !(instSizing || r6iSizing || perf || size) && !!sizingBlock;
        const existing = $("scenario-cascade").querySelector("details");
        const open = (opts && opts.expandMath)
          || Date.now() < pendingExpandMathUntil
          || (existing ? existing.open : false);
        $("scenario-cascade").innerHTML = renderCascadeDetailsHtml(data, open);
        if (opts && opts.expandMath) expandScenarioMath();
        lastSimpleData = data;
        maybeRenderScientificMath();
        $("scn-recalc-status").textContent = citationOnly
          ? "Citation scenario — no fields to edit yet."
          : "Up to date — change any field to recalculate.";
        scheduleHash();
      } catch (e) {
        $("scn-error").hidden = false;
        $("scn-error").textContent = e.message;
      }
    }

    function current() { return MODELS.find((m) => m.slug === $("model").value); }

    function setQuestionChooserCollapsed(collapsed) {
      const chooser = $("question-chooser");
      const btn = $("question-change");
      if (chooser) chooser.classList.toggle("collapsed", !!collapsed);
      if (btn) btn.textContent = collapsed ? "Change question" : "Hide list";
    }

    function fillQuestionCompact(m) {
      const title = $("question-compact-title");
      const sub = $("question-compact-sub");
      const compact = $("question-compact");
      if (!title || !sub || !compact || !m) return;
      const grade = m.validation && m.validation.grade;
      const pill = grade
        ? ` <span class="grade-pill">${esc(GRADE_PILL[grade] || grade)}</span>`
        : "";
      title.innerHTML = esc(shortModelTitle(m)) + pill;
      sub.textContent = m.question || "";
      compact.hidden = false;
    }

    function renderQuestionPicker(filter) {
      const el = $("question-picker");
      if (!el) return;
      const selected = $("model") && $("model").value;
      const card = (m) => {
        const grade = m.validation && m.validation.grade;
        const pill = grade
          ? `<span class="grade-pill">${esc(GRADE_PILL[grade] || grade)}</span>`
          : "";
        const on = m.slug === selected;
        return `<label class="scenario-opt question-opt${on ? " selected" : ""}" data-slug="${esc(m.slug)}">
            <input type="radio" name="question" value="${esc(m.slug)}"${on ? " checked" : ""}>
            <span>
              <div class="label">${esc(shortModelTitle(m))} ${pill}</div>
              <div class="sub">${esc(m.question || "")}</div>
              <div class="slug">${esc(m.slug)}</div>
            </span>
          </label>`;
      };
      const kindBlock = (g) => {
        const models = (g.items || []).filter((m) => modelMatchesQuery(m, filter));
        const nested = (g.subs || []).map((sub) => {
          const items = (sub.items || []).filter((m) => modelMatchesQuery(m, filter));
          if (!items.length) return "";
          return `<h4 class="scenario-sub-title">${esc(sub.label)}</h4>
            <div class="scenario-list">${items.map(card).join("")}</div>`;
        }).join("");
        if (!models.length && !nested) return "";
        const body = `${models.length ? `<div class="scenario-list">${models.map(card).join("")}</div>` : ""}${nested}`;
        if (g.core) {
          return `<section class="scenario-kind" data-kind="${esc(g.id)}">
            <h3 class="scenario-kind-title">${esc(g.label)}</h3>
            ${body}
          </section>`;
        }
        return `<details class="scenario-kind-drawer" data-kind="${esc(g.id)}">
          <summary class="scenario-kind-title">${esc(g.label)}</summary>
          ${body}
        </details>`;
      };
      const html = groupModelsByKind(MODELS).map((band) => {
        const groups = band.groups.map(kindBlock).join("");
        if (!groups) return "";
        return `<section class="scenario-band" data-band="${esc(band.id)}">
          <h2 class="scenario-band-title">${esc(band.label)}</h2>
          ${groups}
        </section>`;
      }).join("");
      el.innerHTML = html || "<p class='help'>No questions match that filter.</p>";
      el.querySelectorAll("[data-slug]").forEach((c) => {
        c.addEventListener("click", () => pickQuestion(c.dataset.slug));
      });
    }

    function syncQuestionPicker(slug) {
      fillQuestionCompact(MODELS.find((m) => m.slug === slug));
      document.querySelectorAll("#question-picker [data-slug]").forEach((el) => {
        const on = el.dataset.slug === slug;
        el.classList.toggle("selected", on);
        const radio = el.querySelector("input[type=radio]");
        if (radio) radio.checked = on;
      });
    }

    function pickQuestion(slug, opts) {
      if (!slug || !MODELS.some((m) => m.slug === slug)) return;
      const sel = $("model");
      if (sel) sel.value = slug;
      syncQuestionPicker(slug);
      if (!opts || opts.collapse !== false) setQuestionChooserCollapsed(true);
      renderInputs();
      scheduleSingleCalc();
      if (!opts || opts.hash !== false) scheduleHash();
    }

    function bootQuestionPicker() {
      renderQuestionPicker("");
      const filter = $("question-filter");
      if (filter) {
        filter.addEventListener("input", () => renderQuestionPicker(filter.value));
      }
      const change = $("question-change");
      if (change) {
        change.addEventListener("click", () => {
          const chooser = $("question-chooser");
          const collapsed = chooser && chooser.classList.contains("collapsed");
          setQuestionChooserCollapsed(!collapsed);
          if (collapsed && filter) filter.focus();
        });
      }
      const initial = $("model") && $("model").value;
      if (initial) {
        fillQuestionCompact(MODELS.find((m) => m.slug === initial));
        setQuestionChooserCollapsed(true);
      }
    }

    function renderInputs() {
      const m = current();
      const fieldHtml = (i) => `
        <div class="field">
          <label for="in-${esc(i.key)}">${esc(i.label)}${i.required ? "" : " <span class='help' style='display:inline'>optional</span>"}</label>
          <input id="in-${esc(i.key)}" data-key="${esc(i.key)}" autocomplete="off"
                 value="${esc(i.default_value == null ? "" : i.default_value)}"
                 placeholder="${i.unit === "bytes" ? "e.g. 500GB" : esc(i.unit)}">
          ${i.help ? `<div class="help">${esc(i.help)}</div>` : ""}
        </div>`;
      const required = (m.inputs || []).filter((i) => i.required);
      const optional = (m.inputs || []).filter((i) => !i.required);
      $("inputs").innerHTML = required.map(fieldHtml).join("")
        + (optional.length
          ? `<details class="input-section-drawer"><summary>Optional inputs</summary>${optional.map(fieldHtml).join("")}</details>`
          : "");
      $("sweep").innerHTML = m.inputs
        .map((i) => `<option value="${esc(i.key)}">${esc(i.label)}</option>`).join("");
      $("result").hidden = true;
      const aside = $("single-aside");
      if (aside) aside.innerHTML = modelAsideHtml(m);
      const st = $("single-recalc-status");
      if (st) st.textContent = "Sizing and the curve update as you type.";
    }

    function readInputs() {
      const out = {};
      document.querySelectorAll("#inputs input").forEach((el) => {
        if (el.value.trim()) out[el.dataset.key] = el.value.trim();
      });
      return out;
    }

    let STATE = null;          // {model, inputs, result, available}
    let YSCALE = "log";
    let singleCalcTimer = null;

    function scheduleSingleCalc() {
      clearTimeout(singleCalcTimer);
      singleCalcTimer = setTimeout(() => calculate({ quiet: true }), 180);
    }

    function calculate(opts) {
      const quiet = !!(opts && opts.quiet);
      const m = current();
      const inputs = readInputs();
      let result;
      try {
        result = XY.evaluate(m, inputs);
      } catch (e) {
        $("error").textContent = e.message;
        $("error").hidden = false;
        $("result").hidden = true;
        const st = $("single-recalc-status");
        if (st) st.textContent = quiet ? "Fill required fields — curve waits." : st.textContent;
        return;
      }
      let available = null;
      const availRaw = $("available").value.trim();
      if (availRaw) {
        try { available = XY.parseBytes(availRaw); }
        catch (e) {
          const st = $("single-recalc-status");
          if (st) st.textContent = "Answer is not reflecting current inputs.";
          $("result").hidden = true;
          if (!quiet) {
            $("error").textContent = e.message;
            $("error").hidden = false;
          }
          return;
        }
      }
      $("error").hidden = true;
      STATE = { model: m, inputs, result, available };
      render();
      const aside = $("single-aside");
      if (aside) aside.innerHTML = modelAsideHtml(m, result, fmt);
      const st = $("single-recalc-status");
      if (st) st.textContent = "Up to date — change a field or scrub the curve.";
    }

    function render() {
      const { model: m, result: d, available } = STATE;
      const u = d.unit;
      $("q").textContent = m.question;
      $("answer").textContent = fmt(d.mode, u);
      $("band").textContent = `band ${fmt(d.lo, u)} – ${fmt(d.hi, u)}`;

      // The band as a bar, because a range written as text reads as decoration and
      // the range is the honest part of the answer.
      const spread = d.hi - d.lo;
      if (spread > 0) {
        $("bandbar").hidden = $("bandends").hidden = false;
        $("bandmode").style.left = ((d.mode - d.lo) / spread * 100) + "%";
        $("bandends").innerHTML = `<span>${esc(fmt(d.lo, u))}</span><span>${esc(fmt(d.hi, u))}</span>`;
      } else {
        $("bandbar").hidden = $("bandends").hidden = true;
      }

      const v = $("validation");
      v.className = "validation " + m.validation.grade;
      v.innerHTML = `<strong>${esc(GRADE_LABEL[m.validation.grade] || m.validation.grade)}</strong> — ${esc(m.validation.text)}`;
      const fnSlot = $("single-model-footnotes");
      if (fnSlot) fnSlot.innerHTML = cascadeStepFootnotesHtml(m.slug);

      if (available != null) {
        const h = XY.headroom(d, available);
        $("headroom").hidden = false;
        $("headroom").innerHTML = `
          <table style="margin-top:1.5rem">
            <tbody>
            <tr><td>Available</td><td class="num">${esc(fmt(h.available, u))}</td></tr>
            <tr><td>Required (mode)</td><td class="num">${esc(fmt(h.required_mode, u))}</td></tr>
            <tr><td>Utilisation</td><td class="num">${h.utilisation_mode_pct.toFixed(0)}%</td></tr>
            <tr><td>Margin</td><td class="num">${esc(fmt(h.margin_mode, u))}</td></tr>
            <tr><td><strong>Verdict</strong></td><td class="num"><strong>${esc(h.verdict)}</strong></td></tr>
            </tbody>
          </table>`;
      } else {
        $("headroom").hidden = true;
      }

      $("steps").innerHTML = renderTermRows(d.steps, u);
      lastSingleCitation = citationFromEvaluate(m, d);

      $("constraints-panel").hidden = d.constraints.length === 0;
      $("constraints").innerHTML = d.constraints.map((c) => `
        <div class="constraint-item">
          <span class="v">${esc(XY.formatG(c.coeff_mode))}${c.unit === "percent" ? "%" : ""}</span>
          <strong>${esc(c.label)}</strong>
          <p>${esc(c.rationale)}</p>
          <div class="cite">${c.source_url ? `<a href="${esc(c.source_url)}" target="_blank" rel="noopener">${esc(c.source)}</a>` : esc(c.source)}</div>
        </div>`).join("");

      $("reframe-panel").hidden = !m.reframe;
      if (m.reframe) {
        $("reframe").innerHTML = m.reframe.trim().split(/\n\s*\n/)
          .map((p) => `<p>${esc(p.replace(/\s+/g, " "))}</p>`).join("");
      }
      $("notes-panel").hidden = !m.notes;
      if (m.notes) {
        $("notes").innerHTML = m.notes.trim().split(/\n\s*\n/)
          .map((p) => `<p>${esc(p.replace(/\s+/g, " "))}</p>`).join("");
      }

      $("result").hidden = false;
      drawChart();
      scheduleHash();
    }

    function setScale(s) {
      YSCALE = s;
      $("ylin").setAttribute("aria-pressed", String(s === "linear"));
      $("ylog").setAttribute("aria-pressed", String(s === "log"));
      drawChart();
    }

    // -- the curve --------------------------------------------------------------
    // One input swept across two decades either side of what was entered, with the
    // band drawn as an envelope rather than a line. The single answer above says
    // how much; this says what happens if the figure fed in was wrong -- which,
    // for a quantity nobody measures precisely, is the more useful question.
    let SWEEP = null;   // {key, unit, xs, los, modes, his}

    function computeSweep() {
      const { model: m, inputs } = STATE;
      const key = $("sweep").value || m.inputs[0].key;
      const spec = m.inputs.find((i) => i.key === key);
      const centre = STATE.result.inputs[key];
      // No value for the swept input (an optional one left blank) means there is
      // nothing to centre on, so span a generic two decades instead of inventing a
      // midpoint the user never supplied.
      const range = sweepBounds(centre, spec.unit);
      // Snap one sample onto the value actually entered. Without this the readout
      // and the marked point sit on whichever log-spaced sample landed nearest,
      // so a page asked about 500 GB answers about 488 GB -- a small lie, and the
      // one figure on the chart a reader is most likely to check against the
      // number above it.
      const grid = sweepGrid(range.from, range.to, SAMPLES, centre);

      const xs = [], los = [], modes = [], his = [];
      for (const x of grid) {
        const trial = Object.assign({}, inputs);
        trial[key] = x;
        let r;
        try { r = XY.evaluate(m, trial); } catch (e) { continue; }
        if (!isFinite(r.mode)) continue;
        xs.push(x); los.push(r.lo); modes.push(r.mode); his.push(r.hi);
      }
      SWEEP = { key, label: spec.label, unit: spec.unit, centre, xs, los, modes, his };
      return SWEEP;
    }

    function drawChart() {
      if (!STATE) return;
      if (currentMode() !== "questions") return;
      const panel = $("tab-single");
      if (panel && panel.hidden) return;
      const s = computeSweep();
      const u = STATE.result.unit;
      const svg = $("chart");
      const { W, H, L, R, T, iw, ih } = chartLayout(720, 340, 78, 16, 16, 46);

      if (s.xs.length < 2) {
        svg.innerHTML = "";
        $("chart-desc").textContent = "";
        if ($("chart-cross")) { $("chart-cross").hidden = true; $("chart-cross").textContent = ""; }
        $("chart-note").textContent = "This input cannot be swept: every trial value failed to evaluate.";
        return;
      }

      const avail = STATE.available;
      let yMin = Math.min.apply(null, s.los);
      let yMax = Math.max.apply(null, s.his);
      if (avail != null) { yMin = Math.min(yMin, avail); yMax = Math.max(yMax, avail); }
      const logY = effectiveYScale(YSCALE, yMin) === "log";
      $("ylin").setAttribute("aria-pressed", String(!logY));
      $("ylog").setAttribute("aria-pressed", String(logY));
      if (!logY) yMin = Math.min(0, yMin);
      else { yMin = yMin / 1.15; yMax = yMax * 1.15; }
      if (!logY) yMax = yMax * 1.05;
      if (yMax === yMin) yMax = yMin + 1;

      const xLo = s.xs[0], xHi = s.xs[s.xs.length - 1];
      const px = mapLogX(xLo, xHi, L, iw);
      const py = logY ? mapLogY(yMin, yMax, T, ih) : mapLinY(yMin, yMax, T, ih);

      const path = (arr) => arr.map((y, i) => (i ? "L" : "M") + px(s.xs[i]).toFixed(1) + " " + py(y).toFixed(1)).join(" ");
      const area = path(s.his) + " " +
        s.los.map((y, i) => "L" + px(s.xs[s.xs.length - 1 - i]).toFixed(1) + " " + py(s.los[s.los.length - 1 - i]).toFixed(1)).join(" ") + " Z";

      const xTicks = ticks(xLo, xHi, true, 5);
      const yTicks = ticks(yMin, yMax, logY, 5);

      const parts = [];
      parts.push(`<g class="axis">`);
      for (const t of yTicks) {
        const y = py(t).toFixed(1);
        parts.push(`<line x1="${L}" x2="${W - R}" y1="${y}" y2="${y}"></line>`);
        parts.push(`<text x="${L - 6}" y="${y}" text-anchor="end" dominant-baseline="middle">${esc(fmt(t, u))}</text>`);
      }
      for (const t of xTicks) {
        const x = px(t).toFixed(1);
        parts.push(`<line x1="${x}" x2="${x}" y1="${T + ih}" y2="${T + ih + 4}"></line>`);
        parts.push(`<text x="${x}" y="${T + ih + 16}" text-anchor="middle">${esc(fmt(t, s.unit))}</text>`);
      }
      parts.push(`<text x="${L + iw / 2}" y="${H - 6}" text-anchor="middle">${esc(s.label)}</text>`);
      parts.push(`</g>`);

      parts.push(`<path class="band-area" d="${area}"></path>`);
      parts.push(`<path class="edge-line" d="${path(s.his)}"></path>`);
      parts.push(`<path class="edge-line" d="${path(s.los)}"></path>`);
      parts.push(`<path class="mode-line" d="${path(s.modes)}"></path>`);

      // What you already have, drawn across. Where it crosses the band is where
      // the sizing stops working -- and the crossing being a range rather than a
      // point is the whole argument for carrying a band at all.
      if (avail != null && avail >= yMin && avail <= yMax) {
        const y = py(avail).toFixed(1);
        parts.push(`<line class="have-line" x1="${L}" x2="${W - R}" y1="${y}" y2="${y}"></line>`);
        parts.push(`<text class="have-label" x="${W - R}" y="${Number(y) - 5}" text-anchor="end">you have ${esc(fmt(avail, u))}</text>`);
        const modeX = coverageX(s.xs, s.modes, avail);
        const hiX = coverageX(s.xs, s.his, avail);
        const loX = coverageX(s.xs, s.los, avail);
        const ticksX = [
          { x: hiX, label: "hi" },
          { x: modeX, label: "mode" },
          { x: loX, label: "lo" },
        ].filter((t) => t.x != null && t.x >= xLo && t.x <= xHi);
        for (const t of ticksX) {
          const cx = px(t.x).toFixed(1);
          parts.push(`<line class="cross-tick" x1="${cx}" x2="${cx}" y1="${Number(y) - 6}" y2="${Number(y) + 6}"></line>`);
          parts.push(`<text class="cross-label" x="${cx}" y="${Number(y) + 16}" text-anchor="middle">${esc(t.label)} ${esc(fmt(t.x, s.unit))}</text>`);
        }
      }

      // Where the value actually entered sits on the curve.
      if (s.centre && s.centre >= xLo && s.centre <= xHi) {
        parts.push(`<line class="cursor-line" x1="${px(s.centre).toFixed(1)}" x2="${px(s.centre).toFixed(1)}" y1="${T}" y2="${T + ih}"></line>`);
      }
      parts.push(`<circle class="cursor-dot" id="dot" r="4" cx="-20" cy="-20"></circle>`);
      parts.push(`<rect x="${L}" y="${T}" width="${iw}" height="${ih}" fill="transparent" id="hit"></rect>`);
      svg.innerHTML = parts.join("");

      const nearest = (clientX) => nearestPixelIndex(svg, clientX, W, s.xs, px);
      const show = (i) => {
        const dot = svg.querySelector("#dot");
        dot.setAttribute("cx", px(s.xs[i]).toFixed(1));
        dot.setAttribute("cy", py(s.modes[i]).toFixed(1));
        $("chart-desc").innerHTML =
          `${esc(fmt(s.xs[i], s.unit))} <span class="dim">→</span> ${esc(fmt(s.modes[i], u))} ` +
          `<span class="dim">(${esc(fmt(s.los[i], u))} – ${esc(fmt(s.his[i], u))})</span>`;
      };
      const commit = (i) => {
        const el = $("in-" + s.key);
        if (!el) return;
        el.value = String(s.xs[i]);
        calculate();
      };
      const hit = svg.querySelector("#hit");
      const reveal = bindChartScrub(hit, svg, nearest, show, commit, s.xs.length);

      const centreIdx = s.centre ? nearestIndex(s.xs, s.centre) : Math.floor(s.xs.length / 2);
      reveal(centreIdx);

      const crossEl = $("chart-cross");
      if (crossEl) {
        const caption = avail != null
          ? bandCoverageCaption(fmt(avail, u), s.xs, s.los, s.modes, s.his, avail, (x) => fmt(x, s.unit), s.label)
          : "";
        crossEl.textContent = caption;
        crossEl.hidden = !caption;
      }

      const ratio = s.his[centreIdx] / (s.los[centreIdx] || 1);
      let note =
        `${s.label} swept from ${fmt(xLo, s.unit)} to ${fmt(xHi, s.unit)}; ` +
        `everything else held at what you entered. The shaded envelope is the band, ` +
        `not error bars — at the value you gave it spans a factor of ${ratio.toFixed(1)}.`;
      if (YSCALE === "log" && !logY) {
        note += " Y-axis is linear because a curve touches 0 (log is undefined).";
      }
      $("chart-note").textContent = note;
      svg.setAttribute("aria-label",
        `${STATE.model.question} — ${s.label} on a ${logY ? "log" : "linear"} axis from ${fmt(xLo, s.unit)} to ${fmt(xHi, s.unit)}, ` +
        `answer from ${fmt(s.modes[0], u)} to ${fmt(s.modes[s.modes.length - 1], u)}. The table below has the figures.`);

      $("sweep-h").textContent = s.label;
      const step = Math.max(1, Math.floor(s.xs.length / 9));
      const rows = [];
      for (let i = 0; i < s.xs.length; i += step) {
        rows.push(`<tr><td class="num">${esc(fmt(s.xs[i], s.unit))}</td>` +
          `<td class="num">${esc(fmt(s.los[i], u))}</td>` +
          `<td class="num">${esc(fmt(s.modes[i], u))}</td>` +
          `<td class="num">${esc(fmt(s.his[i], u))}</td></tr>`);
      }
      $("sweep-rows").innerHTML = rows.join("");
    }


    // -- How it flows -----------------------------------------------------------

    function stepTitle(st) {
      if ((st.kind || "model") === "model") return st.model;
      if (st.lookup === "instance_select") return `instance pick (${st.family || "pool"})`;
      if (st.lookup === "gp3_spec") return "gp3 volume spec";
      return st.lookup || st.kind || "step";
    }

    function stepFeed(st) {
      const feed = st.feed;
      if (!feed) return "";
      if (feed === "previous") return "fed from previous band (whole lo/mode/hi)";
      const parts = Object.entries(feed).map(([k, v]) =>
        `${k} ← ${v === "previous" ? "previous band" : v}`);
      return parts.length ? "feed: " + parts.join(", ") : "";
    }

    function bootFlow() {
      const scenarios = (CORPUS.scenarios || []).filter((s) => !s.disabled);
      $("flow-scenarios").innerHTML = scenarios.map((s) => {
        const steps = s.steps || [];
        const chain = steps.map((st, i) => {
          const arrow = i ? `<div class="flow-arrow">↓ band flows (all three ends)</div>` : "";
          return `${arrow}<div class="flow-step">
            <div class="kind">${esc(st.kind || "model")}</div>
            <div class="name">${esc(stepTitle(st))}</div>
            ${stepFeed(st) ? `<div class="feed">${esc(stepFeed(st))}</div>` : ""}
          </div>`;
        }).join("");
        return `<div class="panel">
          <h2 style="margin-top:0">${esc(s.label)}</h2>
          <p class="help" style="margin:0">${esc(s.summary || "")}</p>
          <div class="flow-chain">${chain || "<p class='help'>No steps.</p>"}</div>
        </div>`;
      }).join("");

      $("flow-model").innerHTML = MODELS.map((m) =>
        `<option value="${esc(m.slug)}">${esc(m.slug)} — ${esc(m.question)}${esc(gradeSuffix(m.validation && m.validation.grade))}</option>`).join("");
      $("flow-model").addEventListener("change", renderFlowTerms);
      renderFlowTerms();
    }

    function renderFlowTerms() {
      const m = MODELS.find((x) => x.slug === $("flow-model").value);
      if (!m) { $("flow-terms").innerHTML = ""; return; }
      const chips = (m.terms || []).map((t) => {
        const apply = t.apply ? ` · ${t.apply}` : "";
        const coeff = t.coefficient
          ? ` · ${t.coeff_mode}${t.unit === "percent" ? "%" : ""}`
          : (t.input_key ? ` · input:${t.input_key}` : "");
        return `<div class="flow-step">
          <span class="term-chip ${esc(t.role)}">${esc(t.role)}</span>
          <strong>${esc(t.label)}</strong>
          <div class="feed">${esc(t.key)}${esc(apply)}${esc(coeff)}</div>
          <div class="help">${esc((t.rationale || "").replace(/\s+/g, " ").slice(0, 220))}${(t.rationale || "").length > 220 ? "…" : ""}</div>
        </div>`;
      }).join("");
      $("flow-terms").innerHTML = chips || "<p class='help'>No terms.</p>";
    }

    // -- Occupancy bands --------------------------------------------------------

    function bootOccupancy() {
      const g = CORPUS.occupancy_band;
      if (!g) {
        $("occ-verdict").textContent = "Occupancy-band guide missing from this export.";
        return;
      }
      const L = g.ladder || {};
      const target = L.eviction_target && L.eviction_target.value;
      const trigger = L.eviction_trigger && L.eviction_trigger.value;
      const dirtyTarget = L.eviction_dirty_target && L.eviction_dirty_target.value;
      const dirtyTrigger = L.eviction_dirty_trigger && L.eviction_dirty_trigger.value;

      if (target != null) {
        $("occ-mark-target").style.left = target + "%";
        $("occ-mark-target").className = occupancyMarkClass(target, 0);
        $("occ-mark-target").textContent = "target " + target;
        document.querySelector("#occ-ladder .zone.hold").style.width = target + "%";
        document.querySelector("#occ-ladder .zone.workers").style.left = target + "%";
        document.querySelector("#occ-ladder .zone.workers").style.width =
          ((trigger != null ? trigger : 95) - target) + "%";
      }
      if (trigger != null) {
        $("occ-mark-trigger").style.left = trigger + "%";
        $("occ-mark-trigger").className = occupancyMarkClass(trigger, 2);
        $("occ-mark-trigger").textContent = "trigger " + trigger;
        document.querySelector("#occ-ladder .zone.danger").style.left = trigger + "%";
        document.querySelector("#occ-ladder .zone.danger").style.width = (100 - trigger) + "%";
      }
      const ninety = $("occ-mark-ninety");
      if (ninety) ninety.className = occupancyMarkClass(90, 1);
      if (dirtyTarget != null && dirtyTrigger != null) {
        $("occ-mark-dirty-target").style.left = dirtyTarget + "%";
        $("occ-mark-dirty-target").className = occupancyMarkClass(dirtyTarget, 1);
        $("occ-mark-dirty-target").textContent = "dirty target " + dirtyTarget;
        $("occ-mark-dirty-trigger").style.left = dirtyTrigger + "%";
        $("occ-mark-dirty-trigger").className = occupancyMarkClass(dirtyTrigger, 0);
        $("occ-mark-dirty-trigger").textContent = "dirty trigger " + dirtyTrigger;
        $("occ-dirty-hold").style.width = dirtyTarget + "%";
        $("occ-dirty-warn").style.left = dirtyTarget + "%";
        $("occ-dirty-warn").style.width = (dirtyTrigger - dirtyTarget) + "%";
        $("occ-dirty-danger").style.left = dirtyTrigger + "%";
        $("occ-dirty-danger").style.width = (100 - dirtyTrigger) + "%";
      }

      const knobSrc = (g.knobs && g.knobs.length)
        ? g.knobs
        : [
            ["eviction_target", L.eviction_target, "Occupancy WT works to hold", ""],
            ["eviction_trigger", L.eviction_trigger, "App threads start eviction", ""],
            ["dirty_target", L.eviction_dirty_target, "Dirty-page hold", ""],
            ["dirty_trigger", L.eviction_dirty_trigger, "Writers stall on dirty eviction", ""],
          ].filter((row) => row[1]).map(([key, c, blurb, example]) => ({
            key, coeff: c, blurb, example,
          }));

      $("occ-vars").innerHTML = knobSrc.map((k) => {
        const c = k.coeff;
        const applies = c && c.applies_to ? ` ${esc(c.applies_to)}.` : "";
        return `<div class="var-card">
          <div class="k">${esc(k.key)}</div>
          <div class="v">${esc(String(c.value))}${c.value <= 100 ? "%" : ""}</div>
          <p>${esc(k.blurb || "")}.${applies}</p>
          ${k.example ? `<p class="ex">${esc(k.example)}</p>` : ""}
        </div>`;
      }).join("");

      $("occ-playbook").innerHTML = (g.playbook || []).map((step) =>
        `<li><p class="when">${esc(step.when)}</p><p class="do">${esc(step.do)}</p></li>`
      ).join("") || "<li><p class='do'>Playbook missing from this export.</p></li>";

      $("occ-verdict").textContent = g.verdict || "";
      $("occ-passes").innerHTML = (g.passes || []).map((p) => {
        const d = p.ops_delta_pct;
        const dTxt = d == null ? "—" : ((d > 0 ? "+" : "") + d.toFixed(2) + "%");
        return `<tr>
          <td class="lab">${esc(p.label)}</td>
          <td style="text-align:right">${p.ops_at_80.toFixed(1)}</td>
          <td style="text-align:right">${p.ops_at_90.toFixed(1)}</td>
          <td style="text-align:right">${esc(dTxt)}</td>
          <td style="text-align:right">${p.occ_mean_at_80.toFixed(2)}%</td>
          <td style="text-align:right">${p.occ_mean_at_90.toFixed(2)}%</td>
        </tr>`;
      }).join("");

      if (g.reef_saturated_occupancy_pct != null) {
        $("occ-reef").textContent =
          `Reef saturated-scan occupancy under default target: ${g.reef_saturated_occupancy_pct}% ` +
          `(observation reef-mongo-bench-2026-08-19-eviction-target-actual).`;
      }
      if (g.weakest_inference) {
        $("occ-weak").hidden = false;
        $("occ-weak").textContent = "Weakest inference — " + g.weakest_inference;
      }

      const ticketBlurb = $("occ-ticket-blurb");
      const ticketFn = footnoteForModel(g.ticket_model || "mongodb.ticket-throughput-ceiling");
      if (ticketBlurb && ticketFn) ticketBlurb.textContent = ticketFn.text;

      $("occ-tickets").innerHTML = (g.ticket_ladder || []).map((row) => {
        const lat = row.latency_ms == null ? "—" : row.latency_ms.toFixed(1) + " ms";
        return `<tr>
          <td class="lab">${esc(row.concurrency)}</td>
          <td style="text-align:right">${esc(row.peak_tickets)}</td>
          <td style="text-align:right">${esc(row.ops_per_s.toFixed(1))}</td>
          <td style="text-align:right">${esc(lat)}</td>
        </tr>`;
      }).join("") || "<tr><td colspan='4' class='lab'>Ticket ladder missing from this export.</td></tr>";

      $("occ-recipe").textContent = g.snapshot_recipe || "";

      $("occ-pct").addEventListener("input", placeOccupancy);
      $("occ-dirty").addEventListener("input", placeOccupancy);
      $("occ-open-wt").addEventListener("click", () => openModelFromOcc(g.model || "mongodb.wt-cache"));
      $("occ-open-tickets").addEventListener("click", () =>
        openModelFromOcc(g.ticket_model || "mongodb.ticket-throughput-ceiling"));
    }

    function openModelFromOcc(slug) {
      setMode("questions", { hash: false });
      setTab("single");
      pickQuestion(slug);
      if (slug === "mongodb.wt-cache") {
        const storage = $("in-storage_size");
        const index = $("in-index_size");
        if (storage && !storage.value) storage.value = "500GB";
        if (index && !index.value) index.value = "40GB";
      }
      if (typeof scheduleSingleCalc === "function") scheduleSingleCalc();
      else calculate();
      requestAnimationFrame(() => {
        const panel = $("notes-panel");
        if (panel && !panel.hidden) panel.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    }

    function placeOccupancy() {
      const g = CORPUS.occupancy_band || {};
      const L = g.ladder || {};
      const target = (L.eviction_target && L.eviction_target.value) || 80;
      const trigger = (L.eviction_trigger && L.eviction_trigger.value) || 95;
      const dirtyTarget = (L.eviction_dirty_target && L.eviction_dirty_target.value) || 5;
      const dirtyTrigger = (L.eviction_dirty_trigger && L.eviction_dirty_trigger.value) || 20;

      const raw = $("occ-pct").value.trim();
      const dirtyRaw = $("occ-dirty").value.trim();
      const you = $("occ-you");
      const dirtyYou = $("occ-dirty-you");
      const bits = [];

      if (!raw && !dirtyRaw) {
        you.hidden = true;
        dirtyYou.hidden = true;
        $("occ-place").textContent = "Enter occupancy and/or dirty % to place yourself on the ladders.";
        return;
      }

      if (raw) {
        const pct = Number(raw);
        if (!isFinite(pct) || pct < 0 || pct > 100) {
          you.hidden = true;
          bits.push("Occupancy needs a number between 0 and 100.");
        } else {
          you.hidden = false;
          you.style.left = pct + "%";
          let band, advice;
          if (pct < target - 5) {
            band = "below target";
            advice = "Spare headroom relative to the default hold — or the working set is not resident yet.";
          } else if (pct < target + 5) {
            band = "near eviction_target (healthy hold)";
            advice = "Background workers are doing their job. Size cache ≈ working_set ÷ 0.8 so you stay here under load.";
          } else if (pct < 90) {
            band = "worker band (above target, before 90)";
            advice = "Workers are active above the hold. Raising target toward 90 fills the cache fuller but is not a throughput lever.";
          } else if (pct < trigger) {
            band = "danger approach (90 → trigger)";
            advice = "Closer to app-thread conscription than to a healthy hold. Watch pages evicted by application threads and disk.";
          } else {
            band = "at/past eviction_trigger";
            advice = "Application threads are (or will be) doing eviction — latency, not OOM. Raise IOPS, shrink working set, or enlarge cache.";
          }
          bits.push(`<strong>${pct.toFixed(1)}%</strong> occupancy — ${esc(band)}. ${esc(advice)}`);
        }
      } else {
        you.hidden = true;
      }

      if (dirtyRaw) {
        const d = Number(dirtyRaw);
        if (!isFinite(d) || d < 0 || d > 100) {
          dirtyYou.hidden = true;
          bits.push("Dirty % needs a number between 0 and 100.");
        } else {
          dirtyYou.hidden = false;
          dirtyYou.style.left = d + "%";
          let band, advice;
          if (d < dirtyTarget) {
            band = "below dirty target";
            advice = "Write path has spare dirty headroom.";
          } else if (d < dirtyTrigger) {
            band = "between dirty target and trigger";
            advice = "Dirty workers are active. A bulk load can sit here with total occupancy still looking fine.";
          } else {
            band = "at/past dirty trigger";
            advice = "Writers (then readers) stall on dirty eviction I/O — this is the write-path cliff, not the 80/95 total-bytes ladder.";
          }
          bits.push(`<strong>${d.toFixed(1)}%</strong> dirty — ${esc(band)}. ${esc(advice)}`);
        }
      } else {
        dirtyYou.hidden = true;
      }

      $("occ-place").innerHTML = bits.join("<br>");
    }

    // -- Cache cliff (inv 006 / inference Phase A) ------------------------------

    function bootCacheCliff() {
      const g = CORPUS.cache_cliff;
      if (!g || !(g.legs || []).length) {
        $("cliff-verdict").textContent = "Cache-cliff series missing from this export.";
        return;
      }
      $("cliff-status").textContent = g.status || "measured";
      $("cliff-verdict").textContent = g.verdict || "";
      $("cliff-transfer").textContent =
        (g.transfer || "") +
        ` · WT cache ${g.wt_cache_gb} GB · source ${g.source} · ${g.investigation}.`;
      $("cliff-rows").innerHTML = g.legs.map((leg) => {
        const r2 = leg.ops_r2 == null ? "—" : String(Math.round(leg.ops_r2));
        const rel = leg.relative_ops == null ? "—" : leg.relative_ops.toFixed(3);
        const pages = leg.pages_per_op == null ? "—" : leg.pages_per_op.toFixed(3);
        return `<tr>
          <td class="lab">${esc(String(leg.ratio))}×</td>
          <td style="text-align:right">${Math.round(leg.ops)}</td>
          <td style="text-align:right">${esc(r2)}</td>
          <td style="text-align:right">${esc(rel)}</td>
          <td style="text-align:right">${esc(pages)}</td>
        </tr>`;
      }).join("");
      drawCliffChart(g);
      $("cliff-open-wt").addEventListener("click", () =>
        openModelFromOcc(g.model || "mongodb.wt-cache"));
    }

    function drawCliffChart(g) {
      const legs = (g.legs || []).filter((l) => l.relative_ops != null);
      const svg = $("cliff-chart");
      if (legs.length < 2) {
        svg.innerHTML = "";
        $("cliff-desc").textContent = "";
        $("cliff-note").textContent = "Need at least two measured legs to draw the shape.";
        return;
      }
      const { W, H, L, R, T, iw, ih } = chartLayout(720, 340, 64, 16, 16, 46);
      const xs = legs.map((l) => l.ratio);
      const ys = legs.map((l) => l.relative_ops);
      const ys2 = legs.map((l) => l.relative_ops_r2);
      const xLo = xs[0], xHi = xs[xs.length - 1];
      let yMin = Math.min.apply(null, ys.filter((y) => y != null));
      let yMax = Math.max.apply(null, ys.filter((y) => y != null));
      ys2.forEach((y) => {
        if (y != null) { yMin = Math.min(yMin, y); yMax = Math.max(yMax, y); }
      });
      yMin = yMin / 1.2;
      yMax = yMax * 1.15;
      if (yMin <= 0) yMin = Math.min.apply(null, ys.filter((y) => y > 0)) / 1.2;

      const px = mapLogX(xLo, xHi, L, iw);
      const py = mapLogY(yMin, yMax, T, ih);
      const pathOf = (arr) => {
        let d = "", first = true;
        for (let i = 0; i < arr.length; i++) {
          if (arr[i] == null) continue;
          d += (first ? "M" : "L") + px(xs[i]).toFixed(1) + " " + py(arr[i]).toFixed(1);
          first = false;
        }
        return d;
      };

      const xTicks = ticks(xLo, xHi, true, 6);
      const yTicks = ticks(yMin, yMax, true, 5);
      const parts = [];
      parts.push(`<g class="axis">`);
      for (const t of yTicks) {
        const y = py(t).toFixed(1);
        parts.push(`<line x1="${L}" x2="${W - R}" y1="${y}" y2="${y}"></line>`);
        parts.push(`<text x="${L - 6}" y="${y}" text-anchor="end" dominant-baseline="middle">${esc(t.toFixed(t >= 1 ? 1 : 2))}</text>`);
      }
      for (const t of xTicks) {
        const x = px(t).toFixed(1);
        parts.push(`<line x1="${x}" x2="${x}" y1="${T + ih}" y2="${T + ih + 4}"></line>`);
        parts.push(`<text x="${x}" y="${T + ih + 16}" text-anchor="middle">${esc(String(t))}×</text>`);
      }
      parts.push(`<text x="${L + iw / 2}" y="${H - 6}" text-anchor="middle">oversubscription (dataSize ÷ maxCache)</text>`);
      parts.push(`</g>`);

      const steep = g.steepest_segment || [0.8, 1.0];
      if (steep[0] >= xLo && steep[1] <= xHi) {
        const x0 = px(steep[0]), x1 = px(steep[1]);
        parts.push(`<rect class="steep-band" x="${x0.toFixed(1)}" y="${T}" width="${(x1 - x0).toFixed(1)}" height="${ih}"></rect>`);
      }
      if (1 >= xLo && 1 <= xHi) {
        const x = px(1).toFixed(1);
        parts.push(`<line class="one-x" x1="${x}" x2="${x}" y1="${T}" y2="${T + ih}"></line>`);
        parts.push(`<text class="have-label" x="${x}" y="${T + 12}" text-anchor="start"> 1.0×</text>`);
      }

      parts.push(`<path class="cliff-line" d="${pathOf(ys)}"></path>`);
      if (ys2.some((y) => y != null)) {
        parts.push(`<path class="cliff-line r2" d="${pathOf(ys2)}"></path>`);
      }
      for (let i = 0; i < legs.length; i++) {
        parts.push(`<circle class="cliff-dot" r="3.5" cx="${px(xs[i]).toFixed(1)}" cy="${py(ys[i]).toFixed(1)}"></circle>`);
        if (ys2[i] != null) {
          parts.push(`<circle class="cliff-dot r2" r="3" cx="${px(xs[i]).toFixed(1)}" cy="${py(ys2[i]).toFixed(1)}"></circle>`);
        }
      }
      parts.push(`<circle class="cursor-dot" id="cliff-dot" r="4" cx="-20" cy="-20"></circle>`);
      parts.push(`<rect x="${L}" y="${T}" width="${iw}" height="${ih}" fill="transparent" id="cliff-hit"></rect>`);
      svg.innerHTML = parts.join("");

      const nearest = (clientX) => nearestPixelIndex(svg, clientX, W, xs, px);
      const show = (i) => {
        const leg = legs[i];
        const dot = svg.querySelector("#cliff-dot");
        dot.setAttribute("cx", px(xs[i]).toFixed(1));
        dot.setAttribute("cy", py(ys[i]).toFixed(1));
        const r2 = leg.relative_ops_r2 == null ? "" :
          ` <span class="dim">· r2 ${leg.relative_ops_r2.toFixed(3)}</span>`;
        const pages = leg.pages_per_op == null ? "" :
          ` <span class="dim">· ${leg.pages_per_op.toFixed(3)} pages/op</span>`;
        $("cliff-desc").innerHTML =
          `${esc(String(leg.ratio))}× <span class="dim">→</span> relative ops ${leg.relative_ops.toFixed(3)}` +
          ` <span class="dim">(${Math.round(leg.ops)} ops/s absolute)</span>${r2}${pages}`;
      };
      const revealCliff = bindChartScrub(svg.querySelector("#cliff-hit"), svg, nearest, show, null, xs.length);
      revealCliff(Math.max(0, xs.findIndex((x) => x >= 1)));

      $("cliff-note").textContent =
        `Shaded band = steepest adjacent segment (${steep[0]}→${steep[1]}×). ` +
        `Solid = A1-r1; dashed = A1-r2 where measured. Absolute ops/s are throttle artifacts.`;
      svg.setAttribute("aria-label",
        `Relative ops per second versus cache oversubscription from ${xLo}× to ${xHi}×.`);
    }


    // -- the gate ---------------------------------------------------------------
    // Before anything renders, re-run the vectors Python computed at export time.
    // A mismatch means this file's arithmetic and the corpus have drifted, and the
    // only safe output is no output. Kept at the end so boot() cannot race the
    // `let` bindings it assigns into.
    const FAILURES = XY.checkGolden(CORPUS);
    if (FAILURES.length) {
      $("selfcheck").hidden = false;
      $("app").hidden = true;
      $("selfcheck-detail").textContent = FAILURES
        .map((f) => f.vector.model + " " + JSON.stringify(f.vector.inputs) + "\n  " + f.reason)
        .join("\n");
    } else {
      boot();
    }

  }

  attachUi();

  return {
    TABS: TABS,
    MODES: MODES,
    MODE_TABS: MODE_TABS,
    SAMPLES: SAMPLES,
    esc: esc,
    ticks: ticks,
    nearestIndex: nearestIndex,
    nearestPixelIndex: nearestPixelIndex,
    sweepBounds: sweepBounds,
    sweepGrid: sweepGrid,
    scenarioInputList: scenarioInputList,
    scenarioRequiredFieldsMissing: scenarioRequiredFieldsMissing,
    effectiveYScale: effectiveYScale,
    chartLayout: chartLayout,
    normalizeSimpleSize: normalizeSimpleSize,
    normalizeSimpleAvgBytes: normalizeSimpleAvgBytes,
    normalizeSimpleDocAvg: normalizeSimpleDocAvg,
    SIMPLE_DOC_AVG_DEFAULT: SIMPLE_DOC_AVG_DEFAULT,
    SIMPLE_VULN_DOC_AVG_DEFAULT: SIMPLE_VULN_DOC_AVG_DEFAULT,
    simpleDocProductStorage: simpleDocProductStorage,
    SIMPLE_FORM_FIELD_IDS: SIMPLE_FORM_FIELD_IDS,
    BASIC_DEFAULT_VULN_COUNT: BASIC_DEFAULT_VULN_COUNT,
    simpleSizeSliderGb: simpleSizeSliderGb,
    formatSimpleSizeSliderGb: formatSimpleSizeSliderGb,
    simpleSizeSliderIndex: simpleSizeSliderIndex,
    nvdNextYearProjection: nvdNextYearProjection,
    simpleChainInputs: simpleChainInputs,
    gradeSuffix: gradeSuffix,
    weakestValidation: weakestValidation,
    occupancyMarkClass: occupancyMarkClass,
    interpolateCrossingXs: interpolateCrossingXs,
    coverageX: coverageX,
    bandCoverageCaption: bandCoverageCaption,
    serializePermalink: serializePermalink,
    parsePermalink: parsePermalink,
    canonicalTab: canonicalTab,
    canonicalMode: canonicalMode,
    modeForTab: modeForTab,
    publicTab: publicTab,
    permalinkView: permalinkView,
    permalinkHref: permalinkHref,
    permalinkFromLocation: permalinkFromLocation,
    simpleHonestyBlockHtml: simpleHonestyBlockHtml,
    validationClause: validationClause,
    validationBannerInner: validationBannerInner,
    validationBannerHtml: validationBannerHtml,
    formatCitation: formatCitation,
    GRADE_LABEL: GRADE_LABEL,
    SYSTEM_ORDER: SYSTEM_ORDER,
    systemLabel: systemLabel,
    shortModelTitle: shortModelTitle,
    modelMatchesQuery: modelMatchesQuery,
    groupModelsBySystem: groupModelsBySystem,
    MODEL_KIND: MODEL_KIND,
    modelKind: modelKind,
    groupModelsByKind: groupModelsByKind,
    scenarioSectionIsDrawer: scenarioSectionIsDrawer,
    SCENARIO_KIND: SCENARIO_KIND,
    SCENARIO_TAXONOMY: SCENARIO_TAXONOMY,
    scenarioKind: scenarioKind,
    groupScenarios: groupScenarios,
    SIMPLE_HONESTY_LINE: SIMPLE_HONESTY_LINE,
    SIZE_PATH_FOOTNOTES: SIZE_PATH_FOOTNOTES,
    SIZE_PATH_RELATED_FOOTNOTES: SIZE_PATH_RELATED_FOOTNOTES,
    footnoteForModel: footnoteForModel,
    footnoteHtml: footnoteHtml,
    cascadeStepFootnotesHtml: cascadeStepFootnotesHtml,
    chainFootnoteSlugs: chainFootnoteSlugs,
    relatedFootnoteSlugs: relatedFootnoteSlugs,
    sizePathFootnoteSlugs: sizePathFootnoteSlugs,
    sizePathFootnotesHtml: sizePathFootnotesHtml,
    chainModelValidations: chainModelValidations,
    zeroInBand: zeroInBand,
    displayValidation: displayValidation,
    simpleWeakestValidation: simpleWeakestValidation,
    simpleHonestyBlockHtml: simpleHonestyBlockHtml,
    simpleGradeTagHtml: simpleGradeTagHtml,
    simpleCatalogMissReason: simpleCatalogMissReason,
    simpleRamHonestyOk: simpleRamHonestyOk,
    simplePickCardHtml: simplePickCardHtml,
    simpleCloudPicksHtml: simpleCloudPicksHtml,
    simpleFirstPaintHtml: simpleFirstPaintHtml,
    infoCardHtml: infoCardHtml,
    widestCitedTerm: widestCitedTerm,
    answerRangeFactor: answerRangeFactor,
    modelAsideHtml: modelAsideHtml,
    basicAsideHtml: basicAsideHtml,
    scenarioInputControlHtml: scenarioInputControlHtml,
    scenarioSectionCopyHtml: scenarioSectionCopyHtml,
    queryRegimeSizingNote: queryRegimeSizingNote,
    concurrencySummaryHtml: concurrencySummaryHtml,
    canonicalMode: canonicalMode,
    modeForTab: modeForTab,
    permalinkView: permalinkView,
    simpleHonestyBlockHtml: simpleHonestyBlockHtml,
    SIMPLE_HONESTY_LINE: SIMPLE_HONESTY_LINE,
  };
})();

if (typeof module !== "undefined" && module.exports) module.exports = XYCALC_APP;
