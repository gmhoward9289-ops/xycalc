# Sequential Wave 1–2 smokes round 2 — fix docs/oversub, CRLF, pre-pulled CH images.
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/local/bin:/mnt/c/Program Files/Docker/Docker/resources/bin:$PATH"
REPO=/mnt/c/Users/Owner/dev/xycalc
OUT=/mnt/v/xycalc-results/wave12-smokes-r2
mkdir -p "$OUT"
cd "$REPO"
find tools/bench -name '*.sh' -print0 | xargs -0 sed -i 's/\r$//' || true
export PROBE_DEV=/dev/sdd
LOG="$OUT/batch-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1
echo "=== wave12-r2 start $(date -Is) ==="
docker version --format 'Server={{.Server.Version}}' || true

run() {
  local name="$1"; shift
  echo "===== BEGIN $name $(date -Is) ====="
  if "$@"; then echo "===== OK $name $(date -Is) ====="
  else echo "===== FAIL $name exit=$? $(date -Is) ====="; fi
}

# T4: smaller cache + enough docs to oversubscribe
run T4-timeseries env PROBE_MODE=timeseries PROBE_LEVELS=8 PROBE_SECONDS=45 \
  PROBE_DOCS=200000 PROBE_CACHE_GB=0.1 PROBE_DEV=/dev/sdd \
  bash tools/bench/ticket_probe.sh > "$OUT/t4-timeseries.json" || true

# T3: longer / higher rates to try to hit dirty trigger
run T3-eviction env PROBE_SECONDS=30 PROBE_RATES=1,2,4,8 PROBE_DEV=/dev/sdd \
  PROBE_CACHE_GB=0.25 \
  bash tools/bench/eviction_probe.sh > "$OUT/t3-eviction.json" || true

# T10: images must already be present (pre-pulled)
run T10-clickhouse env PROBE_ROWS=50000 PROBE_BATCHES=1,10,100 \
  PROBE_IMAGES=clickhouse/clickhouse-server:23.3,clickhouse/clickhouse-server:24.8 \
  PROBE_STEP_TIMEOUT=45 \
  bash tools/bench/clickhouse_probe.sh > "$OUT/t10-clickhouse.json" || true

# T6 prefetch
run T6-prefetch bash -lc "
  cd $REPO/tools/bench/celery_probe
  export PROBE_DEV=/dev/sdd OUT=$OUT/t6-prefetch
  export PROBE_PREFETCHES=1,4 PROBE_RATES=50 PROBE_SECONDS=10 PROBE_DOCS=80000
  sed -i 's|/dev/sda|/dev/sdd|g' compose.yml || true
  ./sweep_prefetch.sh
" || true

# T8 stall
run T8-stall bash -lc "
  cd $REPO/tools/bench/celery_probe
  export PROBE_DEV=/dev/sdd OUT=$OUT/t8-stall
  export PROBE_BASELINE_SECONDS=8 PROBE_STALL_SECONDS=12 PROBE_RECOVERY_TIMEOUT=30
  export PROBE_RATES=50 PROBE_DOCS=80000 PROBE_POLICIES=none,immediate PROBE_STALL_MODE=pause
  sed -i 's|/dev/sda|/dev/sdd|g' compose.yml || true
  ./run_stall_recover.sh
" || true

echo "=== wave12-r2 DONE $(date -Is) ==="
ls -la "$OUT"
