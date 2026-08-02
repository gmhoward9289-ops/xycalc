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

// CodeQL flags this as js/insecure-randomness (alert #4, HIGH), because
// Math.random() flows into fields named `session` and `idempotency_key`, and
// those names read as security contexts. The rule is well aimed even though
// nothing here is exploitable: these documents exist only to occupy pages in a
// throwaway container, nothing authenticates against them, and the container is
// destroyed when the benchmark ends.
//
// The names are kept rather than renamed because this generator produced a
// benchmark the corpus CITES (obs-mongodb-swamplink-bench-2026-07-31), and BSON
// stores field names per document -- renaming them changes storage size and
// stops future runs being comparable to the recorded one.
//
// SO: do not lift this function to seed a development or staging environment.
// It produces predictable values in fields whose names promise unpredictability,
// which is the shape of a real vulnerability even though this instance is not
// one.
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
