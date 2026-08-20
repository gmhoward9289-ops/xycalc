#!/bin/bash
# Force a WiredTiger cache into genuine, sustained eviction pressure, then
# check whether resident bytes settle at the documented eviction target
# (mongodb.eviction-target-pct, default 80%) rather than idly reporting it.
#
# Usage: mongodb_saturated_cache.sh <dbpath> [cache_gb] [target_gb] [port]
#   dbpath      required. WiredTiger data directory (put it on real disk --
#               this needs 2x target_gb free).
#   cache_gb    default 8. wiredTigerCacheSizeGB to pin.
#   target_gb   default 20. Collection size to seed toward -- must exceed
#               cache_gb by a healthy margin or the cache never saturates.
#   port        default 27017.
#
# Reproduces obs-mongodb-reef-bench-2026-08-19: cache_gb=8, target_gb=20.
set -euo pipefail

DBPATH="${1:?dbpath required}"
CACHE_GB="${2:-8}"
TARGET_GB="${3:-20}"
PORT="${4:-27017}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if (( TARGET_GB < CACHE_GB * 2 )); then
  echo "refusing: target_gb ($TARGET_GB) must be at least 2x cache_gb ($CACHE_GB)" \
       "or the cache will not actually saturate -- see tools/bench/README.md" >&2
  exit 1
fi

mkdir -p "$DBPATH" "$DBPATH/../log"
CONF="$DBPATH/../mongod.conf"
LOG="$DBPATH/../log/mongod.log"
: > "$LOG"

cat > "$CONF" <<EOF
storage:
  dbPath: $DBPATH
  wiredTiger:
    engineConfig:
      cacheSizeGB: $CACHE_GB
systemLog:
  destination: file
  path: $LOG
  logAppend: true
net:
  port: $PORT
  bindIp: 127.0.0.1
processManagement:
  fork: true
EOF

pkill -f "mongod.*--config $CONF" 2>/dev/null || true
sleep 1
mongod --config "$CONF"
sleep 3

echo "=== configured cache ==="
mongosh --port "$PORT" --quiet \
  --eval "db.serverStatus().wiredTiger.cache['maximum bytes configured']"

echo "=== seeding toward ${TARGET_GB}GB ==="
MONGO_BENCH_PORT="$PORT" \
MONGO_BENCH_TARGET_BYTES="$((TARGET_GB * 1000 * 1000 * 1000))" \
  mongosh --port "$PORT" --quiet --file "$HERE/mongodb_saturated_cache_seed.js"

echo "=== full scan (forces eviction, not just a count) ==="
time mongosh --port "$PORT" --quiet --eval \
  "db.getSiblingDB('bench').docs.aggregate([{\$group:{_id:null,n:{\$sum:{\$strLenBytes:'\$payload'}}}}], {allowDiskUse:true})"

echo "=== CAPTURE ==="
mongosh --port "$PORT" --quiet --eval \
  "print(JSON.stringify({stats: db.getSiblingDB('bench').stats(), cache: db.serverStatus().wiredTiger.cache, at: new Date()}))"

echo "=== sanity: was the cache actually touched? ==="
mongosh --port "$PORT" --quiet --eval \
  "const c = db.serverStatus().wiredTiger.cache; print('pages read into cache: ' + c['pages read into cache']); print('pages evicted by eviction workers: ' + c['eviction worker thread evicting pages'])"
