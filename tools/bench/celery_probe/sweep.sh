#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
OUT=/root/celery-sweep
mkdir -p "$OUT"
log="$OUT/sweep.log"
exec > >(tee -a "$log") 2>&1
echo "=== celery sweep start $(date -Is) ==="
runs=(
  "1|PROBE_ACKS_LATE=0 PROBE_RATES=200 PROBE_SECONDS=30"
  "2|PROBE_ACKS_LATE=1 PROBE_RATES=25,50,100,200,400 PROBE_SECONDS=30"
  "3|PROBE_ACKS_LATE=1 PROBE_VISIBILITY_TIMEOUT=10 PROBE_RATES=200 PROBE_SECONDS=60"
  "4|PROBE_ACKS_LATE=1 PROBE_VISIBILITY_TIMEOUT=5 PROBE_RATES=200 PROBE_SECONDS=60"
  "5|PROBE_ACKS_LATE=1 PROBE_VISIBILITY_TIMEOUT=2 PROBE_RATES=200 PROBE_SECONDS=60"
  "6|PROBE_ACKS_LATE=1 PROBE_PREFETCH=1 PROBE_RATES=200 PROBE_SECONDS=30"
  "7|PROBE_ACKS_LATE=1 PROBE_PREFETCH=16 PROBE_RATES=200 PROBE_SECONDS=30"
  "8|PROBE_ACKS_LATE=1 PROBE_CONCURRENCY=4 PROBE_RATES=200 PROBE_SECONDS=30"
  "9|PROBE_ACKS_LATE=1 PROBE_CONCURRENCY=16 PROBE_RATES=200 PROBE_SECONDS=30"
)
for entry in "${runs[@]}"; do
  id="${entry%%|*}"
  envs="${entry#*|}"
  echo "--- run $id start $(date -Is) env: $envs ---"
  env $envs ./run.sh > "$OUT/run${id}.log" 2>&1
  ec=$?
  echo "--- run $id exit $ec $(date -Is) ---"
  if [ $ec -ne 0 ]; then echo "FAILED run $id"; exit $ec; fi
  if ! grep -q '===JSON===' "$OUT/run${id}.log"; then echo "MISSING JSON run $id"; exit 1; fi
done
echo "=== celery sweep complete $(date -Is) ==="
