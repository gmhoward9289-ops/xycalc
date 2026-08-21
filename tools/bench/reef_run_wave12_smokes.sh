#!/usr/bin/env bash
# Sequential Wave 1–2 smokes on reef (WSL). One Docker-heavy job at a time.
# Results under /mnt/v/xycalc-results/. Uses PROBE_DEV=/dev/sdd (Docker Desktop virtio data disk).
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/local/bin:$PATH"
# Prefer Windows docker.exe from WSL
if ! command -v docker >/dev/null 2>&1; then
  export PATH="/mnt/c/Program Files/Docker/Docker/resources/bin:$PATH"
fi
REPO=/mnt/c/Users/Owner/dev/xycalc
OUT=/mnt/v/xycalc-results/wave12-smokes
mkdir -p "$OUT"
cd "$REPO"
# strip CRLF on scripts we touch
find tools/bench -name '*.sh' -print0 | xargs -0 sed -i 's/\r$//' || true
export PROBE_DEV=/dev/sdd
LOG="$OUT/batch-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1
echo "=== wave12 batch start $(date -Is) PROBE_DEV=$PROBE_DEV ==="
docker version --format 'Server={{.Server.Version}}' || docker.exe version

run() {
  local name="$1"; shift
  echo "===== BEGIN $name $(date -Is) ====="
  if "$@"; then
    echo "===== OK $name $(date -Is) ====="
  else
    echo "===== FAIL $name exit=$? $(date -Is) ====="
  fi
}

# T4 timeseries smoke
run T4-timeseries env PROBE_MODE=timeseries PROBE_LEVELS=8 PROBE_SECONDS=30 PROBE_DOCS=30000 \
  PROBE_DEV=/dev/sdd bash tools/bench/ticket_probe.sh \
  > "$OUT/t4-timeseries.json" || true

# T3 eviction smoke
run T3-eviction env PROBE_SECONDS=20 PROBE_RATES=0.5,1,2 PROBE_DEV=/dev/sdd \
  bash tools/bench/eviction_probe.sh > "$OUT/t3-eviction.json" || true

# T5 covered query smoke
run T5-covered env PROBE_DOCS=50000 PROBE_CACHE_GB=0.5 \
  bash tools/bench/covered_query_probe.sh > "$OUT/t5-covered.json" || true

# T10 ClickHouse smoke (two images)
run T10-clickhouse env PROBE_ROWS=50000 PROBE_BATCHES=1,10,100 \
  PROBE_IMAGES=clickhouse/clickhouse-server:23.3,clickhouse/clickhouse-server:24.8 \
  PROBE_STEP_TIMEOUT=30 \
  bash tools/bench/clickhouse_probe.sh > "$OUT/t10-clickhouse.json" || true

# T6 prefetch smoke (compose) — needs block device in compose; may fail on Desktop
run T6-prefetch bash -lc '
  cd tools/bench/celery_probe
  export PROBE_DEV=/dev/sdd OUT='"$OUT"'/t6-prefetch
  export PROBE_PREFETCHES=1,4 PROBE_RATES=50 PROBE_SECONDS=8 PROBE_DOCS=80000
  # Patch compose blkio device if present
  if [ -f compose.yml ]; then
    cp compose.yml compose.yml.bak
    sed -i "s|/dev/sda|/dev/sdd|g" compose.yml || true
  fi
  ./sweep_prefetch.sh || true
'

# T8 stall/recover smoke
run T8-stall bash -lc '
  cd tools/bench/celery_probe
  export PROBE_DEV=/dev/sdd OUT='"$OUT"'/t8-stall
  export PROBE_BASELINE_SECONDS=8 PROBE_STALL_SECONDS=12 PROBE_RECOVERY_TIMEOUT=30
  export PROBE_RATES=50 PROBE_DOCS=80000 PROBE_POLICIES=none,immediate PROBE_STALL_MODE=pause
  if [ -f compose.yml ]; then sed -i "s|/dev/sda|/dev/sdd|g" compose.yml || true; fi
  ./run_stall_recover.sh || true
'

# burst smoke (needs root + losetup — may skip)
run burst env PROBE_SMOKE=1 PROBE_SIZE_GB=2 PROBE_IMG=/mnt/v/xycalc-work/burst-smoke.img \
  PROBE_OUT="$OUT/burst" \
  bash -lc 'if [ "$(id -u)" -eq 0 ]; then bash tools/bench/burst_probe.sh; else echo SKIP_burst_not_root; exit 0; fi' \
  > "$OUT/burst.json" || true

echo "=== wave12 batch DONE $(date -Is) ==="
ls -la "$OUT"
