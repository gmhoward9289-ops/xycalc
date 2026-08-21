#!/usr/bin/env bash
# Wave12 round 3 — land T3/T4/T6/T8/T10 after harness blocker fixes.
# DNS via mongo IP, looser T3 write path, PROBE_DOCS≥600k, anon CH pull.
set -uo pipefail
export PATH="/usr/bin:/bin:/usr/local/bin:/mnt/c/Program Files/Docker/Docker/resources/bin:$PATH"
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'
REPO=/mnt/c/Users/Owner/dev/xycalc
OUT=/mnt/v/xycalc-results/wave12-r3
mkdir -p "$OUT"
cd "$REPO"
find tools/bench -name '*.sh' -print0 | xargs -0 sed -i 's/\r$//' || true
export PROBE_DEV=/dev/sdd
LOG="$OUT/batch-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1
echo "=== wave12-r3 start $(date -Is) ==="
docker version --format 'Server={{.Server.Version}}' || true

# Clean leftover probe containers/networks from prior failed runs.
docker ps -aq --filter 'name=xycalc-' | xargs -r docker rm -f >/dev/null 2>&1 || true
docker network ls --format '{{.Name}}' | grep -E 'xycalc-(ticket|eviction|ch)-probe' \
  | xargs -r -n1 docker network rm >/dev/null 2>&1 || true

run() {
  local name="$1"; shift
  echo "===== BEGIN $name $(date -Is) ====="
  if "$@"; then echo "===== OK $name $(date -Is) ====="
  else echo "===== FAIL $name exit=$? $(date -Is) ====="; fi
}

# Pre-pull ClickHouse 23.3 with empty DOCKER_CONFIG (no wincred session).
CFG="$(mktemp -d)"
printf '%s\n' '{"auths":{}}' >"$CFG/config.json"
DOCKER_CONFIG="$CFG" docker pull clickhouse/clickhouse-server:23.3 \
  || echo "WARN: 23.3 pull failed; T10 may run 24.x-only"
DOCKER_CONFIG="$CFG" docker pull clickhouse/clickhouse-server:24.8 \
  || docker tag clickhouse/clickhouse-server:24 clickhouse/clickhouse-server:24.8 \
  || true
rm -rf "$CFG"

# T4 checkpoint soak (1s buckets). Oversubscribed cache.
run T4-timeseries env PROBE_MODE=timeseries PROBE_LEVELS=8 PROBE_SECONDS=120 \
  PROBE_DOCS=400000 PROBE_CACHE_GB=0.1 PROBE_DEV=/dev/sdd \
  bash tools/bench/ticket_probe.sh > "$OUT/t4-timeseries.json" || true

# T3: looser write path + no journal wait + larger docs; insert then update if unclear.
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

# T10 dual-image (or 24.8 alone if 23.3 missing).
IMGS="clickhouse/clickhouse-server:24.8"
if docker image inspect clickhouse/clickhouse-server:23.3 >/dev/null 2>&1; then
  IMGS="clickhouse/clickhouse-server:23.3,clickhouse/clickhouse-server:24.8"
fi
run T10-clickhouse env PROBE_ROWS=80000 PROBE_BATCHES=1,10,100,1000 \
  PROBE_IMAGES="$IMGS" PROBE_STEP_TIMEOUT=90 \
  bash tools/bench/clickhouse_probe.sh > "$OUT/t10-clickhouse.json" || true

# T6 / T8 — force ≥600k docs so oversub gate passes; wipe prior 80k volume.
run T6-prefetch bash -lc "
  cd $REPO/tools/bench/celery_probe
  export PROBE_DEV=/dev/sdd OUT=$OUT/t6-prefetch
  export PROBE_PREFETCHES=1,4,8 PROBE_RATES=50 PROBE_SECONDS=20 PROBE_DOCS=600000
  docker compose down -v >/dev/null 2>&1 || true
  ./sweep_prefetch.sh
" || true

run T8-stall bash -lc "
  cd $REPO/tools/bench/celery_probe
  export PROBE_DEV=/dev/sdd OUT=$OUT/t8-stall
  export PROBE_BASELINE_SECONDS=15 PROBE_STALL_SECONDS=20 PROBE_RECOVERY_TIMEOUT=60
  export PROBE_RATES=50 PROBE_DOCS=600000 PROBE_POLICIES=none,immediate,exponential
  export PROBE_STALL_MODE=pause
  ./run_stall_recover.sh
" || true

echo "=== wave12-r3 DONE $(date -Is) ==="
ls -la "$OUT"
