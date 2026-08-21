#!/usr/bin/env bash
# MongoDB snappy compression probe — issue #5.
#
#   ./tools/bench/compression_probe.sh > compression.json
#   python tools/import_compression_probe.py compression.json \
#       --machine-class "Docker mongo:7.0.39, public sample dataset"
#
# Loads real (demo-curated) MongoDB sample collections into a pinned mongo:7.0.39,
# adds a secondary index, forces a checkpoint, and records dataSize/storageSize —
# the snappy ratio the corpus has only ever measured synthetically. Every guard
# from docs/plans/issue-5-real-compression-samples.md §4 runs in
# compression_probe.py before a ratio is trusted.
#
# Why mongoimport from plain JSON and not mongorestore from BSON: a BSON dump can
# carry a non-default compressor through mongorestore and silently override the
# server default. mongoimport always creates a fresh collection under the target
# server's current defaults (snappy), which is what we want to measure. The guard
# checks creationString regardless.
#
# Datasets: MongoDB's public sample collections, mirrored as plain JSON. VERIFY at
# run time that the mirror is current and its licence permits this use. Override
# PROBE_COLLECTIONS to point at your own mirror or files.
#
# Needs: Docker with network egress (to pull mongo:7.0.39 and the datasets),
# ~2 GiB free. No cloud, no cost beyond the box you already have.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
IMAGE="${PROBE_MONGO_IMAGE:-mongo:7.0.39}"
CONTAINER="${PROBE_NAME:-xycalc-compress-$$-$(date +%s)}"
OUT="${PROBE_OUT:-$(mktemp -d /tmp/compress-probe.XXXXXX)}"
PY="${PROBE_PYTHON:-python3}"
MIRROR="${PROBE_MIRROR:-https://raw.githubusercontent.com/neelabalan/mongodb-sample-dataset/main}"

# spec: "<db>.<collection>=<json-url>=<indexField>"  (indexField optional)
# A spread of real document shapes: narrative text, low-cardinality enums, deep
# nesting, geospatial/numeric. Adjust to taste; guard 3 drops anything too small.
DEFAULT_COLLECTIONS="
sample_mflix.movies=${MIRROR}/sample_mflix/movies.json=year
sample_supplies.sales=${MIRROR}/sample_supplies/sales.json=storeLocation
sample_analytics.transactions=${MIRROR}/sample_analytics/transactions.json=account_id
sample_restaurants.restaurants=${MIRROR}/sample_restaurants/restaurants.json=borough
sample_weatherdata.data=${MIRROR}/sample_weatherdata/data.json=st
"
COLLECTIONS="${PROBE_COLLECTIONS:-$DEFAULT_COLLECTIONS}"

for tool in docker curl; do
  command -v "$tool" >/dev/null 2>&1 || { echo "missing $tool" >&2; exit 3; }
done

cleanup() { docker rm -f "$CONTAINER" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "starting $IMAGE ..." >&2
docker run -d --name "$CONTAINER" "$IMAGE" >/dev/null
# wait for mongod
for _ in $(seq 1 30); do
  if docker exec "$CONTAINER" mongosh --quiet --eval 'db.runCommand({ping:1}).ok' >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

dumps=()
while read -r spec; do
  [ -z "$spec" ] && continue
  db_coll="${spec%%=*}"; rest="${spec#*=}"
  url="${rest%%=*}"; idx_field="${rest#*=}"; [ "$idx_field" = "$url" ] && idx_field=""
  db="${db_coll%%.*}"; coll="${db_coll#*.}"
  echo "=== $db.$coll ===" >&2

  local_json="$OUT/${db}.${coll}.json"
  if ! curl -fsSL "$url" -o "$local_json"; then
    echo "  download failed ($url) — skipping" >&2
    continue
  fi
  docker cp "$local_json" "$CONTAINER:/tmp/in.json" >/dev/null

  # mongoimport handles both a JSON array and one-object-per-line; try array first.
  docker exec "$CONTAINER" mongoimport --quiet --db "$db" --collection "$coll" \
      --drop --jsonArray --file /tmp/in.json >/dev/null 2>&1 \
    || docker exec "$CONTAINER" mongoimport --quiet --db "$db" --collection "$coll" \
      --drop --file /tmp/in.json >/dev/null 2>&1 \
    || { echo "  mongoimport failed — skipping" >&2; continue; }

  # The mongosh script: index, checkpoint (pre/post storageSize), scan, dump.
  dump="$OUT/${db}.${coll}.dump.json"
  docker exec "$CONTAINER" mongosh --quiet "$db" --eval "
    var coll = db.getCollection('$coll');
    var idxField = '$idx_field';
    if (idxField) { var s = {}; s[idxField] = 1; coll.createIndex(s); }
    var pre = coll.stats().storageSize;
    db.adminCommand({fsync: 1});
    var st = coll.stats();
    var post = st.storageSize;
    // full collection scan to pull pages into cache, then one scan per index
    coll.find().hint({\$natural: 1}).forEach(function(){});
    coll.getIndexes().forEach(function(ix){ try { coll.find().hint(ix.key).forEach(function(){}); } catch(e){} });
    var cache = db.serverStatus().wiredTiger.cache;
    print(JSON.stringify({
      collection: '$db.$coll',
      version: db.version(),
      at: new Date(),
      count: st.count,
      data_size: st.size,
      index_size: st.totalIndexSize,
      index_count: coll.getIndexes().length,
      storage_size_precheckpoint: pre,
      storage_size_postcheckpoint: post,
      creation_string: (st.wiredTiger && st.wiredTiger.creationString) || '',
      cache_bytes_in: cache['bytes currently in the cache']
    }));
  " > "$dump" 2>/dev/null || { echo "  dump failed — skipping" >&2; continue; }

  # mongosh may print NumberLong(...) wrappers; the importer/num-reader handles
  # dicts, but keep the harness output plain by stripping the wrappers here.
  "$PY" - "$dump" <<'PYCLEAN' || true
import re, sys, pathlib
p = pathlib.Path(sys.argv[1])
t = p.read_text(encoding="utf-8")
t = re.sub(r"NumberLong\((\-?\d+)\)", r"\1", t)
t = re.sub(r'NumberLong\("(\-?\d+)"\)', r"\1", t)
p.write_text(t, encoding="utf-8")
PYCLEAN
  dumps+=("$dump")
done <<< "$COLLECTIONS"

if [ "${#dumps[@]}" -eq 0 ]; then
  echo "no collections dumped — check the mirror URLs / network" >&2
  exit 4
fi

echo "evaluating ${#dumps[@]} collection(s) ..." >&2
exec "$PY" "$here/compression_probe.py" "${dumps[@]}" --machine-class "${PROBE_MACHINE:-Docker $IMAGE}"
