// Pin the 375px total-cache collision (#132). Desktop (~1024px) already
// reads with 90 below and trigger 95 above — this file must not require a
// desktop-only third row. Invoked from tests/test_export.py.

"use strict";

const assert = require("assert");
const fs = require("fs");
const APP = require(process.argv[2]);
const html = fs.readFileSync(process.argv[3], "utf8");

assert.equal(
  html.includes("#occ-mark-ninety { display: none"),
  false,
  "do not hide the 90 tick at narrow width — live 066e0c7 measured it 0×0",
);

const mqStart = html.indexOf("@media (max-width: 42rem)");
assert.ok(mqStart >= 0, "missing 42rem query");
const mqEnd = html.indexOf("}", html.indexOf("#tab-occupancy #occ-mark-trigger::after", mqStart));
assert.ok(mqEnd > mqStart, "missing #occ-mark-trigger::after in the 42rem query");
const mq = html.slice(mqStart, mqEnd + 1);

assert.ok(mq.includes("#tab-occupancy #occ-mark-trigger"), "42rem query must target trigger 95");
assert.ok(mq.includes("top: -2.6rem"), "trigger 95 must lift ≥1.5rem off target 80 at 375px");
assert.ok(mq.includes("line-height: 1"), "narrow labels need line-height 1 so the lift is a real gap");
assert.equal(mq.includes("display: none"), false, "42rem query must not hide occupancy ticks");

assert.ok(html.includes("top: -1.1rem"), "desktop above-bar row stays at -1.1rem");

const triggerTop = -2.6;
const targetTop = -1.1;
assert.ok(
  Math.abs(triggerTop - targetTop) >= 1.4,
  "narrow trigger vs target tops must differ by ≥1.4rem (body line-height 1.55)",
);

function box(pct, text, topRem, align, ladderPx, fontPx) {
  const w = text.length * fontPx * 0.62;
  const x = (pct / 100) * ladderPx;
  let left;
  if (align === "end") left = x - w;
  else if (align === "start") left = x;
  else left = x - w / 2;
  return { left, right: left + w, top: topRem, text };
}

function overlap(a, b) {
  const sameRow = Math.abs(a.top - b.top) < 0.8;
  return sameRow && a.left < b.right && a.right > b.left;
}

const FONT = 0.7 * 16;

// Desktop track (~1024px viewport, card ~700px+): 80 and 95 share a row and
// must still clear horizontally — the live retest of 066e0c7.
const desk = {
  target: box(80, "target 80", targetTop, "center", 700, FONT),
  ninety: box(90, "90", 10, "center", 700, FONT),
  trigger: box(95, "trigger 95", targetTop, "end", 700, FONT),
};
assert.ok(!overlap(desk.target, desk.trigger), "desktop 80 vs 95 should still clear");
assert.ok(!overlap(desk.ninety, desk.trigger), "desktop 90 is below trigger 95");
assert.ok(!overlap(desk.ninety, desk.target), "desktop 90 is below target 80");

// 375px: live fail was target 80 x 233–294 vs trigger 95 x 238–305 on one row.
const narrowTrack = 310;
const nar = {
  target: box(80, "target 80", targetTop, "center", narrowTrack, FONT),
  ninety: box(90, "90", 10, "center", narrowTrack, FONT),
  trigger: box(95, "trigger 95", triggerTop, "end", narrowTrack, FONT),
};
assert.ok(nar.target.left < nar.trigger.right && nar.target.right > nar.trigger.left,
  "pin still models the 375px horizontal overlap of 80 vs 95");
assert.ok(!overlap(nar.target, nar.trigger), "375px: lifted trigger 95 must not share a row with target 80");
assert.ok(!overlap(nar.ninety, nar.trigger), "375px: 90 stays below, not 0×0");
assert.ok(!overlap(nar.ninety, nar.target), "375px: 90 stays below target 80");

const dirtyNarrow = {
  t: box(5, "dirty target 5", 10, "start", narrowTrack, FONT),
  r: box(20, "dirty trigger 20", targetTop, "center", narrowTrack, FONT),
};
assert.ok(!overlap(dirtyNarrow.t, dirtyNarrow.r), "dirty ladder stays on opposite rows at 375px");

assert.ok(APP.occupancyMarkClass(80, 0).split(" ").indexOf("below") < 0);
assert.ok(APP.occupancyMarkClass(90, 1).includes("below"));
assert.ok(APP.occupancyMarkClass(95, 2).split(" ").indexOf("below") < 0);

for (const id of ["occ-mark-target", "occ-mark-ninety", "occ-mark-trigger"]) {
  assert.ok(html.includes('id="' + id + '"'), "missing " + id);
}

console.log("occupancy ladder labels ok");
