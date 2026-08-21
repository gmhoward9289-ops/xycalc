#!/usr/bin/env bash
# Wave12 Windows/Git-Bash batch for reef (invoked from reef_run_wave12_win.ps1).
set -uo pipefail
export PATH="/c/Program Files/Docker/Docker/resources/bin:/usr/bin:/bin:$PATH"
export PROBE_DEV=/dev/sdd
REPO=/c/Users/Owner/dev/xycalc
OUT=/v/xycalc-results/wave12-win
mkdir -p "$OUT"
cd "$REPO"
find tools/bench -name '*.sh' -print0 | xargs -0 sed -i 's/\r$//' || true
LOG="$OUT/batch.log"
exec > >(tee -a "$LOG") 2>&1
echo "=== win12 start $(date -Is) ==="
docker version --format 'Server={{.Server.Version}}' || true

run() {
  local name="$1"; shift
  echo "===== BEGIN $name $(date -Is) ====="
  if "$@"; then echo "===== OK $name $(date -Is) ====="
  else echo "===== FAIL $name exit=$? $(date -Is) ====="; fi
}

# Ensure clickhouse tags (24.8 alias; 23.3 may fail without hub auth)
docker tag clickhouse/clickhouse-server:24 clickhouse/clickhouse-server:24.8 2>/dev/null || true
docker pull clickhouse/clickhouse-server:23.3 || echo "WARN: no 23.3 image"

run T4 env PROBE_MODE=timeseries PROBE_LEVELS=8 PROBE_SECONDS=60 \
  PROBE_DOCS=400000 PROBE_CACHE_GB=0.1 PROBE_DEV=/dev/sdd \
  bash tools/bench/ticket_probe.sh > "$OUT/t4.json" || true

run T3 env PROBE_SECONDS=40 PROBE_RATES=1,2,4,8 \
  PROBE_WRITE_BPS=16777216 PROBE_WRITE_IOPS=400 PROBE_CACHE_GB=0.25 PROBE_DEV=/dev/sdd \
  bash tools/bench/eviction_probe.sh > "$OUT/t3.json" || true

# T10: if 23.3 missing, still run 24.8 alone and note in log
IMGS="clickhouse/clickhouse-server:24.8"
if docker image inspect clickhouse/clickhouse-server:23.3 >/dev/null 2>&1; then
  IMGS="clickhouse/clickhouse-server:23.3,clickhouse/clickhouse-server:24.8"
fi
run T10 env PROBE_ROWS=50000 PROBE_BATCHES=1,10,100 PROBE_IMAGES="$IMGS" \
  PROBE_STEP_TIMEOUT=60 \
  bash tools/bench/clickhouse_probe.sh > "$OUT/t10.json" || true

run T6 bash -lc '
  cd /c/Users/Owner/dev/xycalc/tools/bench/celery_probe
  sed -i "s|/dev/sda|/dev/sdd|g" compose.yml || true
  export PROBE_DEV=/dev/sdd OUT=/v/xycalc-results/wave12-win/t6
  export PROBE_PREFETCHES=1,4 PROBE_RATES=50 PROBE_SECONDS=12 PROBE_DOCS=600000
  ./sweep_prefetch.sh
' || true

run T8 bash -lc '
  cd /c/Users/Owner/dev/xycalc/tools/bench/celery_probe
  sed -i "s|/dev/sda|/dev/sdd|g" compose.yml || true
  export PROBE_DEV=/dev/sdd OUT=/v/xycalc-results/wave12-win/t8
  export PROBE_BASELINE_SECONDS=10 PROBE_STALL_SECONDS=15 PROBE_RECOVERY_TIMEOUT=40
  export PROBE_RATES=50 PROBE_DOCS=600000 PROBE_POLICIES=none,immediate PROBE_STALL_MODE=pause
  ./run_stall_recover.sh
' || true

echo "=== win12 DONE $(date -Is) ==="
ls -la "$OUT"
