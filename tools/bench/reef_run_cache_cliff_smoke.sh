#!/usr/bin/env bash
# Smoke only (1.0, 2.0 × 6s) — path-fix validation for reef + docker.exe.
set -euo pipefail
ROOT=/mnt/c/Users/Owner/dev/xycalc
OUT=/mnt/c/Users/Owner/xycalc-results
mkdir -p "$OUT"
cd "$ROOT"

DOCKER_EXE="/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe"
BIN=/tmp/xycalc-docker-bin
mkdir -p "$BIN"
cat >"$BIN/docker" <<EOF
#!/bin/sh
exec "$DOCKER_EXE" "\$@"
EOF
chmod +x "$BIN/docker"
export PATH="$BIN:$PATH"

sed -i 's/\r$//' tools/bench/cache_cliff_probe.sh tools/bench/cache_cliff_probe.py 2>/dev/null || true
chmod +x tools/bench/cache_cliff_probe.sh

echo "=== docker / path sanity ===" >&2
docker version >&2
# Prove wslpath conversion the harness will use
wslpath -w "$ROOT/tools/bench/cache_cliff_probe.py" >&2 || true

echo "=== smoke ===" >&2
PROBE_RATIOS=1.0,2.0 PROBE_SECONDS=6 \
  ./tools/bench/cache_cliff_probe.sh \
  >"$OUT/cache-cliff-smoke.json" \
  2>"$OUT/cache-cliff-smoke.log"

echo "=== smoke result ===" >&2
wc -c "$OUT/cache-cliff-smoke.json" >&2
tail -5 "$OUT/cache-cliff-smoke.log" >&2 || true
python3 -c "import json; d=json.load(open('$OUT/cache-cliff-smoke.json')); print('legs', len(d.get('legs',[])), 'failedGuards', d.get('failedDeviceGuards'))" >&2 || true
echo DONE
