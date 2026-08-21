#!/usr/bin/env bash
# Wave12 r6 — tickets fallback + full compose rebuild + heavier T10.
set -uo pipefail
export PATH="/c/Program Files/Docker/Docker/resources/bin:/usr/bin:/bin:$PATH"
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'
REPO=/c/Users/Owner/dev/xycalc
OUT=/v/xycalc-results/wave12-r6
mkdir -p "$OUT"
cd "$REPO"
find tools/bench -name '*.sh' -print0 | xargs -0 sed -i 's/\r$//' || true
export PROBE_DEV=/dev/sdd
LOG="$OUT/batch-$(date +%Y%m%d-%H%M%S).log"
exec >>"$LOG" 2>&1
echo "=== wave12-r6 start $(date -Is) ==="
docker version --format 'Server={{.Server.Version}}' || true
docker ps -aq --filter name=xycalc- | xargs -r docker rm -f >/dev/null 2>&1 || true

run() {
  local name="$1"; shift
  echo "===== BEGIN $name $(date -Is) ====="
  if "$@"; then echo "===== OK $name $(date -Is) ====="
  else echo "===== FAIL $name exit=$? $(date -Is) ====="; fi
}

# T4 smoke (90s) — enough to land timeseries shape; note <480s floor in FINDINGS
run T4-timeseries env PROBE_MODE=timeseries PROBE_LEVELS=8 PROBE_SECONDS=90 \
  PROBE_DOCS=800000 PROBE_CACHE_GB=0.25 PROBE_DEV=/dev/sdd \
  bash tools/bench/ticket_probe.sh > "$OUT/t4-timeseries.json" || true

# T10 — more rows/writers to approach parts_to_delay
IMGS="clickhouse/clickhouse-server:23.3,clickhouse/clickhouse-server:24.8"
run T10-clickhouse env PROBE_ROWS=400000 PROBE_BATCHES=1,10,100 \
  PROBE_WRITERS=16 PROBE_IMAGES="$IMGS" PROBE_STEP_TIMEOUT=120 \
  bash tools/bench/clickhouse_probe.sh > "$OUT/t10-clickhouse.json" || true

# Rebuild ALL celery images so driver gets drive.py fallback
run T6-prefetch bash -lc '
  cd /c/Users/Owner/dev/xycalc/tools/bench/celery_probe
  export PROBE_DEV=/dev/sdd OUT=/v/xycalc-results/wave12-r6/t6-prefetch
  export PROBE_PREFETCHES=1,4,8 PROBE_RATES=50 PROBE_SECONDS=20 PROBE_DOCS=900000
  docker compose down -v >/dev/null 2>&1 || true
  docker compose build --no-cache worker driver stall-driver
  ./sweep_prefetch.sh
' || true

run T8-stall bash -lc '
  cd /c/Users/Owner/dev/xycalc/tools/bench/celery_probe
  export PROBE_DEV=/dev/sdd OUT=/v/xycalc-results/wave12-r6/t8-stall
  export PROBE_BASELINE_SECONDS=15 PROBE_STALL_SECONDS=20 PROBE_RECOVERY_TIMEOUT=60
  export PROBE_RATES=50 PROBE_DOCS=900000 PROBE_POLICIES=none,immediate,exponential
  export PROBE_STALL_MODE=pause
  ./run_stall_recover.sh
' || true

echo "=== wave12-r6 DONE $(date -Is) ==="
ls -la "$OUT"
