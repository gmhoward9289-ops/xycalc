#!/usr/bin/env bash
# T1 / issue #9 — WiredTiger cache-cliff sweep.
#
# Fresh mongod per oversubscription ratio. Fixed cache, throttle, and
# container memory; dataset size is the sweep dimension. Concurrency=1 so
# ticket-pool queueing cannot look like a cache knee.
#
#   ./tools/bench/cache_cliff_probe.sh                         # full 8-ratio sweep
#   PROBE_RATIOS=1.0,2.0 PROBE_SECONDS=6 ./tools/bench/cache_cliff_probe.sh  # smoke
#
# Tries direct_io=[data] first (host page cache structurally bypassed). If
# mongod will not start, falls back without it and records directIo=false.
# Above 1.0x, compares cgroup device read bytes to pagesReadIntoCache ×
# page size; a near-zero device delta with rising WT pages fails the leg.
#
# Prints one JSON document to stdout (array of legs). Needs Linux + Docker
# with a real block device (same class of host as ticket_probe.sh).
set -euo pipefail

NAME_PREFIX="${PROBE_NAME:-xycalc-cache-cliff-$$-$(date +%s)}"
IMAGE="${PROBE_IMAGE:-mongo:7}"
PY_IMAGE="${PROBE_PY_IMAGE:-python:3.12-slim}"

CACHE_GB="${PROBE_CACHE_GB:-0.25}"
READ_BPS="${PROBE_READ_BPS:-8388608}"     # 8 MiB/s
READ_IOPS="${PROBE_READ_IOPS:-150}"
MEMORY="${PROBE_MEMORY:-640m}"
SECONDS_PER="${PROBE_SECONDS:-25}"
CONCURRENCY="${PROBE_CONCURRENCY:-1}"
# 50x/100x added per issue #9 comment (10–1000 GB working sets are normal;
# the interesting region is far above 1.0x, not only the knee neighbourhood).
RATIOS_CSV="${PROBE_RATIOS:-0.5,0.8,1.0,1.2,1.5,2,4,8,50,100}"
RATIO_TOLERANCE="${PROBE_RATIO_TOLERANCE:-0.10}"
WT_PAGE_SIZE="${PROBE_WT_PAGE_SIZE:-32768}"
# Device bytes must be at least this fraction of (pages × page_size) above 1.0x.
DEVICE_BYTE_MIN_FRAC="${PROBE_DEVICE_BYTE_MIN_FRAC:-0.10}"

here="$(cd "$(dirname "$0")" && pwd)"

root_src="$(df --output=source / | tail -1)"
parent="$(lsblk -no PKNAME "$root_src" 2>/dev/null | head -1 || true)"
dev="${PROBE_DEV:-$([ -n "$parent" ] && echo "/dev/$parent" || echo "$root_src")}"
if [ ! -b "$dev" ]; then
    echo "no block device to throttle (tried '$dev' from '$root_src')." >&2
    echo "Set PROBE_DEV=/dev/xxx explicitly. This harness is for Linux/Docker." >&2
    exit 1
fi

ACTIVE_MONGO=""
ACTIVE_DRIVER=""
ACTIVE_NET=""

cleanup_leg() {
    if [ -n "$ACTIVE_DRIVER" ]; then
        docker rm -f "$ACTIVE_DRIVER" >/dev/null 2>&1 || true
    fi
    if [ -n "$ACTIVE_MONGO" ]; then
        docker rm -f "$ACTIVE_MONGO" >/dev/null 2>&1 || true
    fi
    if [ -n "$ACTIVE_NET" ]; then
        docker network rm "$ACTIVE_NET" >/dev/null 2>&1 || true
    fi
    ACTIVE_MONGO=""
    ACTIVE_DRIVER=""
    ACTIVE_NET=""
}
trap cleanup_leg EXIT

if docker ps --format '{{.Names}}' | grep -qE '^xycalc-(ticket|cache-cliff)-probe'; then
    echo "note: another xycalc probe is active on this host. Proceeding —" >&2
    echo "      names are unique per run — but results will contend for the device." >&2
fi

IFS=',' read -r -a RATIOS <<< "$RATIOS_CSV"

{
    echo "device      $dev  ->  ${READ_BPS} B/s, ${READ_IOPS} IOPS (this container only)"
    echo "cache       ${CACHE_GB} GB wiredTiger, ${MEMORY} container (fixed at every ratio)"
    echo "workload    ratios=[${RATIOS_CSV}]  concurrency=${CONCURRENCY}  ${SECONDS_PER}s/leg"
} >&2

# --- helpers ---------------------------------------------------------------

