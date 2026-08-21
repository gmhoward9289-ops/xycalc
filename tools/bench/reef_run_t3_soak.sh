#!/usr/bin/env bash
# T3 full soak: write throttle BELOW achievable insert rate so dirty% can climb.
# Prior 32 MiB/s runs stayed under the cap (~6 MiB/s inserts) → vacuous.
set -euo pipefail
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
OUT="${OUT:-/v/xycalc-results/t3-soak-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUT"

export PROBE_DEV="${PROBE_DEV:-/dev/sdd}"
export PROBE_CACHE_GB="${PROBE_CACHE_GB:-0.25}"
export PROBE_MEMORY="${PROBE_MEMORY:-640m}"
export PROBE_JOURNAL="${PROBE_JOURNAL:-0}"
export PROBE_DOC_BYTES="${PROBE_DOC_BYTES:-4096}"
export PROBE_WORKERS="${PROBE_WORKERS:-8}"
export PROBE_MAX_INSERT_CACHE_FRAC="${PROBE_MAX_INSERT_CACHE_FRAC:-2.0}"
# 1 MiB/s — below ~6 MiB/s insert capacity seen on reef smokes
export PROBE_WRITE_BPS="${PROBE_WRITE_BPS:-1048576}"
export PROBE_WRITE_IOPS="${PROBE_WRITE_IOPS:-128}"
export PROBE_SECONDS="${PROBE_SECONDS:-180}"
export PROBE_RATES="${PROBE_RATES:-1,2,4,8}"

{
  echo "=== T3 soak start $(date -Is) ==="
  echo "OUT=$OUT WRITE_BPS=$PROBE_WRITE_BPS IOPS=$PROBE_WRITE_IOPS SECONDS=$PROBE_SECONDS RATES=$PROBE_RATES"
} | tee "$OUT/batch.log"

run() {
  local label="$1"; shift
  echo "===== BEGIN $label $(date -Is) =====" | tee -a "$OUT/batch.log"
  if "$@" > "$OUT/${label}.json" 2>"$OUT/${label}.err"; then
    echo "===== OK $label $(date -Is) =====" | tee -a "$OUT/batch.log"
  else
    echo "===== FAIL $label exit=$? $(date -Is) =====" | tee -a "$OUT/batch.log"
  fi
}

run t3-insert env PROBE_ARM=insert bash tools/bench/eviction_probe.sh
run t3-update env PROBE_ARM=update bash tools/bench/eviction_probe.sh

echo "=== T3 soak done $(date -Is) ===" | tee -a "$OUT/batch.log"
ls -la "$OUT" | tee -a "$OUT/batch.log"
