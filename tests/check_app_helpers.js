// Node assertions for the pure calculator-UI helpers in static/app.js.
// Invoked from tests/test_export.py so a missing node skips with the rest
// of the JS suite rather than reporting a silent pass.

const assert = require("assert");
const path = require("path");
const appPath = process.argv[2];
global.XY = require(path.join(path.dirname(appPath), "evaluate.js"));
const APP = require(appPath);

assert.deepStrictEqual(
  APP.TABS,
  ["scenario", "math", "single", "flow", "occupancy", "cliff"],
);
assert.strictEqual(APP.SAMPLES, 96);

// Log ticks: 1-2-5 decades, kept inside [lo, hi].
assert.deepStrictEqual(APP.ticks(1, 100, true, 5), [1, 2, 5, 10, 20, 50, 100]);
assert.deepStrictEqual(APP.ticks(8, 40, true, 5), [10, 20]);
assert.ok(APP.ticks(1, 1e12, true, 5).length <= 7);

// Linear ticks land on 1/2/5/10 * magnitude steps.
assert.deepStrictEqual(APP.ticks(0, 10, false, 5), [0, 2, 4, 6, 8, 10]);
assert.deepStrictEqual(APP.ticks(0, 100, false, 5), [0, 20, 40, 60, 80, 100]);

// nearestIndex is log-distance, not linear.
assert.strictEqual(APP.nearestIndex([1, 10, 100], 20), 1);
assert.strictEqual(APP.nearestIndex([1, 10, 100], 90), 2);
assert.strictEqual(APP.nearestIndex([1e9, 5e11, 1e13], 5e11), 1);

const withCentre = APP.sweepBounds(5e11, "bytes");
assert.strictEqual(withCentre.from, 5e10);
assert.strictEqual(withCentre.to, 5e12);
const noCentreBytes = APP.sweepBounds(null, "bytes");
assert.strictEqual(noCentreBytes.from, 1e9);
assert.strictEqual(noCentreBytes.to, 1e13);
const noCentreScalar = APP.sweepBounds(0, "count");
assert.strictEqual(noCentreScalar.from, 1);
assert.strictEqual(noCentreScalar.to, 1e4);

const grid = APP.sweepGrid(1, 100, 5, null);
assert.strictEqual(grid.length, 5);
assert.strictEqual(grid[0], 1);
assert.strictEqual(grid[4], 100);
assert.ok(Math.abs(grid[2] - 10) < 1e-9);

const snapped = APP.sweepGrid(1e9, 1e13, 96, 5e11);
assert.strictEqual(snapped.length, 96);
assert.strictEqual(snapped[APP.nearestIndex(snapped, 5e11)], 5e11);
assert.ok(snapped.includes(5e11));

const inputs = [
  { key: "storage_size", required: true },
  { key: "index_size", required: false },
];
assert.strictEqual(
  APP.scenarioRequiredFieldsMissing(inputs, { storage_size: "500GB", index_size: "" }),
  false,
);
assert.strictEqual(
  APP.scenarioRequiredFieldsMissing(inputs, { storage_size: "", index_size: "40GB" }),
  true,
);
assert.strictEqual(
  APP.scenarioRequiredFieldsMissing(inputs, { storage_size: "  ", index_size: "40GB" }),
  true,
);
assert.strictEqual(
  APP.scenarioRequiredFieldsMissing(inputs, { index_size: "40GB" }),
  true,
);
assert.strictEqual(
  APP.scenarioRequiredFieldsMissing([{ key: "x", required: false }], { x: "" }),
  false,
);
assert.strictEqual(APP.scenarioRequiredFieldsMissing([], {}), false);

const sectioned = {
  input_sections: [
    { title: "A", inputs: [{ key: "need", required: true }, { key: "opt", required: false }] },
  ],
  inputs: [],
};
assert.strictEqual(APP.scenarioInputList(sectioned).length, 2);
assert.strictEqual(
  APP.scenarioRequiredFieldsMissing(APP.scenarioInputList(sectioned), { need: "", opt: "1" }),
  true,
);
assert.strictEqual(
  APP.scenarioRequiredFieldsMissing(APP.scenarioInputList(sectioned), { need: "1", opt: "" }),
  false,
);
assert.strictEqual(APP.effectiveYScale("log", 1), "log");
assert.strictEqual(APP.effectiveYScale("log", 0), "linear");
assert.strictEqual(APP.effectiveYScale("linear", 10), "linear");