cgroup_read_bytes() {
    # Prefer cgroup v2 io.stat inside the container; fall back to v1 throttle
    # service bytes. Returns a single integer (sum of rbytes / Read) or 0.
    local ctn="$1"
    local out
    out="$(docker exec "$ctn" sh -c '
        if [ -r /sys/fs/cgroup/io.stat ]; then
            awk "{
                for (i=1;i<=NF;i++) {
                    if (\$i ~ /^rbytes=/) { split(\$i,a,\"=\"); s+=a[2] }
                }
            } END { print s+0 }" /sys/fs/cgroup/io.stat
        elif [ -r /sys/fs/cgroup/blkio.throttle.io_service_bytes ]; then
            awk "/Read/ { s+=\$3 } END { print s+0 }" \
                /sys/fs/cgroup/blkio.throttle.io_service_bytes
        elif [ -r /sys/fs/cgroup/blkio/blkio.throttle.io_service_bytes ]; then
            awk "/Read/ { s+=\$3 } END { print s+0 }" \
                /sys/fs/cgroup/blkio/blkio.throttle.io_service_bytes
        else
            echo 0
        fi
    ' 2>/dev/null || echo 0)"
    echo "${out:-0}"
}

start_mongod() {
    # Sets ACTIVE_MONGO, ACTIVE_NET, DIRECT_IO (true|false).
    local name="$1"
    local net="${name}-net"
    local try_direct="${2:-true}"

    docker network create "$net" >/dev/null
    ACTIVE_NET="$net"

    local args=(
        run -d --name "$name" --network "$net"
        --device-read-bps  "${dev}:${READ_BPS}"
        --device-read-iops "${dev}:${READ_IOPS}"
        --memory "$MEMORY" --memory-swap "$MEMORY"
    )

    if [ "$try_direct" = true ]; then
        if docker "${args[@]}" "$IMAGE" \
            --wiredTigerCacheSizeGB "$CACHE_GB" \
            --wiredTigerEngineConfigString='direct_io=[data]' \
            >/dev/null 2>/tmp/cache-cliff-mongo-start.err; then
            DIRECT_IO=true
        else
            echo "direct_io start failed; falling back without it:" >&2
            sed 's/^/  /' /tmp/cache-cliff-mongo-start.err >&2 || true
            docker rm -f "$name" >/dev/null 2>&1 || true
            docker "${args[@]}" "$IMAGE" \
                --wiredTigerCacheSizeGB "$CACHE_GB" >/dev/null
            DIRECT_IO=false
        fi
    else
        docker "${args[@]}" "$IMAGE" \
            --wiredTigerCacheSizeGB "$CACHE_GB" >/dev/null
        DIRECT_IO=false
    fi
    ACTIVE_MONGO="$name"

    local ok=0
    for _ in $(seq 1 40); do
        if docker exec "$name" mongosh --quiet --eval 'db.runCommand({ping:1})' \
            >/dev/null 2>&1; then
            ok=1
            break
        fi
        sleep 1
    done
    if [ "$ok" -ne 1 ]; then
        echo "mongod did not accept connections within 40s" >&2
        docker logs "$name" >&2 || true
        return 1
    fi
}

extract_json() {
    # stdin: probe stdout; prints the object after ===JSON===
    awk 'p{print} /^===JSON===$/{p=1}'
}

run_ratio() {
    local ratio="$1"
    local leg="${NAME_PREFIX}-r${ratio//./p}"
    local driver="${leg}-driver"
    local tmp_load tmp_probe
    tmp_load="$(mktemp)"
    tmp_probe="$(mktemp)"

    echo "=== ratio ${ratio}x  (fresh mongod, direct_io attempt) ===" >&2
    start_mongod "$leg" true

    docker run -d --name "$driver" --network "$ACTIVE_NET" \
        "$PY_IMAGE" sleep infinity >/dev/null
    ACTIVE_DRIVER="$driver"

    docker exec "$driver" pip install --quiet --no-cache-dir pymongo >&2
    docker cp "$here/cache_cliff_probe.py" "$driver:/tmp/cache_cliff_probe.py"

    local common_env=(
        -e "PROBE_URI=mongodb://${leg}:27017"
        -e "PROBE_SECONDS=${SECONDS_PER}"
        -e "PROBE_CONCURRENCY=${CONCURRENCY}"
        -e "PROBE_TARGET_RATIO=${ratio}"
        -e "PROBE_RATIO_TOLERANCE=${RATIO_TOLERANCE}"
    )

    echo "  loading..." >&2
    docker exec "${common_env[@]}" "$driver" \
        python /tmp/cache_cliff_probe.py --mode load >"$tmp_load"

    local before_bytes after_bytes
    before_bytes="$(cgroup_read_bytes "$leg")"
    echo "  probing (device read bytes before=${before_bytes})..." >&2
    docker exec "${common_env[@]}" "$driver" \
        python /tmp/cache_cliff_probe.py --mode probe >"$tmp_probe"
    after_bytes="$(cgroup_read_bytes "$leg")"
    local device_delta=$((after_bytes - before_bytes))
    if [ "$device_delta" -lt 0 ]; then
        device_delta=0
    fi
    echo "  device read bytes delta=${device_delta}" >&2

    # Merge load + probe JSON with guard fields via a tiny python one-liner
    # on the driver (already has python).
    docker cp "$tmp_load" "$driver:/tmp/load.json"
    docker cp "$tmp_probe" "$driver:/tmp/probe.json"
    docker exec -i \
        -e "DEVICE_DELTA=${device_delta}" \
        -e "WT_PAGE_SIZE=${WT_PAGE_SIZE}" \
        -e "DEVICE_BYTE_MIN_FRAC=${DEVICE_BYTE_MIN_FRAC}" \
        -e "TARGET_RATIO=${ratio}" \
        -e "DIRECT_IO=${DIRECT_IO}" \
        "$driver" python - <<'PY'
import json, os, sys

def after_marker(path):
    text = open(path).read()
    if "===JSON===" not in text:
        raise SystemExit(f"no ===JSON=== in {path}")
    return json.loads(text.split("===JSON===", 1)[1])

load = after_marker("/tmp/load.json")
probe = after_marker("/tmp/probe.json")
result = probe.get("result") or {}
pages = int(result.get("pagesReadIntoCache") or 0)
expected = pages * int(os.environ["WT_PAGE_SIZE"])
device = int(os.environ["DEVICE_DELTA"])
ratio = float(os.environ["TARGET_RATIO"])
min_frac = float(os.environ["DEVICE_BYTE_MIN_FRAC"])
guard_ok = True
guard_reason = None
if ratio > 1.0:
    if pages == 0:
        guard_ok = False
        guard_reason = "pagesReadIntoCache_zero_above_1x"
    elif expected > 0 and device < expected * min_frac:
        guard_ok = False
        guard_reason = (
            f"device_bytes_too_low: got {device}, need >= "
            f"{int(expected * min_frac)} ({min_frac:.0%} of {expected} "
            f"pages×page_size) — host page cache likely served the miss"
        )

out = {
    "targetRatio": ratio,
    "directIo": os.environ["DIRECT_IO"] == "true",
    "version": probe.get("version") or load.get("version"),
    "sizing": load.get("sizing"),
    "result": result,
    "deviceReadBytesDelta": device,
    "expectedDeviceBytesFromPages": expected,
    "deviceByteGuardOk": guard_ok,
    "deviceByteGuardReason": guard_reason,
    "at": probe.get("at"),
}
if not guard_ok:
    print(f"GUARD FAILED at {ratio}x: {guard_reason}", file=sys.stderr)
print(json.dumps(out))
PY

    cleanup_leg
    rm -f "$tmp_load" "$tmp_probe"
}

# --- main ------------------------------------------------------------------

legs_json=()
failed_guards=0
for ratio in "${RATIOS[@]}"; do
    ratio="$(echo "$ratio" | tr -d '[:space:]')"
    [ -z "$ratio" ] && continue
    leg_out="$(run_ratio "$ratio")"
    legs_json+=("$leg_out")
    if echo "$leg_out" | grep -q '"deviceByteGuardOk": false'; then
        failed_guards=$((failed_guards + 1))
    fi
done

echo "===JSON==="
summary="$(mktemp)"
printf '%s\n' "${legs_json[@]}" >"$summary"
python3 - "$summary" "$failed_guards" <<'PY'
import json, sys
path, failed = sys.argv[1], int(sys.argv[2])
legs = []
with open(path) as f:
    for line in f:
        line = line.strip()
        if line:
            legs.append(json.loads(line))
versions = {leg.get("version") for leg in legs}
print(json.dumps({
    "experiment": "cache-cliff",
    "issue": 9,
    "ratios": [leg["targetRatio"] for leg in legs],
    "versions": sorted(v for v in versions if v),
    "versionConsistent": len(versions) <= 1,
    "failedDeviceGuards": failed,
    "legs": legs,
}, indent=1))
PY
rm -f "$summary"

if [ "$failed_guards" -gt 0 ]; then
    echo "WARNING: ${failed_guards} leg(s) failed the device-byte guard." >&2
    echo "         Do not import those legs into the corpus." >&2
    exit 2
fi
