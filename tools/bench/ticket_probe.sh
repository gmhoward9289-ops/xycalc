#!/usr/bin/env bash
# Build a deliberately I/O-starved MongoDB and run tools/bench/ticket_probe.py
# against it, to settle investigation 003's open question.
#
#   ./tools/bench/ticket_probe.sh                        # full run, ~4 minutes
#   PROBE_SECONDS=6 PROBE_DOCS=30000 ./ticket_probe.sh   # smoke run
#
# Throttling is scoped to THIS CONTAINER ONLY, via the block-IO cgroup. The host
# may be serving other things; saturating its disk to answer a question about
# MongoDB would be an outage caused by curiosity. Scoping also makes the induced
# latency a set quantity rather than a side effect of whatever else was running.
#
# Two containers on a private network: mongod, and a driver with pymongo. The
# driver is NOT mongosh — mongosh auto-awaits its calls, so N "concurrent"
# operations there execute serially and measure nothing. Real OS threads are the
# only way to fill a ticket pool.
#
# Prints the probe's JSON to stdout after a ===JSON=== marker. Removes both
# containers and the network on exit, success or failure.
set -euo pipefail

NAME="${PROBE_NAME:-xycalc-ticket-probe}"
NET="${NAME}-net"
DRIVER="${NAME}-driver"
IMAGE="${PROBE_IMAGE:-mongo:7}"
PY_IMAGE="${PROBE_PY_IMAGE:-python:3.12-slim}"

# Small enough that a random point lookup misses and must reach the device.
# The experiment is vacuous if the working set fits.
CACHE_GB="${PROBE_CACHE_GB:-0.25}"
READ_BPS="${PROBE_READ_BPS:-8388608}"     # 8 MiB/s
READ_IOPS="${PROBE_READ_IOPS:-150}"
# Bounds the container's PAGE CACHE, not just its heap. Without this the host
# serves nearly every read from its own page cache, the block-IO throttle never
# engages, and the probe reports a healthy database on a "throttled" disk --
# which is exactly what the second smoke run did: 2.3x oversubscription against
# the WiredTiger cache, and still zero pressure, because 605 MB of data fits in
# the host's free RAM. cgroup limits apply to device traffic; a page-cache hit
# is not device traffic.
MEMORY="${PROBE_MEMORY:-640m}"

here="$(cd "$(dirname "$0")" && pwd)"

# The block-IO cgroup limit needs a whole device, not a partition.
root_src="$(df --output=source / | tail -1)"
parent="$(lsblk -no PKNAME "$root_src" 2>/dev/null | head -1 || true)"
dev="${PROBE_DEV:-$([ -n "$parent" ] && echo "/dev/$parent" || echo "$root_src")}"
if [ ! -b "$dev" ]; then
    echo "no block device to throttle (tried '$dev' from '$root_src')." >&2
    echo "Set PROBE_DEV=/dev/xxx explicitly." >&2
    exit 1
fi

cleanup() {
    docker rm -f "$DRIVER" "$NAME" >/dev/null 2>&1 || true
    docker network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT
cleanup

{
    echo "device      $dev  ->  ${READ_BPS} B/s, ${READ_IOPS} IOPS (this container only)"
    echo "cache       ${CACHE_GB} GB wiredTiger, ${MEMORY} container (bounds page cache)"
    echo "workload    ${PROBE_DOCS:-1500000} docs, ${PROBE_SECONDS:-25}s per level"
} >&2

docker network create "$NET" >/dev/null

docker run -d --name "$NAME" --network "$NET" \
    --device-read-bps  "${dev}:${READ_BPS}" \
    --device-read-iops "${dev}:${READ_IOPS}" \
    --memory "$MEMORY" --memory-swap "$MEMORY" \
    "$IMAGE" --wiredTigerCacheSizeGB "$CACHE_GB" >/dev/null

# Wait for it to accept connections rather than sleeping and hoping.
for _ in $(seq 1 40); do
    if docker exec "$NAME" mongosh --quiet --eval 'db.runCommand({ping:1})' \
        >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

docker run -d --name "$DRIVER" --network "$NET" \
    -e PROBE_URI="mongodb://${NAME}:27017" \
    -e PROBE_SECONDS="${PROBE_SECONDS:-25}" \
    -e PROBE_DOCS="${PROBE_DOCS:-1500000}" \
    -e PROBE_LEVELS="${PROBE_LEVELS:-1,2,4,8,16,32,64}" \
    "$PY_IMAGE" sleep infinity >/dev/null

docker exec "$DRIVER" pip install --quiet --no-cache-dir pymongo >&2
docker cp "$here/ticket_probe.py" "$DRIVER:/tmp/ticket_probe.py"
docker exec \
    -e PROBE_URI="mongodb://${NAME}:27017" \
    -e PROBE_SECONDS="${PROBE_SECONDS:-25}" \
    -e PROBE_DOCS="${PROBE_DOCS:-1500000}" \
    -e PROBE_LEVELS="${PROBE_LEVELS:-1,2,4,8,16,32,64}" \
    "$DRIVER" python /tmp/ticket_probe.py