const lay = APP.chartLayout(720, 340, 78, 16, 16, 46);
assert.strictEqual(lay.iw, 720 - 78 - 16);
assert.strictEqual(lay.ih, 340 - 16 - 46);

assert.strictEqual(APP.normalizeSimpleSize(""), "");
assert.strictEqual(APP.normalizeSimpleSize("  "), "");
assert.strictEqual(APP.normalizeSimpleSize("50"), "50GB");
assert.strictEqual(APP.normalizeSimpleSize("500"), "500GB");
assert.strictEqual(APP.normalizeSimpleSize("50GB"), "50GB");
assert.strictEqual(APP.normalizeSimpleSize("64GiB"), "64GiB");
assert.strictEqual(APP.normalizeSimpleAvgBytes(""), "");
assert.strictEqual(APP.normalizeSimpleAvgBytes("2048"), "2048");
assert.strictEqual(APP.normalizeSimpleAvgBytes("2MB"), "2MB");
assert.strictEqual(APP.normalizeSimpleAvgBytes("2KB"), "2KB");

assert.deepStrictEqual(APP.SIMPLE_FORM_FIELD_IDS, [
  "simple-vulns",
  "simple-size-path",
  "simple-db-size",
  "simple-doc-count",
  "simple-doc-avg",
  "simple-doc-vulns",
  "simple-devices",
  "simple-device-avg",
  "simple-residual",
]);
assert.strictEqual(APP.normalizeSimpleDocAvg("4"), "4MB");
assert.strictEqual(APP.normalizeSimpleDocAvg("4 MB"), "4 MB");
assert.strictEqual(APP.simpleDocProductStorage("10000", "4 MB"), "40.0GB");
assert.strictEqual(APP.simpleDocProductStorage("10000", "14 MB"), "140.0GB");
assert.deepStrictEqual(
  APP.simpleChainInputs({ path: "docs", docs: "10,000", docAvg: "4" }),
  {
    baseline_vuln_count: APP.BASIC_DEFAULT_VULN_COUNT,
    baseline_storage_size: "40.0GB",
    target_vuln_count: APP.BASIC_DEFAULT_VULN_COUNT,
  },
);
assert.deepStrictEqual(
  APP.simpleChainInputs({ vulns: "", storage: "500" }),
  {
    baseline_vuln_count: APP.BASIC_DEFAULT_VULN_COUNT,
    baseline_storage_size: "500GB",
    target_vuln_count: APP.BASIC_DEFAULT_VULN_COUNT,
  },
);
assert.strictEqual(APP.simpleChainInputs({ vulns: "250000", storage: "" }), null);
assert.deepStrictEqual(
  APP.simpleChainInputs({ vulns: "250,000", storage: "500" }),
  {
    baseline_vuln_count: "250000",
    baseline_storage_size: "500GB",
    target_vuln_count: "250000",
  },
);
assert.deepStrictEqual(
  APP.simpleChainInputs({
    vulns: "250000",
    size: "500GB",
    devices: "10000",
    deviceAvg: "2MB",
    residual: "50",
  }),
  {
    baseline_vuln_count: "250000",
    baseline_storage_size: "500GB",
    target_vuln_count: "250000",
    device_count: "10000",
    device_avg_storage_bytes: "2MB",
    residual_storage_size: "50GB",
  },
);
assert.deepStrictEqual(
  APP.simpleChainInputs({ vulns: "250000", storage: "500", devices: "10" }),
  {
    baseline_vuln_count: "250000",
    baseline_storage_size: "500GB",
    target_vuln_count: "250000",
  },
);
assert.strictEqual(APP.esc("<img src=x onerror=alert(1)>"), "&lt;img src=x onerror=alert(1)&gt;");
assert.strictEqual(APP.esc('a&b"c'), "a&amp;b&quot;c");
assert.strictEqual(APP.esc("it's"), "it&#39;s");

assert.strictEqual(APP.gradeSuffix("none"), " · unvalidated");
assert.strictEqual(APP.gradeSuffix("thin"), " · thinly validated");
assert.strictEqual(APP.gradeSuffix("reasonable"), " · validated");
assert.strictEqual(APP.gradeSuffix("mystery"), "");
assert.strictEqual(APP.GRADE_LABEL.reasonable, "Validated");
assert.strictEqual(APP.GRADE_LABEL.thin, "Thinly validated");

