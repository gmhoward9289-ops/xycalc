#!/usr/bin/env bash
# Build a deliberately I/O-starved MongoDB and run tools/bench/ticket_probe.py
# against it, to settle investigation 003's open question.
#
#   ./tools/bench/ticket_probe.sh                        # full run, ~4 minutes
#   PROBE_SECONDS=6 PROBE_DOCS=30000 ./ticket_probe.sh   # smoke run
#
# Issue #3 residual (convergence series + cooldown):
#   PROBE_LEVELS=64,150 PROBE_SECONDS=600 \
#   PROBE_COOLDOWN_SECONDS=900 PROBE_COOLDOWN_HEARTBEAT_HZ=0 \
#     ./tools/bench/ticket_probe.sh > /tmp/ticket-probe-idle.json
#   PROBE_LEVELS=64,150 PROBE_SECONDS=600 \
#   PROBE_COOLDOWN_SECONDS=900 PROBE_COOLDOWN_HEARTBEAT_HZ=1 \
#     ./tools/bench/ticket_probe.sh > /tmp/ticket-probe-trickle.json
#
# Issue #12 / T4 (checkpoint sawtooth soak, 1s buckets):
#   PROBE_MODE=timeseries PROBE_LEVELS=8 PROBE_SECONDS=480 \
#     ./tools/bench/ticket_probe.sh > /tmp/ticket-probe-timeseries.json
#   # smoke: PROBE_MODE=timeseries PROBE_LEVELS=8 PROBE_SECONDS=30 PROBE_DOCS=30000 ...
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

# Git Bash (MSYS) rewrites absolute Unix paths on docker.exe argv
# (/tmp/foo -> C:/Users/.../Temp/foo). Disable that for this script.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

# Unique per run. The names used to be a fixed default, and `cleanup` runs at
# STARTUP as well as on exit -- so a second run beginning while a first was
# still going would docker-rm the first one's containers out from under it.
# That happened on 2026-08-01: two sessions probed this box within minutes of
# each other, one run died mid-load, and it was initially misdiagnosed as an
# ssh teardown killing the process. A benchmark harness that silently destroys
# a concurrent benchmark is worse than one that refuses to start.
NAME="${PROBE_NAME:-xycalc-ticket-probe-$$-$(date +%s)}"
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
# Prefer a Windows path for docker.exe host-side args under Git Bash.
if here_win="$(cd "$(dirname "$0")" && pwd -W 2>/dev/null)"; then
    :
elif command -v cygpath >/dev/null 2>&1; then
    here_win="$(cygpath -w "$here")"
else
    here_win="$here"
fi
probe_py="${here_win%/}\\ticket_probe.py"
# Fallback when not on Windows / pwd -W unavailable.
if [ ! -f "$probe_py" ] && [ ! -f "${here}/ticket_probe.py" ]; then
    echo "cannot find ticket_probe.py next to $0" >&2
    exit 1
fi
if [ ! -f "$probe_py" ]; then
    probe_py="${here}/ticket_probe.py"
fi
# The block-IO cgroup limit needs a whole device, not a partition.
root_src="$(df --output=source / | tail -1)"
parent="$(lsblk -no PKNAME "$root_src" 2>/dev/null | head -1 || true)"
dev="${PROBE_DEV:-$([ -n "$parent" ] && echo "/dev/$parent" || echo "$root_src")}"
# Git Bash / Windows hosts have no real /dev block nodes even when Docker
# Desktop's Linux VM does. An explicit PROBE_DEV is trusted; docker will fail
# loudly if the device is wrong inside the engine.
if [ -z "${PROBE_DEV:-}" ] && [ ! -b "$dev" ]; then
    echo "no block device to throttle (tried '$dev' from '$root_src')." >&2
    echo "Set PROBE_DEV=/dev/xxx explicitly." >&2
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

# Deliberately NOT a blanket cleanup of a shared name at startup. The names are
# unique to this run, so there is nothing of ours to clear, and anything
# matching an older pattern belongs to somebody else's run.
if docker ps --format '{{.Names}}' | grep -q '^xycalc-ticket-probe'; then
    echo "note: another ticket_probe run is active on this host. Proceeding —" >&2
    echo "      names are unique per run — but the two will contend for the" >&2
    echo "      same device and CPU, so neither result is cleanly isolated." >&2
fi

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

# Resolving the mongo endpoint differs by host, and getting this wrong is
# fatal rather than degraded, so branch explicitly instead of assuming.
#
# Git Bash mangles docker's go-template argv and has no python, so there we
# shell out to powershell.exe to inspect the container. powershell.exe is NOT
# on PATH under WSL or on Linux, where an unconditional call aborts the run
# with "command not found" -- which is exactly what it did on reef. On those
# hosts docker's embedded DNS resolves the container name on the user-defined
# network, which is what the harness did before Windows support existed.
if command -v powershell.exe >/dev/null 2>&1; then
    running="$(powershell.exe -NoProfile -Command "(docker inspect '$NAME' | ConvertFrom-Json).State.Status" | tr -d '
