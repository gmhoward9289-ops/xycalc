const fs = require("fs");
const XY = require("./src/xycalc/static/evaluate.js");
const html = fs.readFileSync(process.env.TEMP + "/xycalc-live.html", "utf8");
const m = html.match(/application\/json">([\s\S]*?)<\/script>/);
const c = JSON.parse(m[1]);
console.log(
  JSON.stringify(
    {
      golden_failures: XY.checkGolden(c).length,
      cliff_legs: c.cache_cliff.legs.length,
      status: c.cache_cliff.status,
      has_cache_cliff_tab: html.includes("Cache cliff"),
      has_scrub_hint: html.includes("Scrub the curve"),
      selfcheck_starts_hidden: /id="selfcheck" hidden/.test(html),
    },
    null,
    2
  )
);