// n=3, 0 within band must not render the strongest badge. Grade is assigned
// in Python; this pins that only `reasonable` maps to Validated, so a thin
// 0-in-band status cannot look like a pass on the Scenario step.
const zeroInBand = {
  grade: "thin",
  text: "thinly validated (n=3, 0 within band, mean absolute error 0.8%) — none of the observations fell inside the predicted band",
};
const zeroBanner = APP.validationBannerInner(zeroInBand);
assert.ok(zeroBanner.includes("Thinly validated"), zeroBanner);
assert.ok(zeroBanner.includes("0 within band"), zeroBanner);
assert.ok(!zeroBanner.includes("<strong>Validated</strong>"), zeroBanner);
assert.ok(!APP.validationBannerHtml(zeroInBand).includes(" reasonable"), APP.validationBannerHtml(zeroInBand));

const worst = APP.weakestValidation([
  { grade: "reasonable", text: "ok" },
  { grade: "none", text: "unchecked" },
  { grade: "thin", text: "thin" },
]);
assert.strictEqual(worst.grade, "none");
assert.strictEqual(APP.weakestValidation([]), null);

assert.ok(APP.occupancyMarkClass(5, 1).includes("below"));
assert.ok(APP.occupancyMarkClass(5, 1).includes("edge-start"));
assert.ok(APP.occupancyMarkClass(20, 0).split(" ").indexOf("below") < 0);
assert.ok(APP.occupancyMarkClass(95, 2).includes("edge-end"));
// Desktop (#130): 90 below, trigger 95 above — already readable at ~1024px.
assert.ok(APP.occupancyMarkClass(90, 1).includes("below"));
assert.ok(APP.occupancyMarkClass(95, 2).split(" ").indexOf("below") < 0);
assert.ok(APP.occupancyMarkClass(95, 2).split(" ").indexOf("high") < 0);

const xs = [100, 200, 400];
const ys = [50, 100, 200];
const crosses = APP.interpolateCrossingXs(xs, ys, 100);
assert.strictEqual(crosses.length, 1);
assert.ok(Math.abs(crosses[0] - 200) < 1e-9);
assert.strictEqual(APP.coverageX(xs, ys, 200), 400);
assert.strictEqual(APP.coverageX(xs, ys, 10), null);

const cap = APP.bandCoverageCaption("256 GB", [1, 2, 4], [300, 200, 100], [400, 256, 120], [500, 300, 150], 256, (x) => String(x), "vulns");
assert.ok(cap.includes("256 GB covers"));
assert.ok(cap.includes("mode"));
assert.ok(cap.includes("vulns"));

const encoded = APP.serializePermalink({
  mode: "advanced",
  tab: "single",
  model: "mongodb.wt-cache",
  available: "256GB",
  inputs: { storage_size: "500GB", index_size: "40GB" },
});
const parsed = APP.parsePermalink("#" + encoded);
assert.strictEqual(parsed.mode, "advanced");
assert.strictEqual(parsed.tab, "single");
assert.strictEqual(parsed.model, "mongodb.wt-cache");
assert.strictEqual(parsed.available, "256GB");
assert.strictEqual(parsed.inputs.storage_size, "500GB");
assert.strictEqual(APP.parsePermalink(""), null);
assert.strictEqual(APP.parsePermalink("#"), null);

// #115: Cache cliff's public slug is cache-cliff; the DOM id is tab-cliff.
assert.strictEqual(APP.canonicalTab("cache-cliff"), "cliff");
assert.strictEqual(APP.canonicalTab("cliff"), "cliff");
assert.strictEqual(APP.canonicalTab("occupancy-bands"), "occupancy");
assert.strictEqual(APP.canonicalTab("nope"), null);
assert.strictEqual(APP.publicTab("cliff"), "cache-cliff");
assert.strictEqual(APP.publicTab("occupancy"), "occupancy");

