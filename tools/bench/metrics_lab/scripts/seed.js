// Sample hot collection for the metrics lab. Sized to exceed a 256 MiB
// WiredTiger cache so scans miss, without filling a laptop disk.
//
//   docker exec -i xycalc-lab-mongo mongosh --quiet < scripts/seed.js

const dbName = "xylab";
const collName = "events";
const n = Number(process.env.XYLAB_DOCS || 80000);
const pad = "x".repeat(2048);

db = db.getSiblingDB(dbName);
db[collName].drop();
db[collName].createIndex({ account: 1, ts: 1 });

const batch = 500;
for (let i = 0; i < n; i += batch) {
  const docs = [];
  const end = Math.min(i + batch, n);
  for (let j = i; j < end; j++) {
    docs.push({
      account: j % 200,
      ts: new Date(Date.now() - j * 1000),
      status: j % 7 === 0 ? "closed" : "open",
      region: ["us", "eu", "ap"][j % 3],
      blob: pad,
    });
  }
  db[collName].insertMany(docs, { ordered: false });
  if (i % 10000 === 0) print(`inserted ${i}/${n}`);
}

const s = db[collName].stats();
printjson({
  ns: `${dbName}.${collName}`,
  count: s.count,
  storageSize: s.storageSize,
  indexSize: s.totalIndexSize,
  avgObjSize: s.avgObjSize,
});
