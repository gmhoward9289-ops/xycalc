#!/usr/bin/env bash
# Phase A on reef via WSL: smoke, then A1 run 1. Results under /mnt/c/Users/Owner/xycalc-results/
set -euo pipefail
ROOT=/mnt/c/Users/Owner/dev/xycalc
OUT=/mnt/c/Users/Owner/xycalc-results
mkdir -p "$OUT"
cd "$ROOT"
sed -i 's/\r$//' tools/bench/cache_cliff_probe.sh tools/bench/cache_cliff_probe.py 2>/dev/null || true
chmod +x tools/bench/cache_cliff_probe.sh

echo "=== device / docker sanity ===" >&2
lsblk -o NAME,ROTA,TRAN,SIZE,MODEL | head -8 >&2 || true
docker info >/dev/null
echo "docker ok" >&2

echo "=== A smoke (1.0, 2.0 x 6s) ===" >&2
PROBE_RATIOS=1.0,2.0 PROBE_SECONDS=6 \
  ./tools/bench/cache_cliff_probe.sh \
  >"$OUT/cache-cliff-smoke.json" \
  2>"$OUT/cache-cliff-smoke.log"
echo "smoke wrote $OUT/cache-cliff-smoke.json ($(wc -c <"$OUT/cache-cliff-smoke.json") bytes)" >&2

echo "=== A1 run 1 (full ratios, 0.25 GB cache) ===" >&2
./tools/bench/cache_cliff_probe.sh \
  >"$OUT/cache-cliff-a1-r1.json" \
  2>"$OUT/cache-cliff-a1-r1.log"
echo "a1-r1 wrote $OUT/cache-cliff-a1-r1.json ($(wc -c <"$OUT/cache-cliff-a1-r1.json") bytes)" >&2
echo DONE
