#!/bin/bash
# Validate mongodb.host-ram: start mongod with NO wiredTigerCacheSizeGB
# override, and check that the cache it configures for itself matches the
# documented default split (50% of RAM - 1GB) against the host RAM MongoDB
# itself sees. No dataset needed -- the default split is computed from
# visible RAM at startup, independent of data volume.
#
# Usage: mongodb_default_split.sh <dbpath> [port]
set -euo pipefail

DBPATH="${1:?dbpath required}"
PORT="${2:-27017}"

mkdir -p "$DBPATH" "$DBPATH/../log"
CONF="$DBPATH/../mongod-default.conf"
LOG="$DBPATH/../log/mongod-default.log"
: > "$LOG"

cat > "$CONF" <<EOF
storage:
  dbPath: $DBPATH
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

echo "=== host RAM MongoDB itself sees ==="
mongosh --port "$PORT" --quiet --eval "db.hostInfo().system.memSizeMB"

echo "=== default-configured WT cache ==="
mongosh --port "$PORT" --quiet \
  --eval "db.serverStatus().wiredTiger.cache['maximum bytes configured']"

echo "=== check: cache should equal 0.5 * (RAM - 1GB) exactly ==="
mongosh --port "$PORT" --quiet --eval '
  const memBytes = db.hostInfo().system.memSizeMB * 1024 * 1024;
  const cache = db.serverStatus().wiredTiger.cache["maximum bytes configured"];
  const expected = 0.5 * (memBytes - 1024*1024*1024);
  print("memBytes: " + memBytes);
  print("cache:    " + cache);
  print("expected: " + expected);
  print("diff:     " + (cache - expected) + " bytes");
'

pkill -f "mongod.*--config $CONF" 2>/dev/null || true
