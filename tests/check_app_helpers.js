// Node assertions for the pure calculator-UI helpers in static/app.js.
// Invoked from tests/test_export.py so a missing node skips with the rest
// of the JS suite rather than reporting a silent pass.

const assert = require("assert");
const APP = require(process.argv[2]);

assert.deepStrictEqual(
  APP.TABS,
  ["scenario", "single", "flow", "occupancy", "cliff"],
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
assert.strictEqual(APP.esc("<img src=x onerror=alert(1)>"), "&lt;img src=x onerror=alert(1)&gt;");
assert.strictEqual(APP.esc('a&b"c'), "a&amp;b&quot;c");
assert.strictEqual(APP.esc("it's"), "it&#39;s");

assert.strictEqual(APP.gradeSuffix("none"), " · unvalidated");
assert.strictEqual(APP.gradeSuffix("thin"), " · thinly validated");
assert.strictEqual(APP.gradeSuffix("reasonable"), " · validated");
assert.strictEqual(APP.gradeSuffix("mystery"), "");

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
assert.strictEqual(parsed.tab, "single");
assert.strictEqual(parsed.model, "mongodb.wt-cache");
assert.strictEqual(parsed.available, "256GB");
assert.strictEqual(parsed.inputs.storage_size, "500GB");
assert.strictEqual(APP.parsePermalink(""), null);
assert.strictEqual(APP.parsePermalink("#"), null);

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

console.log("app helpers ok");