const inboundCliff = APP.parsePermalink("#mode=advanced&tab=cache-cliff");
assert.strictEqual(inboundCliff.mode, "advanced");
assert.strictEqual(inboundCliff.tab, "cliff");
const view = APP.permalinkView(inboundCliff);
assert.deepStrictEqual(view, { mode: "data", tab: "cliff" });
assert.strictEqual(APP.permalinkView(APP.parsePermalink("#mode=simple")).mode, "basic");
assert.strictEqual(APP.permalinkView(APP.parsePermalink("#mode=simple")).tab, null);
assert.strictEqual(APP.permalinkView(APP.parsePermalink("#mode=basic")).mode, "basic");
assert.strictEqual(APP.permalinkView(APP.parsePermalink("#mode=scientific")).mode, "scientific");
assert.strictEqual(APP.permalinkView(APP.parsePermalink("#mode=scientific")).tab, "math");
assert.strictEqual(APP.permalinkView(APP.parsePermalink("#mode=data")).mode, "data");
assert.strictEqual(APP.permalinkView(APP.parsePermalink("#mode=data")).tab, "occupancy");
assert.strictEqual(APP.canonicalMode("simple"), "basic");
assert.strictEqual(APP.modeForTab("cache-cliff"), "data");
assert.strictEqual(APP.modeForTab("single"), "scientific");
assert.strictEqual(APP.modeForTab("scenario"), "advanced");

const cliffHash = APP.serializePermalink({ mode: "data", tab: "cliff", inputs: {} });
assert.ok(cliffHash.includes("mode=data"), cliffHash);
assert.ok(cliffHash.includes("tab=cache-cliff"), cliffHash);
assert.ok(!cliffHash.includes("tab=scenario"), cliffHash);
const cliffRoundTrip = APP.parsePermalink("#" + cliffHash);
assert.strictEqual(cliffRoundTrip.mode, "data");
assert.strictEqual(cliffRoundTrip.tab, "cliff");

const href = APP.permalinkHref("#mode=advanced&tab=cache-cliff", {
  pathname: "/tools/xycalc/calculator/",
  search: "",
});
assert.strictEqual(href, "/tools/xycalc/calculator/#mode=advanced&tab=cache-cliff");

const modelOnly = APP.parsePermalink("#tab=single&model=mongodb.wt-cache");
assert.strictEqual(modelOnly.model, "mongodb.wt-cache");
assert.strictEqual(APP.permalinkView(modelOnly).tab, "single");
assert.strictEqual(APP.permalinkView(modelOnly).mode, "scientific");

const qModel = APP.parsePermalink("?model=mongodb.wt-cache");
assert.strictEqual(qModel.model, "mongodb.wt-cache");
assert.strictEqual(APP.permalinkView(qModel).tab, "single");

const fromQuery = APP.permalinkFromLocation({
  hash: "",
  search: "?model=mongodb.wt-cache",
});
assert.strictEqual(fromQuery.model, "mongodb.wt-cache");
const hashWins = APP.permalinkFromLocation({
  hash: "#tab=scenario&scenario=mongodb.size-to-instance",
  search: "?model=mongodb.wt-cache",
});
assert.strictEqual(hashWins.scenario, "mongodb.size-to-instance");
assert.strictEqual(hashWins.model, undefined);

// #113: known grade must render GRADE_LABEL + the corpus `text` clause, never
// "reasonable —" with an empty tail (the Simple-mode field-name miss).
const unvalidated = {
  grade: "none",
  text: "unvalidated (n=0) — no observation has ever been checked against this model",
};
const banner = APP.validationBannerInner(unvalidated);
assert.ok(banner.includes("Unvalidated"), banner);
assert.ok(banner.includes("unvalidated (n=0)"), banner);
assert.ok(banner.includes("no observation has ever been checked against this model"), banner);
assert.ok(!/^<strong>none<\/strong>/.test(banner), banner);
assert.ok(!/— (<\/|$)/.test(banner.replace(/\s+/g, " ")), banner);

const reasonable = {
  grade: "reasonable",
  text: "validated (n=12, 12 within band, mean absolute error 2.0%)",
};
const reasonableBanner = APP.validationBannerInner(reasonable);
assert.ok(reasonableBanner.includes("Validated"), reasonableBanner);
assert.ok(reasonableBanner.includes("validated (n=12"), reasonableBanner);
assert.ok(!/^<strong>reasonable<\/strong>/.test(reasonableBanner), reasonableBanner);

