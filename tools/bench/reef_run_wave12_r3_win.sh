#!/usr/bin/env bash
# Wave12 round 3 — Git Bash on reef Windows (Docker Desktop).
set -uo pipefail
export PATH="/c/Program Files/Docker/Docker/resources/bin:/usr/bin:/bin:$PATH"
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'
REPO=/c/Users/Owner/dev/xycalc
OUT=/v/xycalc-results/wave12-r3
mkdir -p "$OUT"
cd "$REPO"
find tools/bench -name '*.sh' -print0 | xargs -0 sed -i 's/\r$//' || true
export PROBE_DEV=/dev/sdd
LOG="$OUT/batch-$(date +%Y%m%d-%H%M%S).log"
exec >>"$LOG" 2>&1
echo "=== wave12-r3-win start $(date -Is) ==="
docker version --format 'Server={{.Server.Version}}' || true

docker ps -aq --filter name=xycalc- | xargs -r docker rm -f >/dev/null 2>&1 || true

run() {
  local name="$1"; shift
  echo "===== BEGIN $name $(date -Is) ====="
  if "$@"; then echo "===== OK $name $(date -Is) ====="
  else echo "===== FAIL $name exit=$? $(date -Is) ====="; fi
}

# 23.3 pre-loaded via docker load (Hub credential helper broken headless).
docker tag clickhouse/clickhouse-server:24 clickhouse/clickhouse-server:24.8 2>/dev/null || true
if ! docker image inspect clickhouse/clickhouse-server:23.3 >/dev/null 2>&1; then
  echo "WARN: clickhouse 23.3 missing — load V:/xycalc-results/ch23.3.tar first" >&2
fi

run T4-timeseries env PROBE_MODE=timeseries PROBE_LEVELS=8 PROBE_SECONDS=120 \
  PROBE_DOCS=400000 PROBE_CACHE_GB=0.1 PROBE_DEV=/dev/sdd \
  bash tools/bench/ticket_probe.sh > "$OUT/t4-timeseries.json" || true

run T3-eviction-insert env PROBE_SECONDS=60 PROBE_RATES=1,2,4,8 \
  PROBE_WRITE_BPS=33554432 PROBE_WRITE_IOPS=800 \
  PROBE_CACHE_GB=0.25 PROBE_DEV=/dev/sdd PROBE_JOURNAL=0 \
  PROBE_DOC_BYTES=4096 PROBE_WORKERS=8 PROBE_MAX_INSERT_CACHE_FRAC=2.0 \
  PROBE_ARM=insert \
  bash tools/bench/eviction_probe.sh > "$OUT/t3-eviction-insert.json" || true

run T3-eviction-update env PROBE_SECONDS=60 PROBE_RATES=2,4,8 \
  PROBE_WRITE_BPS=33554432 PROBE_WRITE_IOPS=800 \
  PROBE_CACHE_GB=0.25 PROBE_DEV=/dev/sdd PROBE_JOURNAL=0 \
  PROBE_DOC_BYTES=4096 PROBE_WORKERS=8 \
  PROBE_ARM=update \
  bash tools/bench/eviction_probe.sh > "$OUT/t3-eviction-update.json" || true

IMGS="clickhouse/clickhouse-server:24.8"
if docker image inspect clickhouse/clickhouse-server:23.3 >/dev/null 2>&1; then
  IMGS="clickhouse/clickhouse-server:23.3,clickhouse/clickhouse-server:24.8"
fi
run T10-clickhouse env PROBE_ROWS=80000 PROBE_BATCHES=1,10,100,1000 \
  PROBE_IMAGES="$IMGS" PROBE_STEP_TIMEOUT=90 \
  bash tools/bench/clickhouse_probe.sh > "$OUT/t10-clickhouse.json" || true

run T6-prefetch bash -lc '
  cd /c/Users/Owner/dev/xycalc/tools/bench/celery_probe
  export PROBE_DEV=/dev/sdd OUT=/v/xycalc-results/wave12-r3/t6-prefetch
  export PROBE_PREFETCHES=1,4,8 PROBE_RATES=50 PROBE_SECONDS=20 PROBE_DOCS=600000
  docker compose down -v >/dev/null 2>&1 || true
  ./sweep_prefetch.sh
' || true

run T8-stall bash -lc '
  cd /c/Users/Owner/dev/xycalc/tools/bench/celery_probe
  export PROBE_DEV=/dev/sdd OUT=/v/xycalc-results/wave12-r3/t8-stall
  export PROBE_BASELINE_SECONDS=15 PROBE_STALL_SECONDS=20 PROBE_RECOVERY_TIMEOUT=60
  export PROBE_RATES=50 PROBE_DOCS=600000 PROBE_POLICIES=none,immediate,exponential
  export PROBE_STALL_MODE=pause
  ./run_stall_recover.sh
' || true

echo "=== wave12-r3-win DONE $(date -Is) ==="
ls -la "$OUT"
