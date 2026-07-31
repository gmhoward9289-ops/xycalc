// Generate a corpus that compresses like application data rather than like a
// lorem-ipsum file. Repetitive synthetic text compresses 10x or better and
// would make snappy look far kinder than it is on a real collection, which
// would produce a validation case that flatters the model for the wrong reason.
//
// So: high-cardinality ids, timestamps, numbers, short varied strings, and a
// few low-cardinality enum fields -- roughly the shape of an events or orders
// collection.

const db = connect("mongodb://127.0.0.1:27017/xycalcbench");
db.events.drop();

const STATUS = ["pending", "settled", "failed", "refunded", "disputed"];
const REGION = ["us-east-1", "us-west-2", "eu-central-1", "ap-southeast-2"];
const CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";

function rnd(n) {
  let s = "";
  for (let i = 0; i < n; i++) s += CHARS[Math.floor(Math.random() * CHARS.length)];
  return s;
}

const TOTAL = 500000;
const BATCH = 5000;
const t0 = Date.now();

for (let b = 0; b < TOTAL / BATCH; b++) {
  const docs = [];
  for (let i = 0; i < BATCH; i++) {
    const n = b * BATCH + i;
    docs.push({
      account_id: rnd(24),
      session: rnd(32),
      status: STATUS[n % STATUS.length],
      region: REGION[n % REGION.length],
      amount_cents: Math.floor(Math.random() * 5000000),
      latency_ms: Math.floor(Math.random() * 2000),
      retries: n % 4,
      created_at: new Date(Date.now() - Math.floor(Math.random() * 3.15e10)),
      updated_at: new Date(),
      idempotency_key: rnd(40),
      note: rnd(60) + " " + rnd(40),
      tags: [rnd(8), rnd(8), rnd(8)],
      meta: { ua: rnd(48), ip_hash: rnd(32), shard: n % 64 },
    });
  }
  db.events.insertMany(docs, { ordered: false });
  if (b % 20 === 0) print(`  ${(b * BATCH).toLocaleString()} / ${TOTAL.toLocaleString()}`);
}

// Indexes matter to this test: the model treats in-cache index bytes as
// roughly indexSize, which is its weakest inference. A collection with no
// indexes would not exercise it at all.
db.events.createIndex({ account_id: 1 });
db.events.createIndex({ created_at: -1 });
db.events.createIndex({ status: 1, region: 1 });
db.events.createIndex({ idempotency_key: 1 }, { unique: false });

print(`loaded in ${((Date.now() - t0) / 1000).toFixed(0)}s`);