assert.strictEqual(APP.validationClause({ text: "from text", summary: "from summary" }), "from text");
assert.strictEqual(APP.validationClause({ summary: "from summary" }), "from summary");
assert.strictEqual(APP.validationClause({ note: "from note" }), "from note");
assert.strictEqual(APP.validationClause({ grade: "reasonable" }), "");

const xssBanner = APP.validationBannerInner({
  grade: "none",
  text: "<img src=x onerror=alert(1)>",
});
assert.ok(xssBanner.includes("&lt;img src=x onerror=alert(1)&gt;"), xssBanner);
assert.ok(!xssBanner.includes("<img src"), xssBanner);

const cite = APP.formatCitation({
  question: "How much cache?",
  mode: "180 GB",
  lo: "90 GB",
  hi: "320 GB",
  validation: "Unvalidated — n=0",
  terms: [{
    label: "storageSize",
    contribution: "× 0.5",
    source: "MongoDB docs",
    source_url: "https://example.invalid/wt",
    quote: "Set cacheSizeGB to 50% of RAM.",
  }],
}, { digest: "abc", version: "0.0.0", git: "deadbee" });
assert.ok(cite.includes("xycalc: How much cache?"));
assert.ok(cite.includes("Mode 180 GB  band 90 GB – 320 GB"));
assert.ok(cite.includes("Unvalidated"));
assert.ok(cite.includes("MongoDB docs <https://example.invalid/wt>"));
assert.ok(cite.includes("Set cacheSizeGB"));
assert.ok(cite.includes("Corpus abc · xycalc 0.0.0 · deadbee"));
assert.ok(!cite.includes("<script>"));

assert.ok(APP.SIMPLE_HONESTY_LINE.includes("What are we missing"));
assert.ok(APP.simpleHonestyBlockHtml().includes("simple-open-scientific"));
assert.ok(APP.simpleHonestyBlockHtml().includes(APP.SIMPLE_HONESTY_LINE));
assert.ok(APP.simpleHonestyBlockHtml("scientific").includes("Cited math below"));
assert.ok(!APP.simpleHonestyBlockHtml("scientific").includes("simple-open-scientific"));

const demoted = APP.displayValidation({
  grade: "reasonable",
  within_band: 0,
  text: "validated (n=3, 0 within band, mean absolute error 0.8%)",
});
assert.strictEqual(demoted.grade, "thin");
assert.ok(APP.zeroInBand({ text: "validated (n=3, 0 within band, MAE 0.8%)" }));
assert.ok(!APP.zeroInBand({ grade: "reasonable", text: "validated (n=12, 12 within band)" }));

const miss = APP.simpleCatalogMissReason({
  exceeds_pool: true,
  largest_in_pool: { name: "r8i.48xlarge", ram_bytes: 1536 * 1024 ** 3 },
}, (n) => String(n) + "B");
assert.ok(miss.includes("r8i.48xlarge"), miss);
assert.ok(miss.includes("catalog has no fit"), miss);

const chainWorst = APP.simpleWeakestValidation([
  { kind: "model", validation: { grade: "reasonable", text: "validated (n=12, 12 within band)" } },
  { kind: "lookup", validation: { grade: "none", text: "should ignore lookups" } },
  { kind: "model", validation: { grade: "none", text: "unvalidated (n=0) — no observation has ever been checked against this model" } },
]);
assert.strictEqual(chainWorst.grade, "none");

const okBanner = APP.validationBannerHtml({
  grade: "none",
  text: "unvalidated (n=0) — no observation has ever been checked against this model",
});
assert.ok(APP.simpleRamHonestyOk("120 GB", okBanner, {
  grade: "none",
  text: "unvalidated (n=0) — no observation has ever been checked against this model",
}));
assert.ok(!APP.simpleRamHonestyOk("120 GB", "<div>reasonable —</div>", { grade: "reasonable" }));

assert.ok(APP.SIZE_PATH_FOOTNOTES["mongodb.wt-cache"].text.includes("FINDINGS 006"));
assert.ok(APP.SIZE_PATH_FOOTNOTES["mongodb.ticket-throughput-ceiling"].text.includes("FINDINGS 003"));
assert.ok(APP.SIZE_PATH_FOOTNOTES["ebs.iops-to-provision"].text.includes("cooper-burst-2026-08-21"));
assert.strictEqual(APP.cascadeStepFootnotesHtml("mongodb.host-ram"), "");
assert.deepStrictEqual(
  APP.relatedFootnoteSlugs("mongodb.size-to-instance"),
  ["mongodb.ticket-throughput-ceiling"],
);
assert.deepStrictEqual(APP.relatedFootnoteSlugs("ebs.microburst"), []);

