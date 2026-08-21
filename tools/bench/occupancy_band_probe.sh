#!/usr/bin/env bash
# 007 — eviction_target 80% vs 90% under the same oversubscription.
#
# Fresh mongod per target. Fixed cache, throttle, ratio, concurrency=1.
# Captures occupancy during the window + tcmalloc snapshot.
#
#   ./tools/bench/occupancy_band_probe.sh
#   PROBE_TARGETS=80,90 PROBE_SECONDS=8 PROBE_TARGET_RATIO=2.0 ./tools/bench/occupancy_band_probe.sh
#
# Needs Linux + Docker (same class of host as cache_cliff_probe.sh).
set -euo pipefail

NAME_PREFIX="${PROBE_NAME:-xycalc-occ-band-$$-$(date +%s)}"
IMAGE="${PROBE_IMAGE:-mongo:7}"
PY_IMAGE="${PROBE_PY_IMAGE:-python:3.12-slim}"

CACHE_GB="${PROBE_CACHE_GB:-0.25}"
READ_BPS="${PROBE_READ_BPS:-8388608}"
READ_IOPS="${PROBE_READ_IOPS:-150}"
MEMORY="${PROBE_MEMORY:-640m}"
SECONDS_PER="${PROBE_SECONDS:-25}"
CONCURRENCY="${PROBE_CONCURRENCY:-1}"
RATIO="${PROBE_TARGET_RATIO:-2.0}"
RATIO_TOLERANCE="${PROBE_RATIO_TOLERANCE:-0.10}"
TARGETS_CSV="${PROBE_TARGETS:-80,90}"
OCC_BAND="${PROBE_OCC_BAND:-8}"
WT_PAGE_SIZE="${PROBE_WT_PAGE_SIZE:-32768}"
DEVICE_BYTE_MIN_FRAC="${PROBE_DEVICE_BYTE_MIN_FRAC:-0.10}"

here="$(cd "$(dirname "$0")" && pwd)"

root_src="$(df --output=source / | tail -1)"
parent="$(lsblk -no PKNAME "$root_src" 2>/dev/null | head -1 || true)"
dev="${PROBE_DEV:-$([ -n "$parent" ] && echo "/dev/$parent" || echo "$root_src")}"
if [ ! -b "$dev" ]; then
    echo "no block device to throttle (tried '$dev'). Set PROBE_DEV=/dev/xxx." >&2
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

IFS=',' read -r -a TARGETS <<< "$TARGETS_CSV"

{
    echo "device      $dev  ->  ${READ_BPS} B/s, ${READ_IOPS} IOPS"
    echo "cache       ${CACHE_GB} GB wiredTiger, ${MEMORY} container"
    echo "workload    ratio=${RATIO}x  targets=[${TARGETS_CSV}]  c=${CONCURRENCY}  ${SECONDS_PER}s/leg"
} >&2

cgroup_read_bytes() {
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
    # Arithmetic under `set -u` treats non-numeric junk (e.g. docker/OCI
    # error text) as unbound variable names — coerce hard to an integer.
    if [[ "${out:-0}" =~ ^[0-9]+$ ]]; then
        echo "$out"
    else
        echo 0
    fi
}

