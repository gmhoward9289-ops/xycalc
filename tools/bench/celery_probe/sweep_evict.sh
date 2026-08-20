#!/usr/bin/env bash
# Three-arm maxmemory-policy sweep for investigation 005 (issue #15).
#
#   ./sweep_evict.sh
#   OUT=/tmp/evict OUT=/tmp/evict ./sweep_evict.sh
set -euo pipefail
cd "$(dirname "$0")"
OUT="${OUT:-/root/celery-evict-sweep}"
mkdir -p "$OUT"
log="$OUT/sweep.log"
exec > >(tee -a "$log") 2>&1
echo "=== redis evict sweep start $(date -Is) ==="

for policy in noeviction allkeys-lru volatile-lru; do
  echo "--- $policy start $(date -Is) ---"
  PROBE_MAXMEMORY_POLICY="$policy" ./run_evict.sh > "$OUT/${policy}.log" 2>&1
  ec=$?
  echo "--- $policy exit $ec $(date -Is) ---"
  if [ "$ec" -ne 0 ]; then echo "FAILED $policy (exit $ec)"; exit "$ec"; fi
  if ! grep -q '===JSON===' "$OUT/${policy}.log"; then
    echo "MISSING JSON $policy"
    exit 1
  fi
done

echo "=== redis evict sweep complete $(date -Is) ==="
echo "Import with:"
echo "  python tools/import_evict_probe.py $OUT/noeviction.log $OUT/allkeys-lru.log $OUT/volatile-lru.log --date $(date +%F) --publish"