const queryRegimeField = {
  key: "query_regime",
  label: "Query regime",
  unit: "text",
  help: "aggregation | fallback (planning default: fallback). Not consumed by a model.",
};
const regimeControl = APP.scenarioInputControlHtml(queryRegimeField, "");
assert.ok(regimeControl.includes("fallback"), regimeControl);
assert.ok(regimeControl.includes('type="hidden"'), regimeControl);
assert.ok(regimeControl.includes('value="fallback"'), regimeControl);
assert.ok(regimeControl.includes('aria-pressed="true"'), regimeControl);

const celeryCopy = APP.scenarioSectionCopyHtml("Concurrency and Celery");
assert.ok(celeryCopy.includes("More Celery workers increase in-flight scans and broker occupancy."), celeryCopy);
assert.ok(celeryCopy.includes("They do not raise the stall completion ceiling"), celeryCopy);
assert.ok(celeryCopy.includes("scan_fanout is how many queries one task issues."), celeryCopy);
assert.ok(celeryCopy.includes("Allow-list misses never use v1/v2 aggregation"), celeryCopy);

const mixedNote = APP.queryRegimeSizingNote("mixed");
assert.ok(mixedNote.includes("same regardless of query regime"), mixedNote);
assert.ok(!mixedNote.includes("if the DB stays small"), mixedNote);
const allowlistNote = APP.queryRegimeSizingNote("allowlist");
assert.ok(allowlistNote.toLowerCase().includes("allow-list"), allowlistNote);

const queued = APP.concurrencySummaryHtml({ slots: 8, fanout: 12, in_flight: 96 }, { tickets: "64" });
assert.ok(queued.includes("96 in-flight scans"), queued);
assert.ok(queued.includes("arrivals queue; this is not extra ops/s"), queued);
assert.ok(queued.includes("More Celery workers increase in-flight scans and broker occupancy."), queued);

const wide = APP.widestCitedTerm({
  terms: [
    { key: "a", label: "tight", coeff_lo: 1, coeff_mode: 1, coeff_hi: 1.1 },
    { key: "b", label: "peak-to-mean", coeff_lo: 1.5, coeff_mode: 3, coeff_hi: 10, unit: "ratio" },
  ],
});
assert.strictEqual(wide.term.key, "b");
assert.ok(Math.abs(wide.factor - 10 / 1.5) < 1e-9, wide.factor);

assert.strictEqual(APP.answerRangeFactor({ lo: 100, hi: 200 }), 2);
assert.strictEqual(APP.answerRangeFactor({ lo: 0, hi: 200 }), null);

const aside = APP.modelAsideHtml({
  validation: { grade: "thin", text: "thinly validated (n=1)" },
  lab: { label: "WiredTiger cache size", measured: "Two resident-cache cases.", still_needs: "Independent collections." },
  terms: [{ key: "b", label: "peak-to-mean", coeff_lo: 1.5, coeff_mode: 3, coeff_hi: 10, unit: "ratio" }],
  slug: "ebs.iops-to-provision",
});
assert.ok(aside.includes("Checked against reality"), aside);
assert.ok(aside.includes("Thinly validated"), aside);
assert.ok(aside.includes("What we measured"), aside);
assert.ok(aside.includes("Widest cited coefficient"), aside);
assert.ok(aside.includes("peak-to-mean"), aside);
assert.ok(aside.includes("&lt;img") || !aside.includes("<img src=x"), aside);
const xssAside = APP.modelAsideHtml({
  validation: { grade: "none", text: "<img src=x onerror=alert(1)>" },
  terms: [],
});
assert.ok(xssAside.includes("&lt;img"), xssAside);
assert.ok(!xssAside.includes("<img src=x"), xssAside);

const basicAside = APP.basicAsideHtml();
assert.ok(basicAside.includes("storageSize, not dataSize"), basicAside);
assert.ok(basicAside.includes("4 MB · vulns 14 MB"), basicAside);
assert.ok(basicAside.includes("Occupancy / cache-cliff"), basicAside);

