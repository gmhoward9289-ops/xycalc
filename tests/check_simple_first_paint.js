// Default Simple first-paint honesty. Invoked from tests/test_export.py with
// app.js, evaluate.js, and a corpus blob so the 100GB path cannot drift from
// what calculateSimple actually runs.
const assert = require("assert");
const fs = require("fs");

const APP = require(process.argv[2]);
const XY = require(process.argv[3]);
const corpus = JSON.parse(fs.readFileSync(process.argv[4], "utf8"));

const FLOOR = 64 * 1024 ** 3;

function applySimpleHostFloor(data) {
  const s = data.sizing_summary || {};
  if (!s.ram) return data;
  const clamp = (n) => (n < FLOOR ? FLOOR : n);
  s.ram = {
    lo: clamp(s.ram.lo),
    mode: clamp(s.ram.mode),
    hi: clamp(s.ram.hi),
    unit: s.ram.unit,
  };
  const ceiling = corpus.default_instance_ceiling_bytes;
  const pick = XY.selectInstance(
    { lo: s.ram.lo, mode: s.ram.mode, hi: s.ram.hi },
    corpus.instance_catalog || [],
    null,
    ceiling === 0 ? null : ceiling,
  );
  if (pick && s.cpu) {
    s.cpu.instance_lo = pick.pick_lo && pick.pick_lo.name;
    s.cpu.instance_mode = pick.pick_mode && pick.pick_mode.name;
    s.cpu.instance_hi = pick.pick_hi && pick.pick_hi.name;
  }
  data.sizing_summary = s;
  data.simple_instance_pick = pick || null;
  return data;
}

function simpleInputs(size) {
  return APP.simpleChainInputs({ vulns: "250000", storage: size });
}

function paint(size) {
  const data = applySimpleHostFloor(
    XY.chainEvaluate(corpus, "mongodb.size-to-instance", simpleInputs(size)),
  );
  return { data, panel: APP.simpleFirstPaintHtml(data, XY.formatQuantity) };
}

function hasSentence(hay, sentence) {
  return hay.includes(sentence) || hay.includes(APP.esc(sentence));
}

function assertHonesty(panel, label) {
  assert.ok(panel.ramText, label + ": expected a host-RAM figure, got " + JSON.stringify(panel.ramText));
  assert.ok(panel.weakest && panel.weakest.grade != null, label + ": missing weakest grade");
  const clause = APP.validationClause(panel.weakest);
  assert.ok(clause, label + ": weakest clause empty");
  assert.ok(panel.bannerHtml.includes(APP.GRADE_LABEL[panel.weakest.grade] || panel.weakest.grade), panel.bannerHtml);
  assert.ok(panel.bannerHtml.includes(clause) || panel.bannerHtml.includes(APP.esc(clause)), panel.bannerHtml);
  assert.ok(/\bn=\d+/.test(clause) || /unvalidated \(n=0\)/.test(clause), clause);
  assert.ok(panel.html.includes(APP.SIMPLE_HONESTY_LINE), panel.html);
  assert.ok(panel.html.includes("simple-open-advanced"), panel.html);
  const fn = APP.SIZE_PATH_FOOTNOTES;
  assert.ok(hasSentence(panel.html, fn["mongodb.wt-cache"].text), panel.html);
  assert.ok(hasSentence(panel.html, fn["mongodb.ticket-throughput-ceiling"].text), panel.html);
  assert.ok(hasSentence(panel.html, fn["ebs.iops-to-provision"].text), panel.html);
  assert.ok(hasSentence(panel.footnotesHtml, fn["mongodb.wt-cache"].text), panel.footnotesHtml);
  assert.ok(APP.simpleRamHonestyOk(panel.ramText, panel.bannerHtml, panel.weakest), label);
  if (panel.weakest.grade !== "reasonable" || APP.zeroInBand(panel.weakest)) {
    assert.ok(!panel.html.includes("<strong>Validated</strong>"), panel.html);
  }
}

const first = paint("100GB");
assertHonesty(first.panel, "100GB first paint");
assert.ok(first.panel.html.includes(first.panel.ramText));

const stale = APP.simpleFirstPaintHtml({
  sizing_summary: {
    ram: { lo: 6.26e11, mode: 6.26e11, hi: 6.26e11, unit: "bytes" },
    cpu: { instance_mode: "r8i.24xlarge" },
  },
  steps: [{
    kind: "model",
    validation: {
      grade: "reasonable",
      within_band: 0,
      text: "validated (n=3, 0 within band, mean absolute error 0.8%)",
    },
  }],
}, XY.formatQuantity);
assert.ok(stale.ramText, stale);
assert.ok(!stale.html.includes("<strong>Validated</strong>"), stale.html);
assert.ok(stale.html.includes("Thinly validated"), stale.html);
assert.ok(stale.html.includes("0 within band"), stale.html);
assert.ok(stale.html.includes(APP.SIMPLE_HONESTY_LINE), stale.html);

const noGrade = APP.simpleFirstPaintHtml({
  sizing_summary: { ram: { lo: 1e11, mode: 1e11, hi: 1e11, unit: "bytes" } },
  steps: [],
}, XY.formatQuantity);
assert.strictEqual(noGrade.ramText, "");
assert.ok(noGrade.html.includes(APP.SIMPLE_HONESTY_LINE));

const homepage = paint("500GB");
assertHonesty(homepage.panel, "500GB homepage");
assert.ok(!homepage.panel.picksHtml.includes("custom sizing"), homepage.panel.picksHtml);
assert.ok(homepage.panel.picksHtml.includes("r8i.96xlarge"), homepage.panel.picksHtml);
assert.ok(homepage.panel.picksHtml.includes("u7i-12tb.224xlarge"), homepage.panel.picksHtml);
assert.ok(
  /3\.1\s*TB/.test(homepage.panel.ramText),
  "500 GB Simple host RAM should stay ~3.1 TB, got " + homepage.panel.ramText,
);
assert.ok(
  /unvalidated \(n=0\)/i.test(homepage.panel.bannerHtml + homepage.panel.html),
  homepage.panel.bannerHtml,
);

console.log("simple first paint ok");
