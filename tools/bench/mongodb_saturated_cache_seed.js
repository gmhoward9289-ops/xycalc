// Seed a collection well past a target WiredTiger cache size, so a full scan
// afterward genuinely saturates the cache rather than comfortably fitting in
// it. See mongodb_saturated_cache.sh for how this gets invoked.
//
// Payload is a shuffled slice of a pre-generated random pool rather than
// fresh per-character randomness -- generating true per-document randomness
// at these volumes is the bottleneck, not the database. This makes the data
// close to incompressible (worth knowing when reading storageSize back: it
// will NOT show a realistic snappy ratio, only obs-mongodb-swamplink-
// bench-2026-07-31's rnd() generator is tuned for that).
//
// TARGET_BYTES and PORT come from environment variables so the same script
// serves both a quick smoke run and the full saturated-cache benchmark.

const PORT = parseInt(process.env.MONGO_BENCH_PORT || "27017", 10);
const TARGET_BYTES = parseInt(
  process.env.MONGO_BENCH_TARGET_BYTES || String(2 * 1000 * 1000 * 1000),
  10
);

const db = connect(`mongodb://127.0.0.1:${PORT}/bench`);
db.docs.drop();
db.docs.createIndex({ k: 1 });

const POOL_SIZE = 5 * 1000 * 1000;
const CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
let pool = "";
{
  const chunk = [];
  for (let i = 0; i < 65536; i++) chunk.push(CHARS[Math.floor(Math.random() * 62)]);
  const chunkStr = chunk.join("");
  while (pool.length < POOL_SIZE) pool += chunkStr;
  pool = pool.slice(0, POOL_SIZE);
}

const DOC_PAYLOAD_BYTES = 20000;
const BATCH = 500;
let n = 0;
const start = new Date();

while (true) {
  const docs = [];
  for (let i = 0; i < BATCH; i++) {
    const off = Math.floor(Math.random() * (POOL_SIZE - DOC_PAYLOAD_BYTES));
    docs.push({
      k: n + i,
      ts: new Date(),
      payload: (n + i) + ":" + pool.substr(off, DOC_PAYLOAD_BYTES),
    });
  }
  db.docs.insertMany(docs, { ordered: false });
  n += BATCH;

  if (n % 50000 === 0) {
    const stats = db.stats();
    const elapsedSec = (new Date() - start) / 1000;
    print(JSON.stringify({
      docs: n,
      dataSize: stats.dataSize,
      elapsedSec: elapsedSec,
      rateMBps: (stats.dataSize / 1e6 / elapsedSec).toFixed(1),
    }));
    if (stats.dataSize >= TARGET_BYTES) break;
  }
}

print("SEED_DONE " + JSON.stringify(db.stats()));
