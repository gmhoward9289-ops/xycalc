#!/usr/bin/env bash
# Issue #17 — I/O size crossover under Docker blkio emulation + local baseline.
#
#   ./tools/bench/io_crossover_probe.sh              # full run (~30 min)
#   PROBE_RUNTIME=8 PROBE_ARM=local ./io_crossover_probe.sh   # local smoke
#
# Arm A throttles via --device-read-bps/iops (same flags as ticket_probe.sh).
# Arm B runs unthrottled on the resolved whole block device.
set -euo pipefail

NAME="${PROBE_NAME:-xycalc-io-crossover-$$-$(date +%s)}"
CONTAINER="${NAME}-fio"
PY_IMAGE="${PROBE_PY_IMAGE:-python:3.12-slim}"
MEMORY="${PROBE_MEMORY:-512m}"
FILE_MB="${PROBE_FILE_MB:-4096}"
RUNTIME="${PROBE_RUNTIME:-12}"
IODEPTH="${PROBE_IODEPTH:-32}"
ARM="${PROBE_ARM:-both}"  # both | local | throttled

here="$(cd "$(dirname "$0")" && pwd)"

root_src="$(df --output=source / | tail -1)"
parent="$(lsblk -no PKNAME "$root_src" 2>/dev/null | head -1 || true)"
dev="${PROBE_DEV:-$([ -n "$parent" ] && echo "/dev/$parent" || echo "$root_src")}"
if [ ! -b "$dev" ]; then
  echo "no block device (tried '$dev'). Set PROBE_DEV=/dev/xxx." >&2
  exit 1
fi

echo "device $dev" >&2
lsblk -o NAME,ROTA,TRAN,SIZE,MODEL "$dev" >&2 || true

cleanup() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker run -d --name "$CONTAINER" \
  --memory "$MEMORY" --memory-swap "$MEMORY" \
  "$PY_IMAGE" sleep infinity >/dev/null

docker exec "$CONTAINER" apt-get update -qq >/dev/null
docker exec "$CONTAINER" apt-get install -y -qq --no-install-recommends fio python3 >/dev/null
docker cp "$here/io_crossover_probe.py" "$CONTAINER:/tmp/io_crossover_probe.py"

TEST="/tmp/io-probe-${FILE_MB}m.bin"
docker exec "$CONTAINER" dd if=/dev/zero of="$TEST" bs=1M count="$FILE_MB" status=none

SIZES="${PROBE_SIZES:-4,8,16,32,64,128,256,512,1024}"

run_local() {
  docker exec "$CONTAINER" python3 /tmp/io_crossover_probe.py \
    --test-file "$TEST" --device "$dev" --arm local \
    --sizes-kib "$SIZES" --runtime "$RUNTIME" --iodepth "$IODEPTH"
}

run_throttled_pair() {
  local iops="$1" bps="$2" tag="$3"
  docker rm -f "${CONTAINER}-t" >/dev/null 2>&1 || true
  docker run -d --name "${CONTAINER}-t" \
    --device-read-bps "${dev}:${bps}" \
    --device-read-iops "${dev}:${iops}" \
    --memory "$MEMORY" --memory-swap "$MEMORY" \
    "$PY_IMAGE" sleep infinity >/dev/null
  docker exec "${CONTAINER}-t" apt-get update -qq >/dev/null
  docker exec "${CONTAINER}-t" apt-get install -y -qq --no-install-recommends fio python3 >/dev/null
  docker cp "$here/io_crossover_probe.py" "${CONTAINER}-t:/tmp/io_crossover_probe.py"
  local tfile="/tmp/io-probe-t-${FILE_MB}m.bin"
  docker exec "${CONTAINER}-t" dd if=/dev/zero of="$tfile" bs=1M count="$FILE_MB" status=none
  docker exec "${CONTAINER}-t" python3 /tmp/io_crossover_probe.py \
    --test-file "$tfile" --device "$dev" --arm "$tag" \
    --sizes-kib "$SIZES" --runtime "$RUNTIME" --iodepth "$IODEPTH" \
    --throttle-iops "$iops" --throttle-bps "$bps"
  docker rm -f "${CONTAINER}-t" >/dev/null 2>&1 || true
}

if [ "$ARM" = "local" ] || [ "$ARM" = "both" ]; then
  echo "=== Arm B — local (unthrottled container) ===" >&2
  run_local
fi

if [ "$ARM" = "throttled" ] || [ "$ARM" = "both" ]; then
  echo "=== Arm A — baseline 3000 / 125 MiB/s ===" >&2
  run_throttled_pair 3000 $((125 * 1024 * 1024)) gp3-baseline
  echo "=== Arm A — throughput-cap 10500 / 2000 MiB/s ===" >&2
  run_throttled_pair 10500 $((2000 * 1024 * 1024)) gp3-throughput-cap
fi
