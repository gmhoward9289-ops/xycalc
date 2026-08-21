// Pin that occupancy-ladder tick labels cannot share a row.
// Invoked from tests/test_export.py. A later CSS tweak that puts "target 80"
// and "trigger 95" on the same top, or hides the 90 tick, must fail here.

"use strict";

const assert = require("assert");
const fs = require("fs");
const APP = require(process.argv[2]);
const html = fs.readFileSync(process.argv[3], "utf8");

assert.ok(
  !/#occ-mark-ninety\s*\{[^}]*display\s*:\s*none/i.test(html),
  "do not hide the 90 tick — #132 keeps ticks at their values",
);

function block(selector) {
  const esc = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const re = new RegExp(esc + "\\s*\\{([^}]+)\\}");
  const m = html.match(re);
  assert.ok(m, "missing CSS for " + selector);
  return m[1];
}

function decl(css, prop) {
  const re = new RegExp("(?:^|;)\\s*" + prop.replace(/-/g, "\\-") + "\\s*:\\s*([^;]+)", "i");
  const m = css.match(re);
  return m ? m[1].trim() : "";
}

function rem(value, label) {
  const m = String(value).match(/^(-?[\d.]+)rem$/);
  assert.ok(m, label + " should be rem, got " + JSON.stringify(value));
  return Number(m[1]);
}

const aboveTop = rem(decl(block("#tab-occupancy .ladder .mark-label"), "top"), "above top");
const highTop = rem(decl(block("#tab-occupancy .ladder .mark.high .mark-label"), "top"), "high top");
const belowCss = block("#tab-occupancy .ladder .mark.below .mark-label");
const belowBottom = rem(decl(belowCss, "bottom"), "below bottom");
assert.strictEqual(decl(belowCss, "top"), "auto");

const lineGap = Math.abs(highTop - aboveTop);
assert.ok(
  lineGap >= 0.9,
  "high vs above label tops must differ by ≥0.9rem so they cannot overprint; got " + lineGap,
);
assert.ok(highTop < aboveTop, "high row sits further above the bar than the default row");
assert.ok(belowBottom < 0, "below labels sit under the bar");

function occupancyRow(cls) {
  const bits = cls.split(" ");
  if (bits.indexOf("below") >= 0) return "below";
  if (bits.indexOf("high") >= 0) return "high";
  return "above";
}

function rowTop(row) {
  if (row === "below") return 10; // distinct band under the 2.4rem bar
  if (row === "high") return highTop;
  return aboveTop;
}

function align(cls) {
  const bits = cls.split(" ");
  if (bits.indexOf("edge-start") >= 0) return "start";
  if (bits.indexOf("edge-end") >= 0) return "end";
  return "center";
}

function box(pct, text, cls, ladderPx, fontPx) {
  const row = occupancyRow(cls);
  const w = text.length * fontPx * 0.62;
  const x = (pct / 100) * ladderPx;
  let left;
  if (align(cls) === "start") left = x;
  else if (align(cls) === "end") left = x - w;
  else left = x - w / 2;
  const top = rowTop(row);
  return { left, right: left + w, top, bottom: top + 0.7, text, row };
}

function overlap(a, b) {
  const sameRow = Math.abs(a.top - b.top) < 0.8;
  const horiz = a.left < b.right && a.right > b.left;
  return sameRow && horiz;
}

const total = [
  { pct: 80, text: "target 80", cls: APP.occupancyMarkClass(80, 0) },
  { pct: 90, text: "90", cls: APP.occupancyMarkClass(90, 1) },
  { pct: 95, text: "trigger 95", cls: APP.occupancyMarkClass(95, 2) },
];
const dirty = [
  { pct: 5, text: "dirty target 5", cls: APP.occupancyMarkClass(5, 1) },
  { pct: 20, text: "dirty trigger 20", cls: APP.occupancyMarkClass(20, 0) },
];

assert.strictEqual(new Set(total.map((m) => occupancyRow(m.cls))).size, 3);

for (const id of ["occ-mark-target", "occ-mark-ninety", "occ-mark-trigger",
  "occ-mark-dirty-target", "occ-mark-dirty-trigger"]) {
  assert.ok(html.includes('id="' + id + '"'), "missing " + id);
  assert.ok(html.includes("mark-label"), "labels must be .mark-label so ticks stay at left%");
}

// ~1280 desktop card (~1100px track) and ~375px viewport (track after panel pad).
const FONT = 0.7 * 16;
for (const width of [1100, 310]) {
  const boxes = total.map((m) => box(m.pct, m.text, m.cls, width, FONT));
  for (let i = 0; i < boxes.length; i++) {
    for (let j = i + 1; j < boxes.length; j++) {
      assert.ok(
        !overlap(boxes[i], boxes[j]),
        "total-cache labels collide at " + width + "px: " +
          boxes[i].text + " vs " + boxes[j].text,
      );
    }
  }
  const dirtyBoxes = dirty.map((m) => box(m.pct, m.text, m.cls, width, FONT));
  assert.ok(
    !overlap(dirtyBoxes[0], dirtyBoxes[1]),
    "dirty-occupancy labels collide at " + width + "px",
  );
}

console.log("occupancy ladder labels ok");
