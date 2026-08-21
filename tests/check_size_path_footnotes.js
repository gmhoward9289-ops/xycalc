// Pin the three measured size-path footnotes on the default MongoDB size
// path (Simple first paint + size-to-instance chain). Invoked from
// tests/test_export.py with app.js, evaluate.js, and a corpus blob.
const assert = require("assert");
const fs = require("fs");

const APP = require(process.argv[2]);
const XY = require(process.argv[3]);
const corpus = JSON.parse(fs.readFileSync(process.argv[4], "utf8"));

const OCC = APP.SIZE_PATH_FOOTNOTES["mongodb.wt-cache"].text;
const TIX = APP.SIZE_PATH_FOOTNOTES["mongodb.ticket-throughput-ceiling"].text;
const EBS = APP.SIZE_PATH_FOOTNOTES["ebs.iops-to-provision"].text;

assert.ok(OCC.includes("FINDINGS 006"));
assert.ok(OCC.includes("FINDINGS 007"));
assert.ok(OCC.includes("0.8→1.0"));
assert.ok(OCC.includes("−3.8") || OCC.includes("-3.8"));
assert.ok(OCC.includes("80%"));
assert.ok(OCC.includes("95%"));
assert.ok(OCC.includes("Not a new GB coefficient"));

assert.ok(TIX.includes("FINDINGS 003"));
assert.ok(TIX.includes("issue #2"));
assert.ok(TIX.includes("pinned-N"));
assert.ok(TIX.includes("4, not 128"));
assert.ok(TIX.includes("32×") || TIX.includes("32x"));
assert.ok(TIX.includes("~110"));
assert.ok(TIX.includes("4→74"));
assert.ok(TIX.includes("64"));
assert.ok(TIX.includes("611"));
assert.ok(TIX.includes("Default 128"));

assert.ok(EBS.includes("issue #4"));
assert.ok(EBS.includes("cooper-burst-2026-08-21"));
assert.ok(EBS.includes("1.5–3–10"));
assert.ok(EBS.includes("6.7"));
assert.ok(EBS.includes("n=0"));
assert.ok(EBS.includes("1.59"));
assert.ok(EBS.includes("1.16"));
assert.ok(EBS.includes("12.59"));

assert.ok(APP.cascadeStepFootnotesHtml("mongodb.wt-cache").includes(OCC));
assert.ok(APP.cascadeStepFootnotesHtml("ebs.iops-to-provision").includes(EBS));
assert.ok(APP.cascadeStepFootnotesHtml("mongodb.ticket-throughput-ceiling").includes(TIX));
assert.strictEqual(APP.cascadeStepFootnotesHtml("mongodb.host-ram"), "");
assert.strictEqual(APP.cascadeStepFootnotesHtml("nvd.storage-from-vuln-growth"), "");

const sizeInputs = {
  baseline_storage_size: "100GB",
  baseline_vuln_count: "250000",
  target_vuln_count: "250000",
};
const sizeChain = XY.chainEvaluate(corpus, "mongodb.size-to-instance", sizeInputs);
const chainSlugs = (sizeChain.steps || []).map((st) => st.model).filter(Boolean);
assert.ok(chainSlugs.includes("mongodb.wt-cache"), chainSlugs.join(","));
assert.ok(chainSlugs.includes("ebs.iops-to-provision"), chainSlugs.join(","));
assert.ok(!chainSlugs.includes("mongodb.ticket-throughput-ceiling"), chainSlugs.join(","));

const fromChain = APP.chainFootnoteSlugs(sizeChain.steps);
assert.deepStrictEqual(fromChain, ["mongodb.wt-cache", "ebs.iops-to-provision"]);

const sizeSlugs = APP.sizePathFootnoteSlugs(sizeChain.steps, "mongodb.size-to-instance");
assert.deepStrictEqual(sizeSlugs, [
  "mongodb.wt-cache",
  "ebs.iops-to-provision",
  "mongodb.ticket-throughput-ceiling",
]);

const whatYouNeed = APP.sizePathFootnotesHtml(sizeChain.steps, "mongodb.size-to-instance");
assert.ok(whatYouNeed.includes(OCC), whatYouNeed);
assert.ok(whatYouNeed.includes(TIX), whatYouNeed);
assert.ok(whatYouNeed.includes(EBS), whatYouNeed);

const ebsOnly = XY.chainEvaluate(corpus, "ebs.microburst", { average_iops: "4000" });
const ebsHtml = APP.sizePathFootnotesHtml(ebsOnly.steps, "ebs.microburst");
assert.ok(ebsHtml.includes(EBS), ebsHtml);
assert.ok(!ebsHtml.includes(OCC), ebsHtml);
assert.ok(!ebsHtml.includes(TIX), ebsHtml);

const paint = APP.simpleFirstPaintHtml(sizeChain, XY.formatQuantity);
assert.ok(paint.footnotesHtml.includes(OCC));
assert.ok(paint.footnotesHtml.includes(TIX));
assert.ok(paint.footnotesHtml.includes(EBS));
assert.ok(paint.html.includes(OCC));
assert.ok(paint.html.includes(TIX));
assert.ok(paint.html.includes(EBS));

console.log("size path footnotes ok");
