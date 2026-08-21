// Issue #13 / T5 — covered query index residency vs document fetch.
// Assumes mongodb_load.js already ran and mongod was restarted (cold WT cache).

const OCC_ABORT = Number(process.env.PROBE_OCCUPANCY_ABORT_PCT || 70);

function cacheSnap() {
  const c = db.serverStatus().wiredTiger.cache;
  const max = c["maximum bytes configured"];
  const cur = c["bytes currently in the cache"];
  return {
    inCache: cur,
    maxCache: max,
    occupancyPct: max ? (100 * cur) / max : 0,
    pagesReadIntoCache: c["pages read into cache"],
  };
}

function assertOcc(label, snap) {
  if (snap.occupancyPct > OCC_ABORT) {
    throw new Error(
      `REFUSING: occupancy ${snap.occupancyPct.toFixed(1)}% > ${OCC_ABORT}% after ${label}`
    );
  }
}

function hasFetch(stage) {
  if (!stage) return false;
  if (stage.stage === "FETCH") return true;
  if (stage.inputStage) return hasFetch(stage.inputStage);
  if (stage.shards) {
    return stage.shards.some((s) => hasFetch(s.executionStages || s));
  }
  return false;
}

function explainCovered(query, projection, hint) {
  const ex = db.events.find(query, projection).hint(hint).explain("executionStats");
  const stats = ex.executionStats || ex;
  const plan = stats.executionStages || (ex.queryPlanner && ex.queryPlanner.winningPlan);
  const docsExamined = stats.totalDocsExamined != null
    ? stats.totalDocsExamined
    : (stats.executionStages && stats.executionStages.docsExamined) || 0;
  return { plan, docsExamined, hasFetch: hasFetch(plan), raw: ex };
}

function fullScan(query, projection, hint) {
  let n = 0;
  const cur = db.events.find(query, projection).hint(hint).batchSize(1000);
  while (cur.hasNext()) {
    cur.next();
    n++;
  }
  return n;
}

function plateauCheck(label, query, projection, hint, beforePages) {
  const n = fullScan(query, projection, hint);
  const after = cacheSnap();
  const delta = after.pagesReadIntoCache - beforePages;
  if (delta > 100) {
    throw new Error(
      `REFUSING: plateau re-run for ${label} still read ${delta} pages into cache`
    );
  }
  return { docs: n, pagesReadDelta: delta, cache: after };
}

const db = db.getSiblingDB("xycalcbench");

// Cold baseline after restart.
const t1 = cacheSnap();
assertOcc("T1", t1);
if (t1.inCache > 50 * 1024 * 1024) {
  throw new Error(
    `REFUSING: cache still warm after restart (${(t1.inCache / 1e6).toFixed(0)} MB)`
  );
}

const stats = db.events.stats();
const indexSizes = stats.indexSizes || {};

// --- Phase A: covered high-cardinality account_id ---
const hintA = { account_id: 1 };
const projA = { _id: 0, account_id: 1 };
const exA = explainCovered({}, projA, hintA);
if (exA.hasFetch || exA.docsExamined !== 0) {
  throw new Error(
    `REFUSING: Phase A not covered (hasFetch=${exA.hasFetch}, docsExamined=${exA.docsExamined})`
  );
}
const pagesBeforeA = cacheSnap().pagesReadIntoCache;
const nA = fullScan({}, projA, hintA);
const t2 = cacheSnap();
assertOcc("T2", t2);
const plateauA = plateauCheck("A", {}, projA, hintA, t2.pagesReadIntoCache);

// --- Phase B: covered low-cardinality compound ---
const hintB = { status: 1, region: 1 };
const projB = { _id: 0, status: 1, region: 1 };
const exB = explainCovered({}, projB, hintB);
if (exB.hasFetch || exB.docsExamined !== 0) {
  throw new Error(
    `REFUSING: Phase B not covered (hasFetch=${exB.hasFetch}, docsExamined=${exB.docsExamined})`
  );
}
const nB = fullScan({}, projB, hintB);
const t3 = cacheSnap();
assertOcc("T3", t3);
const plateauB = plateauCheck("B", {}, projB, hintB, t3.pagesReadIntoCache);

// --- Phase C: document fetch on account_id ---
const hintC = { account_id: 1 };
const projC = {}; // full documents
const exC = explainCovered({}, projC, hintC);
if (!exC.hasFetch) {
  throw new Error("REFUSING: Phase C expected FETCH stage, plan has none");
}
const nC = fullScan({}, projC, hintC);
const t4 = cacheSnap();
assertOcc("T4", t4);
const plateauC = plateauCheck("C", {}, projC, hintC, t4.pagesReadIntoCache);

const accountKey = Object.keys(indexSizes).find((k) => k.startsWith("account_id")) || "account_id_1";
const statusKey =
  Object.keys(indexSizes).find((k) => k.indexOf("status") >= 0 && k.indexOf("region") >= 0) ||
  "status_1_region_1";
const createdKey = Object.keys(indexSizes).find((k) => k.startsWith("created_at")) || "created_at_-1";
const idemKey =
  Object.keys(indexSizes).find((k) => k.startsWith("idempotency_key")) || "idempotency_key_1";

const indexA = indexSizes[accountKey] || 0;
const indexB = indexSizes[statusKey] || 0;
const deltaA = t2.inCache - t1.inCache;
const deltaB = t3.inCache - t2.inCache;
const deltaC = t4.inCache - t3.inCache;

const out = {
  dataSize: stats.dataSize,
  indexSizes,
  t1,
  t2,
  t3,
  t4,
  phaseA: {
    docs: nA,
    residencyBytes: deltaA,
    indexSizeBytes: indexA,
    ratio: indexA ? deltaA / indexA : null,
    pagesReadDelta: t2.pagesReadIntoCache - pagesBeforeA,
    plateau: plateauA,
    explainOk: true,
  },
  phaseB: {
    docs: nB,
    residencyBytes: deltaB,
    indexSizeBytes: indexB,
    ratio: indexB ? deltaB / indexB : null,
    plateau: plateauB,
    explainOk: true,
  },
  phaseC: {
    docs: nC,
    residencyBytes: deltaC,
    dataSizeBytes: stats.dataSize,
    ratio: stats.dataSize ? deltaC / stats.dataSize : null,
    plateau: plateauC,
    explainOk: true,
  },
  untouchedIndexSanity: {
    created_at_bytes: indexSizes[createdKey] || 0,
    idempotency_key_bytes: indexSizes[idemKey] || 0,
    note: "indexSizes are on-disk; residency growth for these should be ~0 (not queried)",
  },
  at: new Date().toISOString(),
};

print("===JSON===");
print(JSON.stringify(out, null, 1));