assert.strictEqual(APP.systemLabel("mongodb"), "MongoDB");
assert.strictEqual(APP.systemLabel("azure-disks"), "Azure disks");
assert.strictEqual(APP.systemLabel(""), "Other");
assert.strictEqual(
  APP.shortModelTitle({ lab: { label: "WiredTiger cache size" }, question: "How much RAM…" }),
  "WiredTiger cache size",
);
assert.ok(APP.modelMatchesQuery({ slug: "ebs.iops-to-provision", question: "microburst", system: "ebs" }, "iops"));
assert.ok(!APP.modelMatchesQuery({ slug: "mongodb.wt-cache", question: "cache", system: "mongodb" }, "azure"));
const grouped = APP.groupModelsBySystem([
  { slug: "ebs.iops-to-provision", system: "ebs" },
  { slug: "mongodb.wt-cache", system: "mongodb" },
  { slug: "azure.premium-v2-throughput-ceiling", system: "azure-disks" },
]);
assert.deepStrictEqual(grouped.map((g) => g.system), ["mongodb", "ebs", "azure-disks"]);

const scenarioBands = APP.groupScenarios([
  { slug: "clickhouse.parts-insert-ceiling", ui: { band: "hardware", group: "database", sub: "clickhouse" } },
  { slug: "mongodb.size-to-instance", ui: { band: "hardware", group: "instance" } },
  { slug: "redis.celery-broker", ui: { band: "runtime", group: "redis" } },
  { slug: "future.unmapped" },
]);
assert.deepStrictEqual(scenarioBands.map((b) => b.id), ["hardware", "runtime"]);
assert.strictEqual(scenarioBands[0].groups[0].id, "instance");
assert.strictEqual(scenarioBands[0].groups[0].core, true);
assert.strictEqual(scenarioBands[0].groups[1].id, "database");
assert.strictEqual(scenarioBands[0].groups[1].subs[0].id, "clickhouse");
assert.deepStrictEqual(scenarioBands[1].groups.map((g) => g.id), ["services", "redis"]);
assert.strictEqual(APP.scenarioKind({ slug: "ebs.microburst" }).group, "storage");
assert.ok(APP.scenarioSectionIsDrawer({ title: "Current node (optional)", inputs: [{ required: true }] }));
assert.ok(APP.scenarioSectionIsDrawer({ title: "Concurrency and Celery", inputs: [{ required: true }] }));
assert.ok(!APP.scenarioSectionIsDrawer({ title: "Project database size", inputs: [{ required: true }] }));

const modelBands = APP.groupModelsByKind([
  { slug: "mongodb.wt-cache" },
  { slug: "clickhouse.parts-insert-ceiling" },
  { slug: "celery.redis-broker-maxmemory" },
]);
assert.deepStrictEqual(modelBands.map((b) => b.id), ["hardware", "runtime"]);
assert.strictEqual(APP.modelKind({ slug: "ebs.iops-to-provision" }).group, "storage");

assert.strictEqual(APP.formatSimpleSizeSliderGb(500), "500GB");
assert.strictEqual(APP.formatSimpleSizeSliderGb(2000), "2TB");
assert.strictEqual(APP.simpleSizeSliderIndex(10), 0);
assert.strictEqual(APP.simpleSizeSliderIndex(32000), 80);
assert.strictEqual(APP.simpleSizeSliderGb(0), 10);
assert.strictEqual(APP.simpleSizeSliderGb(80), 32000);
assert.ok(Math.abs(APP.simpleSizeSliderGb(APP.simpleSizeSliderIndex(500)) - 500) <= 20);

const nvdProj = APP.nvdNextYearProjection({
  annual: [{ year: 2023, count: 28818 }, { year: 2024, count: 40009 }, { year: 2025, count: 48185 }],
  growth_pct: { lo: 15, mode: 21, hi: 39 },
});
assert.strictEqual(nvdProj.year, 2026);
assert.ok(Math.abs(nvdProj.mode - 48185 * 1.21) < 1e-6);
assert.ok(Math.abs(nvdProj.lo - 48185 * 1.15) < 1e-6);
assert.ok(Math.abs(nvdProj.hi - 48185 * 1.39) < 1e-6);
assert.strictEqual(APP.nvdNextYearProjection({ annual: [] }), null);
console.log("app helpers ok");
