#!/usr/bin/env node
// Dual Python/JS golden gate for the static export. Same check as
// tmp-check-live.js / XY.checkGolden(), but it exits non-zero on disagreement
// so a deploy job can fail closed. Optional second HTML path must carry the
// same corpus_digest so a live fetch cannot pass by serving an older page
// whose goldens still happen to agree.
"use strict";

const fs = require("fs");
const path = require("path");
const XY = require("../../src/xycalc/static/evaluate.js");

function corpusFromHtml(file) {
  const html = fs.readFileSync(file, "utf8");
  const m = html.match(
    /<script id="corpus" type="application\/json">([\s\S]*?)<\/script>/
  );
  if (!m) {
    throw new Error(file + ": no <script id=\"corpus\" type=\"application/json\"> blob");
  }
  return JSON.parse(m[1]);
}

function report(file, corpus) {
  const failures = XY.checkGolden(corpus);
  for (const f of failures) {
    const vec = f.vector || {};
    const label = vec.model || vec.scenario || "?";
    console.error(label + " " + JSON.stringify(vec.inputs || {}) + " :: " + f.reason);
  }
  console.log(
    JSON.stringify({
      file: file,
      golden_failures: failures.length,
      corpus_digest: corpus.corpus_digest || null,
      xycalc_git: corpus.xycalc_git || null,
    })
  );
  return failures;
}

if (process.argv.length < 3) {
  console.error("usage: check-export-goldens.js <html> [must-match-html]");
  process.exit(2);
}

const primary = path.resolve(process.argv[2]);
const corpus = corpusFromHtml(primary);
const failures = report(primary, corpus);
if (failures.length) process.exit(1);

if (process.argv[3]) {
  const otherPath = path.resolve(process.argv[3]);
  const other = corpusFromHtml(otherPath);
  const otherFailures = report(otherPath, other);
  if (otherFailures.length) process.exit(1);
  if (corpus.corpus_digest !== other.corpus_digest) {
    console.error(
      "corpus_digest mismatch: " +
        primary +
        " has " +
        corpus.corpus_digest +
        " but " +
        otherPath +
        " has " +
        other.corpus_digest +
        " (live page is not the blob that just exported)"
    );
    process.exit(1);
  }
}
