#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
OUT=/root/celery-sweep
mkdir -p "$OUT"
run_one() {
  local id="$1"
  shift
  echo "--- run $id: $* ---"
  env "$@" ./run.sh > "$OUT/run${id}.log" 2>&1
  grep -q '===JSON===' "$OUT/run${id}.log"
}
run_one 7 PROBE_ACKS_LATE=1 PROBE_PREFETCH=16 PROBE_RATES=200 PROBE_SECONDS=30
run_one 8 PROBE_ACKS_LATE=1 PROBE_CONCURRENCY=4 PROBE_RATES=200 PROBE_SECONDS=30
echo done
