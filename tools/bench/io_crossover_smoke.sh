#!/usr/bin/env bash
# Quick local fio sweep — smoke test for io_crossover_probe.py parsing.
# Not the full Arm A/B harness (no Docker throttle).
#
# Multi-GB scratch (.probe-io-test.bin, gitignored) may stay on COOPER or lynx.
# Do not leave it on swamplink — set PROBE_FILE under /tmp and rm after if you
# must smoke there.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
host="$(hostname -s 2>/dev/null || hostname | cut -d. -f1)"
host_lc="$(printf '%s' "$host" | tr '[:upper:]' '[:lower:]')"

if [ -n "${PROBE_FILE:-}" ]; then
  TEST="$PROBE_FILE"
else
  case "$host_lc" in
    cooper*|lynx*)
      TEST="$repo/.probe-io-test.bin"
      ;;
    swamplink*)
      echo "Refuse multi-GB probe file on swamplink. Run on cooper/lynx, or set PROBE_FILE=/tmp/... and rm after." >&2
      exit 1
      ;;
    *)
      echo "Unknown host '$host'. Set PROBE_FILE explicitly (keep multi-GB scratch on cooper/lynx only)." >&2
      exit 1
      ;;
  esac
fi

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
