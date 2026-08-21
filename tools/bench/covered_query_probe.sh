#!/usr/bin/env bash
# Issue #13 / T5 — covered vs fetch cache residency (mongosh + mongodb_load.js).
#
#   ./tools/bench/covered_query_probe.sh
#   PROBE_DOCS=50000 PROBE_CACHE_GB=1 ./tools/bench/covered_query_probe.sh  # smoke
set -euo pipefail

NAME="${PROBE_NAME:-xycalc-covered-probe-$$-$(date +%s)}"
IMAGE="${PROBE_IMAGE:-mongo:7}"
MEMORY="${PROBE_MEMORY:-1g}"
CACHE_GB="${PROBE_CACHE_GB:-1}"

here="$(cd "$(dirname "$0")" && pwd)"

cleanup() {
    docker rm -f "$NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

if docker ps --format '{{.Names}}' | grep -q '^xycalc-covered-probe'; then
    echo "note: another covered_query_probe is active; names are unique." >&2
fi

echo "container $NAME  cache=${CACHE_GB}G memory=${MEMORY}" >&2
docker run -d --name "$NAME" \
    --memory "$MEMORY" --memory-swap "$MEMORY" \
    "$IMAGE" --wiredTigerCacheSizeGB "$CACHE_GB" >/dev/null

for _ in $(seq 1 40); do
    if docker exec "$NAME" mongosh --quiet --eval 'db.runCommand({ping:1})' \
        >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

# Load uses mongodb_load.js (500k default). For smoke, patch TOTAL via env copy.
docker cp "$here/mongodb_load.js" "$NAME:/tmp/mongodb_load.js"
if [ -n "${PROBE_DOCS:-}" ] && [ "${PROBE_DOCS}" != "500000" ]; then
    docker exec "$NAME" bash -lc \
        "sed -i 's/const TOTAL = 500000;/const TOTAL = ${PROBE_DOCS};/' /tmp/mongodb_load.js"
fi

echo "loading dataset..." >&2
docker exec "$NAME" mongosh --quiet /tmp/mongodb_load.js >&2

echo "restarting mongod to clear WT cache..." >&2
docker restart "$NAME" >/dev/null
for _ in $(seq 1 40); do
    if docker exec "$NAME" mongosh --quiet --eval 'db.runCommand({ping:1})' \
        >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

docker cp "$here/covered_query_probe.js" "$NAME:/tmp/covered_query_probe.js"
docker exec \
    -e PROBE_OCCUPANCY_ABORT_PCT="${PROBE_OCCUPANCY_ABORT_PCT:-70}" \
    "$NAME" mongosh --quiet /tmp/covered_query_probe.js
