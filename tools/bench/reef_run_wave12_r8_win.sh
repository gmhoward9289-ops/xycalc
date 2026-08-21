#!/usr/bin/env bash
# Wave12 r8 — T4 (mongo_tickets synced) + T8 (pause skip tickets) + T10 (retain non-onset JSON).
set -uo pipefail
export PATH="/c/Program Files/Docker/Docker/resources/bin:/usr/bin:/bin:$PATH"
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'
REPO=/c/Users/Owner/dev/xycalc
OUT=/v/xycalc-results/wave12-r8
mkdir -p "$OUT"
cd "$REPO"
find tools/bench -name '*.sh' -print0 | xargs -0 sed -i 's/\r$//' || true
export PROBE_DEV=/dev/sdd
LOG="$OUT/batch-$(date +%Y%m%d-%H%M%S).log"
exec >>"$LOG" 2>&1
echo "=== wave12-r8 start $(date -Is) ==="
ls -la tools/bench/mongo_tickets.py tools/bench/celery_probe/stall_recover.py tools/bench/clickhouse_probe.sh || true

run() {
  local name="$1"; shift
  echo "===== BEGIN $name $(date -Is) ====="
  if "$@"; then echo "===== OK $name $(date -Is) ====="
  else echo "===== FAIL $name exit=$? $(date -Is) ====="; fi
}

run T4-timeseries env PROBE_MODE=timeseries PROBE_LEVELS=8 PROBE_SECONDS=120 \
  PROBE_DOCS=800000 PROBE_CACHE_GB=0.25 PROBE_DEV=/dev/sdd \
  bash tools/bench/ticket_probe.sh > "$OUT/t4-timeseries.json" || true

run T8-stall bash -lc '
  cd /c/Users/Owner/dev/xycalc/tools/bench/celery_probe
  export PROBE_DEV=/dev/sdd OUT=/v/xycalc-results/wave12-r8/t8-stall
  export PROBE_BASELINE_SECONDS=15 PROBE_STALL_SECONDS=20 PROBE_RECOVERY_TIMEOUT=60
  export PROBE_RATES=200 PROBE_DOCS=900000 PROBE_POLICIES=none,immediate,exponential
  export PROBE_STALL_MODE=pause
  docker compose down -v >/dev/null 2>&1 || true
  docker compose build --no-cache worker driver stall-driver
  ./run_stall_recover.sh
' || true

run T10-clickhouse env PROBE_ROWS=500000 PROBE_WRITERS=16 PROBE_BATCHES=1,10,100 \
  PROBE_STEP_TIMEOUT=90 PROBE_IMAGES=clickhouse/clickhouse-server:23.3,clickhouse/clickhouse-server:24.8 \
  bash tools/bench/clickhouse_probe.sh > "$OUT/t10-clickhouse.json" || true

echo "=== wave12-r8 DONE $(date -Is) ==="
ls -la "$OUT"
ls -la "$OUT/t8-stall" 2>/dev/null || true
