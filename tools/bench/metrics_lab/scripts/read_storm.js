// Sequential + indexed reads so WiredTiger pulls pages. Run while Grafana
// is open on the WT board and the cgroup board.
//
//   docker exec -i xycalc-lab-mongo mongosh --quiet < scripts/read_storm.js

const dbName = "xylab";
const collName = "events";
const seconds = Number(process.env.XYLAB_SECONDS || 45);

db = db.getSiblingDB(dbName);
const start = Date.now();
let n = 0;
while ((Date.now() - start) / 1000 < seconds) {
  db[collName].find({ account: n % 200 }).limit(50).toArray();
  if (n % 20 === 0) {
    db[collName].find({ blob: { $ne: null } }).limit(20).toArray();
  }
  n++;
}
printjson({ loops: n, seconds: (Date.now() - start) / 1000 });