')"
    if [ "$running" != "running" ]; then
        echo "mongo container $NAME is not running after wait" >&2
        docker logs "$NAME" >&2 || true
        exit 1
    fi
    mongo_ip="$(powershell.exe -NoProfile -Command "docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' '$NAME'" | tr -d '
')"
    if [ -z "$mongo_ip" ]; then
        echo "could not resolve IP for $NAME" >&2
        exit 1
    fi
    mongo_uri="mongodb://${mongo_ip}:27017"
else
    running="$(docker inspect -f '{{.State.Status}}' "$NAME" 2>/dev/null || true)"
    if [ "$running" != "running" ]; then
        echo "mongo container $NAME is not running after wait" >&2
        docker logs "$NAME" >&2 || true
        exit 1
    fi
    mongo_uri="mongodb://${NAME}:27017"
fi
echo "mongo uri   $mongo_uri (name=$NAME)" >&2

docker run -d --name "$DRIVER" --network "$NET" \
    -e PROBE_URI="$mongo_uri" \
    -e PROBE_SECONDS="${PROBE_SECONDS:-25}" \
    -e PROBE_DOCS="${PROBE_DOCS:-1500000}" \
    -e PROBE_LEVELS="${PROBE_LEVELS:-1,2,4,8,16,32,64}" \
    -e PROBE_MODE="${PROBE_MODE:-levels}" \
    -e PROBE_SAMPLE_S="${PROBE_SAMPLE_S:-}" \
    -e PROBE_MIN_CHECKPOINTS="${PROBE_MIN_CHECKPOINTS:-4}" \
    -e PROBE_COOLDOWN_SECONDS="${PROBE_COOLDOWN_SECONDS:-0}" \
    -e PROBE_COOLDOWN_HEARTBEAT_HZ="${PROBE_COOLDOWN_HEARTBEAT_HZ:-0}" \
    -e PROBE_CONVERGENCE_WINDOW_S="${PROBE_CONVERGENCE_WINDOW_S:-90}" \
    -e PROBE_CONVERGENCE_TOL="${PROBE_CONVERGENCE_TOL:-0.05}" \
    "$PY_IMAGE" sleep infinity >/dev/null

docker exec "$DRIVER" pip install --quiet --no-cache-dir pymongo >&2
docker cp "$probe_py" "${DRIVER}:/tmp/ticket_probe.py"
# mongo_tickets.py must sit beside the probe in /tmp. Copy from a cwd that
# is the bench dir so Git Bash / docker.exe path conversion cannot invent C:\c:\...
copied_tickets=0
if [ -f "${here}/mongo_tickets.py" ]; then
    (cd "$here" && docker cp mongo_tickets.py "${DRIVER}:/tmp/mongo_tickets.py") && copied_tickets=1
fi
if [ "$copied_tickets" -ne 1 ]; then
    echo "REFUSING: mongo_tickets.py not copied into driver (here=$here)" >&2
    ls -la "$here/mongo_tickets.py" >&2 || true
    exit 1
fi

# Detached exec + file capture: Docker Desktop named-pipe `docker exec`
# often 500s mid-run on long probes. Poll files instead.
env_args=(
    -e "PROBE_URI=$mongo_uri"
    -e "PROBE_SECONDS=${PROBE_SECONDS:-25}"
    -e "PROBE_DOCS=${PROBE_DOCS:-1500000}"
    -e "PROBE_LEVELS=${PROBE_LEVELS:-1,2,4,8,16,32,64}"
    -e "PROBE_MODE=${PROBE_MODE:-levels}"
    -e "PROBE_SAMPLE_S=${PROBE_SAMPLE_S:-}"
    -e "PROBE_MIN_CHECKPOINTS=${PROBE_MIN_CHECKPOINTS:-4}"
    -e "PROBE_COOLDOWN_SECONDS=${PROBE_COOLDOWN_SECONDS:-0}"
    -e "PROBE_COOLDOWN_HEARTBEAT_HZ=${PROBE_COOLDOWN_HEARTBEAT_HZ:-0}"
    -e "PROBE_CONVERGENCE_WINDOW_S=${PROBE_CONVERGENCE_WINDOW_S:-90}"
    -e "PROBE_CONVERGENCE_TOL=${PROBE_CONVERGENCE_TOL:-0.05}"
)
docker exec -d -w /tmp "${env_args[@]}" "$DRIVER" \
    sh -c 'python /tmp/ticket_probe.py > /tmp/probe.out 2>/tmp/probe.err; echo $? > /tmp/probe.exit'
echo "probe launched detached in $DRIVER; polling /tmp/probe.exit ..." >&2
while ! docker exec "$DRIVER" test -f /tmp/probe.exit >/dev/null 2>&1; do
    # stream latest stderr lines so the operator sees progress
    docker exec "$DRIVER" sh -c 'tail -n 3 /tmp/probe.err 2>/dev/null' >&2 || true
    sleep 15
done
exit_code="$(docker exec "$DRIVER" cat /tmp/probe.exit | tr -d '\r\n')"
docker exec "$DRIVER" cat /tmp/probe.err >&2 || true
docker exec "$DRIVER" cat /tmp/probe.out || true
exit "${exit_code:-1}"

