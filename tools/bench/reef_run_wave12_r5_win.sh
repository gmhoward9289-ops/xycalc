#!/usr/bin/env bash
# Wave12 r5 — T4 (cache≥0.25), T6/T8 (mongo_tickets in image), T10 (cp path fix).
set -uo pipefail
export PATH="/c/Program Files/Docker/Docker/resources/bin:/usr/bin:/bin:$PATH"
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'
REPO=/c/Users/Owner/dev/xycalc
OUT=/v/xycalc-results/wave12-r5
mkdir -p "$OUT"
cd "$REPO"
find tools/bench -name '*.sh' -print0 | xargs -0 sed -i 's/\r$//' || true
export PROBE_DEV=/dev/sdd
LOG="$OUT/batch-$(date +%Y%m%d-%H%M%S).log"
exec >>"$LOG" 2>&1
echo "=== wave12-r5 start $(date -Is) ==="
docker version --format 'Server={{.Server.Version}}' || true
docker ps -aq --filter name=xycalc- | xargs -r docker rm -f >/dev/null 2>&1 || true

run() {
  local name="$1"; shift
  echo "===== BEGIN $name $(date -Is) ====="
  if "$@"; then echo "===== OK $name $(date -Is) ====="
  else echo "===== FAIL $name exit=$? $(date -Is) ====="; fi
}

# T4 — MongoDB requires cacheSizeGB ≥ 0.25
run T4-timeseries env PROBE_MODE=timeseries PROBE_LEVELS=8 PROBE_SECONDS=90 \
  PROBE_DOCS=800000 PROBE_CACHE_GB=0.25 PROBE_DEV=/dev/sdd \
  bash tools/bench/ticket_probe.sh > "$OUT/t4-timeseries.json" || true

# T10 dual image
IMGS="clickhouse/clickhouse-server:24.8"
if docker image inspect clickhouse/clickhouse-server:23.3 >/dev/null 2>&1; then
  IMGS="clickhouse/clickhouse-server:23.3,clickhouse/clickhouse-server:24.8"
fi
echo "T10 images: $IMGS"
run T10-clickhouse env PROBE_ROWS=80000 PROBE_BATCHES=1,10,100,1000 \
  PROBE_IMAGES="$IMGS" PROBE_STEP_TIMEOUT=90 \
  bash tools/bench/clickhouse_probe.sh > "$OUT/t10-clickhouse.json" || true

# T6/T8 with rebuilt worker image (mongo_tickets)
run T6-prefetch bash -lc '
  cd /c/Users/Owner/dev/xycalc/tools/bench/celery_probe
  export PROBE_DEV=/dev/sdd OUT=/v/xycalc-results/wave12-r5/t6-prefetch
  export PROBE_PREFETCHES=1,4,8 PROBE_RATES=50 PROBE_SECONDS=20 PROBE_DOCS=900000
  docker compose down -v >/dev/null 2>&1 || true
  docker compose build --no-cache worker >/dev/null
  ./sweep_prefetch.sh
' || true

run T8-stall bash -lc '
  cd /c/Users/Owner/dev/xycalc/tools/bench/celery_probe
  export PROBE_DEV=/dev/sdd OUT=/v/xycalc-results/wave12-r5/t8-stall
  export PROBE_BASELINE_SECONDS=15 PROBE_STALL_SECONDS=20 PROBE_RECOVERY_TIMEOUT=60
  export PROBE_RATES=50 PROBE_DOCS=900000 PROBE_POLICIES=none,immediate,exponential
  export PROBE_STALL_MODE=pause
  ./run_stall_recover.sh
' || true

echo "=== wave12-r5 DONE $(date -Is) ==="
ls -la "$OUT"
