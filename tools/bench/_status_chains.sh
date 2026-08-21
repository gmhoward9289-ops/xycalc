#!/usr/bin/env bash
set -euo pipefail
ssh -o BatchMode=yes root@swamplink bash -s <<'EOF'
echo "=== procs ==="
pgrep -af 'chain_t11|chain_a2|run_cliff|cache_cliff' | grep -v pgrep || echo none
echo "=== chain.out ==="
tail -5 /root/dev/xycalc/results/cache-cliff/chain.out 2>/dev/null || true
echo "=== a1-r2 log tail ==="
grep -E '=== ratio|ops/s|finished' /root/dev/xycalc/results/cache-cliff/a1-r2.log | tail -20
if ! pgrep -f 'chain_t11.sh' >/dev/null; then
  nohup bash /tmp/chain_t11.sh > /root/dev/xycalc/results/cache-cliff/chain-t11.out 2>&1 &
  echo "started chain_t11 pid=$!"
fi
sleep 1
head -5 /root/dev/xycalc/results/cache-cliff/chain-t11.out 2>/dev/null || true
EOF
