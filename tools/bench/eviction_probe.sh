#!/usr/bin/env bash
# Issue #11 / T3 — write-rate vs eviction_dirty_trigger.
#
#   ./tools/bench/eviction_probe.sh
#   PROBE_ARM=update ./tools/bench/eviction_probe.sh
#   PROBE_SECONDS=20 PROBE_RATES=0.5,1,2 ./tools/bench/eviction_probe.sh  # smoke
set -euo pipefail

NAME="${PROBE_NAME:-xycalc-eviction-probe-$$-$(date +%s)}"
NET="${NAME}-net"
DRIVER="${NAME}-driver"
IMAGE="${PROBE_IMAGE:-mongo:7}"
PY_IMAGE="${PROBE_PY_IMAGE:-python:3.12-slim}"
CACHE_GB="${PROBE_CACHE_GB:-0.25}"
WRITE_BPS="${PROBE_WRITE_BPS:-4194304}"   # 4 MiB/s
WRITE_IOPS="${PROBE_WRITE_IOPS:-100}"
MEMORY="${PROBE_MEMORY:-640m}"
ARM="${PROBE_ARM:-insert}"

here="$(cd "$(dirname "$0")" && pwd)"

root_src="$(df --output=source / | tail -1)"
parent="$(lsblk -no PKNAME "$root_src" 2>/dev/null | head -1 || true)"
dev="${PROBE_DEV:-$([ -n "$parent" ] && echo "/dev/$parent" || echo "$root_src")}"
if [ ! -b "$dev" ]; then
    echo "no block device to throttle (tried '$dev'). Set PROBE_DEV=/dev/xxx." >&2
    exit 1
fi

cleanup() {
    docker rm -f "$DRIVER" "$NAME" >/dev/null 2>&1 || true
    docker network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT

if docker ps --format '{{.Names}}' | grep -q '^xycalc-eviction-probe'; then
    echo "note: another eviction_probe run is active; names are unique but devices contend." >&2
fi

{
    echo "device      $dev  ->  write ${WRITE_BPS} B/s, ${WRITE_IOPS} IOPS"
    echo "cache       ${CACHE_GB} GB wiredTiger, ${MEMORY} container"
    echo "arm         ${ARM}"
} >&2

docker network create "$NET" >/dev/null
docker run -d --name "$NAME" --network "$NET" \
    --device-write-bps  "${dev}:${WRITE_BPS}" \
    --device-write-iops "${dev}:${WRITE_IOPS}" \
    --memory "$MEMORY" --memory-swap "$MEMORY" \
    "$IMAGE" --wiredTigerCacheSizeGB "$CACHE_GB" >/dev/null

for _ in $(seq 1 40); do
    if docker exec "$NAME" mongosh --quiet --eval 'db.runCommand({ping:1})' \
        >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

docker run -d --name "$DRIVER" --network "$NET" \
    "$PY_IMAGE" sleep infinity >/dev/null
docker exec "$DRIVER" pip install --quiet --no-cache-dir pymongo >&2
docker cp "$here/eviction_probe.py" "$DRIVER:/tmp/eviction_probe.py"
docker exec \
    -e PROBE_URI="mongodb://${NAME}:27017" \
    -e PROBE_ARM="$ARM" \
    -e PROBE_SECONDS="${PROBE_SECONDS:-180}" \
    -e PROBE_RATES="${PROBE_RATES:-0.25,0.5,1,2,4,8}" \
    -e PROBE_WRITE_BPS="$WRITE_BPS" \
    -e PROBE_CACHE_GB="$CACHE_GB" \
    -e PROBE_WORKERS="${PROBE_WORKERS:-4}" \
    -e PROBE_DOC_BYTES="${PROBE_DOC_BYTES:-1024}" \
    "$DRIVER" python /tmp/eviction_probe.py