start_mongod() {
    local name="$1"
    local net="${name}-net"
    docker network create "$net" >/dev/null
    ACTIVE_NET="$net"

    local args=(
        run -d --name "$name" --network "$net"
        --device-read-bps  "${dev}:${READ_BPS}"
        --device-read-iops "${dev}:${READ_IOPS}"
        --memory "$MEMORY" --memory-swap "$MEMORY"
    )

    if docker "${args[@]}" "$IMAGE" \
        --wiredTigerCacheSizeGB "$CACHE_GB" \
        --wiredTigerEngineConfigString='direct_io=[data]' \
        >/dev/null 2>/tmp/occ-band-mongo-start.err; then
        DIRECT_IO=true
    else
        echo "direct_io start failed; falling back:" >&2
        sed 's/^/  /' /tmp/occ-band-mongo-start.err >&2 || true
        docker rm -f "$name" >/dev/null 2>&1 || true
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

run_target() {
    local target="$1"
    local leg="${NAME_PREFIX}-t${target}"
    local driver="${leg}-driver"
    local tmp_load tmp_probe
    tmp_load="$(mktemp)"
    tmp_probe="$(mktemp)"

    echo "=== eviction_target=${target}%  (fresh mongod, ratio ${RATIO}x) ===" >&2
    start_mongod "$leg"

    docker run -d --name "$driver" --network "$ACTIVE_NET" \
        "$PY_IMAGE" sleep infinity >/dev/null
    ACTIVE_DRIVER="$driver"

    docker exec "$driver" pip install --quiet --no-cache-dir pymongo >&2
    docker cp "$here/occupancy_band_probe.py" "$driver:/tmp/occupancy_band_probe.py"

    local common_env=(
        -e "PROBE_URI=mongodb://${leg}:27017"
        -e "PROBE_SECONDS=${SECONDS_PER}"
        -e "PROBE_CONCURRENCY=${CONCURRENCY}"
        -e "PROBE_TARGET_RATIO=${RATIO}"
        -e "PROBE_RATIO_TOLERANCE=${RATIO_TOLERANCE}"
        -e "PROBE_EVICTION_TARGET=${target}"
        -e "PROBE_OCC_BAND=${OCC_BAND}"
    )

    echo "  loading..." >&2
    docker exec "${common_env[@]}" "$driver" \
        python /tmp/occupancy_band_probe.py --mode load >"$tmp_load"

    # Configure target after load so insert path used defaults; then probe.
    echo "  configuring eviction_target=${target}..." >&2
    docker exec "${common_env[@]}" "$driver" \
        python /tmp/occupancy_band_probe.py --mode configure >/dev/null

    local before_bytes after_bytes
    before_bytes="$(cgroup_read_bytes "$leg")"
    echo "  probing (device read bytes before=${before_bytes})..." >&2
    set +e
    docker exec "${common_env[@]}" "$driver" \
        python /tmp/occupancy_band_probe.py --mode probe >"$tmp_probe" 2>"$tmp_probe.err"
    local probe_rc=$?
    set -e
    if [ "$probe_rc" -ne 0 ]; then
        echo "probe exited $probe_rc:" >&2
        sed 's/^/  /' "$tmp_probe.err" >&2 || true
        sed 's/^/  /' "$tmp_probe" >&2 || true
        cleanup_leg
        rm -f "$tmp_load" "$tmp_probe" "$tmp_probe.err"
        return 1
    fi
    cat "$tmp_probe.err" >&2 || true
    after_bytes="$(cgroup_read_bytes "$leg")"
    local device_delta=$((after_bytes - before_bytes))
    if [ "$device_delta" -lt 0 ]; then
        device_delta=0
    fi
    echo "  device read bytes delta=${device_delta}" >&2
    rm -f "$tmp_probe.err"

    docker cp "$tmp_load" "$driver:/tmp/load.json"
    docker cp "$tmp_probe" "$driver:/tmp/probe.json"
    docker exec -i \
        -e "DEVICE_DELTA=${device_delta}" \
        -e "WT_PAGE_SIZE=${WT_PAGE_SIZE}" \
        -e "DEVICE_BYTE_MIN_FRAC=${DEVICE_BYTE_MIN_FRAC}" \
        -e "TARGET_RATIO=${RATIO}" \
        -e "DIRECT_IO=${DIRECT_IO}" \
        -e "EVICTION_TARGET=${target}" \
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
            f"{int(expected * min_frac)}"
        )
if result.get("occupancyInConfiguredBand") is False:
    # Soft fail: still emit the leg, but flag it — WT may not hold exactly
    # at a raised target under this throttle.
    if guard_ok:
        guard_reason = (
            f"occupancy_mean={result.get('occupancyPctMean')} outside "
            f"band {result.get('occupancyBand')} for configured target "
            f"{os.environ['EVICTION_TARGET']}"
        )
    # Do not flip deviceByteGuardOk for occupancy miss — separate flag.

out = {
    "evictionTarget": int(os.environ["EVICTION_TARGET"]),
    "targetRatio": ratio,
    "directIo": os.environ["DIRECT_IO"] == "true",
    "version": probe.get("version") or load.get("version"),
    "wtConfig": probe.get("wtConfig"),
    "sizing": load.get("sizing"),
    "result": result,
    "deviceReadBytesDelta": device,
    "expectedDeviceBytesFromPages": expected,
    "deviceByteGuardOk": guard_ok,
    "deviceByteGuardReason": guard_reason if not guard_ok else None,
    "occupancyBandNote": guard_reason if result.get("occupancyInConfiguredBand") is False else None,
    "at": probe.get("at"),
}
if not guard_ok:
    print(f"GUARD FAILED at target={os.environ['EVICTION_TARGET']}: {guard_reason}", file=sys.stderr)
print(json.dumps(out))
PY

    cleanup_leg
    rm -f "$tmp_load" "$tmp_probe"
}

legs_json=()
failed_guards=0
for target in "${TARGETS[@]}"; do
    target="$(echo "$target" | tr -d '[:space:]')"
    [ -z "$target" ] && continue
    leg_out="$(run_target "$target")"
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
# Pair 80 vs 90 when both present and guarded.
by_t = {leg.get("evictionTarget"): leg for leg in legs}
delta = None
if 80 in by_t and 90 in by_t:
    a, b = by_t[80].get("result") or {}, by_t[90].get("result") or {}
    if a.get("opsPerSecond") and b.get("opsPerSecond"):
        delta = {
            "opsPerSecond80": a["opsPerSecond"],
            "opsPerSecond90": b["opsPerSecond"],
            "opsPerSecondDeltaPct": round(
                100.0 * (b["opsPerSecond"] - a["opsPerSecond"]) / a["opsPerSecond"], 2
            ),
            "meanLatencyMs80": a.get("meanLatencyMs"),
            "meanLatencyMs90": b.get("meanLatencyMs"),
            "occupancyPctMean80": a.get("occupancyPctMean"),
            "occupancyPctMean90": b.get("occupancyPctMean"),
            "evictedByApp80": a.get("evictedByAppThreads"),
            "evictedByApp90": b.get("evictedByAppThreads"),
            "tcmallocAfter80": a.get("tcmallocAfter"),
            "tcmallocAfter90": b.get("tcmallocAfter"),
        }
print(json.dumps({
    "experiment": "occupancy-band-80-vs-90",
    "failedDeviceGuards": failed,
    "legs": legs,
    "delta80vs90": delta,
}))
if failed:
    sys.exit(2)
PY
rm -f "$summary"
