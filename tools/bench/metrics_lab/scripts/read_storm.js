// Sequential + indexed reads so WiredTiger pulls pages. Run while Grafana
// is open on the WT board and the cgroup board. After this script, print a
// time-range URL (empty unless Prometheus scraped the run):
//
//   python ../grafana_link.py --uid xycalc-mongodb-wt --window-s 1800
//   python ../grafana_link.py --uid xycalc-wt-cgroup --window-s 1800 \
//       --var container=xycalc-lab-mongo
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
