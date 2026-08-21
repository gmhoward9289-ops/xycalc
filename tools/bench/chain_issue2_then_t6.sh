#!/usr/bin/env bash
# Wait for issue #2 phase 1, then run T6 prefetch sweep (serial on one host).
set -euo pipefail
cd /root/dev/xycalc
PHASE1_OUT=/root/dev/xycalc/results/issue2-phase1
LOG=$PHASE1_OUT/chain.log
mkdir -p "$PHASE1_OUT"
exec >>"$LOG" 2>&1
echo "=== chain start $(date -Is) ==="

while true; do
  if pgrep -f '/tools/bench/run_issue2_phase1.sh' >/dev/null 2>&1 \
     || pgrep -f '/tools/bench/ticket_probe.sh' >/dev/null 2>&1; then
    echo "waiting for phase1 $(date -Is)"
    sleep 30
    continue
  fi
  break
done

if ! grep -q 'phase 1 complete' "$PHASE1_OUT/nohup.out" 2>/dev/null; then
  echo "WARN: no completion marker in nohup.out; sleeping 60s"
  sleep 60
fi

T6_OUT=/root/dev/xycalc/results/t6-prefetch-$(date +%Y%m%d-%H%M%S)
echo "=== phase1 done $(date -Is); starting T6 out=$T6_OUT ==="
mkdir -p "$T6_OUT"
cd tools/bench/celery_probe
docker compose down >/dev/null 2>&1 || true
OUT="$T6_OUT" PROBE_RATES=400 PROBE_SECONDS=30 ./sweep_prefetch.sh
echo "=== T6 complete $(date -Is) out=$T6_OUT ==="
