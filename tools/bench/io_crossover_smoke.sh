#!/usr/bin/env bash
# Quick local fio sweep — smoke test for io_crossover_probe.py parsing.
# Not the full Arm A/B harness (no Docker throttle). Writes /tmp/xycalc-io-probe.bin.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
TEST="${PROBE_FILE:-/mnt/c/Users/gmhow/dev/xycalc/.probe-io-test.bin}"
MEM_MB="${PROBE_MEMORY_MB:-512}"
FILE_MB=$((MEM_MB * 8))

if [ ! -f "$TEST" ] || [ "$(stat -c%s "$TEST" 2>/dev/null || echo 0)" -lt $((FILE_MB * 1024 * 1024)) ]; then
  dd if=/dev/zero of="$TEST" bs=1M count="$FILE_MB" status=none
fi

root_src="$(df --output=source / | tail -1)"
parent="$(lsblk -no PKNAME "$root_src" 2>/dev/null | head -1 || true)"
dev="${PROBE_DEV:-$([ -n "$parent" ] && echo "/dev/$parent" || echo "$root_src")}"

python3 "$here/io_crossover_probe.py" \
  --test-file "$TEST" \
  --device "$dev" \
  --arm local \
  --sizes-kib "4,8,16,32,64,128,256,512,1024" \
  --runtime "${PROBE_RUNTIME:-8}" \
  --iodepth "${PROBE_IODEPTH:-32}"
