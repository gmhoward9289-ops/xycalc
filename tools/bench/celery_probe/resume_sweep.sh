#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
OUT=/root/celery-sweep
mkdir -p "$OUT"
runs=(
  "7|PROBE_ACKS_LATE=1 PROBE_PREFETCH=16 PROBE_RATES=200 PROBE_SECONDS=30"
  "8|PROBE_ACKS_LATE=1 PROBE_CONCURRENCY=4 PROBE_RATES=200 PROBE_SECONDS=30"
  "9|PROBE_ACKS_LATE=1 PROBE_CONCURRENCY=16 PROBE_RATES=200 PROBE_SECONDS=30"
)
for entry in "${runs[@]}"; do
  id="${entry%%|*}"
  envs="${entry#*|}"
  echo "--- resume run $id ---"
  env $envs ./run.sh > "$OUT/run${id}.log" 2>&1
  grep -q '===JSON===' "$OUT/run${id}.log"
done
echo "resume complete"
