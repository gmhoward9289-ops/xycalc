#!/usr/bin/env bash
# T2 / issue #10 — compression ratio as a function of document shape.
#
# Five synthetic shapes × snappy/zstd/zlib on one unthrottled mongod.
# Same JSONL bytes per shape across the three compressor arms (no RNG
# confound). Guards live in compression_shape_probe.py.
#
#   ./tools/bench/compression_shape_probe.sh > shape-sweep.json
#   PROBE_TARGET_BYTES=50000000 ./tools/bench/compression_shape_probe.sh  # smoke
#
# Needs: Docker, python3, gzip. ~5 GB free for the default 300 MB×5×3 load.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
IMAGE="${PROBE_MONGO_IMAGE:-mongo:7.0.39}"
CONTAINER="${PROBE_NAME:-xycalc-shape-$$-$(date +%s)}"
PY="${PROBE_PYTHON:-python3}"
WORK="${PROBE_WORK:-$(mktemp -d /tmp/compress-shape.XXXXXX)}"
TARGET_BYTES="${PROBE_TARGET_BYTES:-300000000}"

SHAPES=(pure-random random-repeated-fields low-cardinality-enums realistic-mixed near-duplicate)
COMPRESSORS=(snappy zstd zlib)

for tool in docker "$PY"; do
  command -v "$tool" >/dev/null 2>&1 || { echo "missing $tool" >&2; exit 3; }
done

cleanup() { docker rm -f "$CONTAINER" >/dev/null 2>&1 || true; }
trap cleanup EXIT

export PROBE_TARGET_BYTES="$TARGET_BYTES"
echo "generating shapes under $WORK (target ${TARGET_BYTES} B each) ..." >&2
"$PY" "$here/compression_shape_probe.py" generate --out "$WORK/shapes" >/dev/null

echo "starting $IMAGE ..." >&2
docker run -d --name "$CONTAINER" ${PROBE_DOCKER_NETWORK:+--network "$PROBE_DOCKER_NETWORK"} "$IMAGE" >/dev/null
for _ in $(seq 1 60); do
  if docker exec "$CONTAINER" mongosh --quiet --eval 'db.runCommand({ping:1}).ok' >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

results="$WORK/results.jsonl"
: > "$results"

for shape in "${SHAPES[@]}"; do
  jsonl="$WORK/shapes/${shape}.jsonl"
  docker cp "$jsonl" "$CONTAINER:/tmp/${shape}.jsonl" >/dev/null
  for comp in "${COMPRESSORS[@]}"; do
    db="cprobe_${shape//-/_}_${comp}"
    echo "=== $shape / $comp → db=$db ===" >&2
    docker exec "$CONTAINER" mongosh --quiet --eval "
      db = db.getSiblingDB('$db');
      db.docs.drop();
      db.createCollection('docs', {
        storageEngine: { wiredTiger: { configString: 'block_compressor=$comp' } }
      });
    " >/dev/null
    docker exec "$CONTAINER" mongoimport --quiet --db "$db" --collection docs \
      --file "/tmp/${shape}.jsonl" >/dev/null
  done
done

echo "checkpoint ..." >&2
docker exec "$CONTAINER" mongosh --quiet --eval 'db.adminCommand({fsync:1})' >/dev/null

for shape in "${SHAPES[@]}"; do
  for comp in "${COMPRESSORS[@]}"; do
    db="cprobe_${shape//-/_}_${comp}"
    docker exec "$CONTAINER" mongosh --quiet --eval "
      db = db.getSiblingDB('$db');
      var st = db.docs.stats();
      var cs = (st.wiredTiger && st.wiredTiger.creationString) || '';
      print(JSON.stringify({
        shape: '$shape',
        compressor: '$comp',
        db: '$db',
        version: db.version(),
        count: st.count,
        data_size: st.size,
        storage_size: st.storageSize,
        creation_string: cs
      }));
    " >> "$results"
  done
done

echo "evaluating ..." >&2
summary="$WORK/summary.json"
"$PY" "$here/compression_shape_probe.py" evaluate "$results" \
  --shapes-meta "$WORK/shapes/shapes.json" | tee "$summary" >&2

# Re-print only the JSON document on stdout for piping.
awk 'BEGIN{p=0} /^===JSON===$/{p=1; next} p{print}' "$summary"

echo "work dir kept at $WORK (container removed on exit)" >&2
