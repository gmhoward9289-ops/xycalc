#!/usr/bin/env bash
# Issue #11 / T3 — write-rate vs eviction_dirty_trigger.
#
#   ./tools/bench/eviction_probe.sh
#   PROBE_ARM=update ./tools/bench/eviction_probe.sh
#   PROBE_SECONDS=20 PROBE_RATES=0.5,1,2 ./tools/bench/eviction_probe.sh  # smoke
set -euo pipefail

# Git Bash rewrites Unix paths into docker.exe argv; keep device paths literal.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

NAME="${PROBE_NAME:-xycalc-eviction-probe-$$-$(date +%s)}"
NET="${NAME}-net"
DRIVER="${NAME}-driver"
IMAGE="${PROBE_IMAGE:-mongo:7}"
PY_IMAGE="${PROBE_PY_IMAGE:-python:3.12-slim}"
CACHE_GB="${PROBE_CACHE_GB:-0.25}"
# Default looser than early smokes (4 MiB/s / 100 IOPS): those starved
# inserts on journal wait before dirty% could approach the 20% trigger.
WRITE_BPS="${PROBE_WRITE_BPS:-33554432}"   # 32 MiB/s
WRITE_IOPS="${PROBE_WRITE_IOPS:-800}"
MEMORY="${PROBE_MEMORY:-640m}"
ARM="${PROBE_ARM:-insert}"

here="$(cd "$(dirname "$0")" && pwd)"

root_src="$(df --output=source / | tail -1)"
parent="$(lsblk -no PKNAME "$root_src" 2>/dev/null | head -1 || true)"
dev="${PROBE_DEV:-$([ -n "$parent" ] && echo "/dev/$parent" || echo "$root_src")}"
# Git Bash / Windows hosts lack real /dev nodes; trust explicit PROBE_DEV.
if [ -z "${PROBE_DEV:-}" ] && [ ! -b "$dev" ]; then
    echo "no block device to throttle (tried '$dev'). Set PROBE_DEV=/dev/xxx." >&2
    exit 1
fi
if [ -n "${PROBE_DEV:-}" ] && [ ! -b "$dev" ]; then
    echo "note: PROBE_DEV=$dev is not a host block device; trusting Docker engine." >&2
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
    if [ "${WRITE_BPS}" = "0" ] || [ "${WRITE_IOPS}" = "0" ]; then
        echo "device      $dev  ->  write throttle OFF (PROBE_WRITE_BPS/IOPS=0)"
    else
        echo "device      $dev  ->  write ${WRITE_BPS} B/s, ${WRITE_IOPS} IOPS"
    fi
    echo "cache       ${CACHE_GB} GB wiredTiger, ${MEMORY} container"
    echo "arm         ${ARM}"
} >&2

docker network create "$NET" >/dev/null
mongo_args=(
    -d --name "$NAME" --network "$NET"
    --memory "$MEMORY" --memory-swap "$MEMORY"
)
if [ "${WRITE_BPS}" != "0" ] && [ "${WRITE_IOPS}" != "0" ]; then
    mongo_args+=(
        --device-write-bps  "${dev}:${WRITE_BPS}"
        --device-write-iops "${dev}:${WRITE_IOPS}"
    )
fi
docker run "${mongo_args[@]}" \
    "$IMAGE" --wiredTigerCacheSizeGB "$CACHE_GB" >/dev/null

for _ in $(seq 1 40); do
    if docker exec "$NAME" mongosh --quiet --eval 'db.runCommand({ping:1})' \
        >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

# Resolve mongo IP via PowerShell — Git Bash mangled go-templates / no python.
running="$(powershell.exe -NoProfile -Command "(docker inspect '$NAME' | ConvertFrom-Json).State.Status" | tr -d '\r\n')"
if [ "$running" != "running" ]; then
    echo "mongo container $NAME is not running after wait" >&2
    docker logs "$NAME" >&2 || true
    exit 1
fi
mongo_ip="$(powershell.exe -NoProfile -Command "docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' '$NAME'" | tr -d '\r\n')"
if [ -z "$mongo_ip" ]; then
    echo "could not resolve IP for $NAME" >&2
    exit 1
fi
mongo_uri="mongodb://${mongo_ip}:27017"
echo "mongo uri   $mongo_uri (name=$NAME)" >&2

docker run -d --name "$DRIVER" --network "$NET" \
    "$PY_IMAGE" sleep infinity >/dev/null
docker exec "$DRIVER" pip install --quiet --no-cache-dir pymongo >&2
# Prefer Windows path for docker.exe under Git Bash (avoid C:\c:\... mangling).
if here_win="$(cd "$(dirname "$0")" && pwd -W 2>/dev/null)"; then
    docker cp "${here_win%/}\eviction_probe.py" "$DRIVER:/tmp/eviction_probe.py"
else
    docker cp "$here/eviction_probe.py" "$DRIVER:/tmp/eviction_probe.py"
fi
docker exec \
    -e PROBE_URI="$mongo_uri" \
    -e PROBE_ARM="$ARM" \
    -e PROBE_SECONDS="${PROBE_SECONDS:-180}" \
    -e PROBE_RATES="${PROBE_RATES:-0.25,0.5,1,2,4,8}" \
    -e PROBE_WRITE_BPS="$WRITE_BPS" \
    -e PROBE_CACHE_GB="$CACHE_GB" \
    -e PROBE_WORKERS="${PROBE_WORKERS:-8}" \
    -e PROBE_DOC_BYTES="${PROBE_DOC_BYTES:-4096}" \
    -e PROBE_JOURNAL="${PROBE_JOURNAL:-0}" \
    -e PROBE_MAX_INSERT_CACHE_FRAC="${PROBE_MAX_INSERT_CACHE_FRAC:-2.0}" \
    -e PROBE_UNPACED="${PROBE_UNPACED:-0}" \
    "$DRIVER" python /tmp/eviction_probe.py
