#!/usr/bin/env bash
# After a1-r2 finishes, run A2 transfer (1.0 GB cache, knee ratios only).
set -euo pipefail
cd /root/dev/xycalc
OUTDIR=results/cache-cliff
mkdir -p "$OUTDIR"

echo "waiting for a1-r2 runner..." | tee -a "$OUTDIR/chain.log"
while pgrep -f 'run_cliff.sh a1-r2' >/dev/null 2>&1 || pgrep -f 'cache_cliff_probe.sh' >/dev/null 2>&1; do
  # still running if either the wrapper or the probe itself is alive
  if pgrep -f 'run_cliff.sh a1-r2' >/dev/null 2>&1; then
    sleep 30
    continue
  fi
  # wrapper gone — wait for any leftover probe
  if pgrep -f 'cache_cliff_probe.sh' >/dev/null 2>&1; then
    sleep 30
    continue
  fi
  break
done
echo "a1-r2 clear at $(date -Is)" | tee -a "$OUTDIR/chain.log"
ls -la "$OUTDIR/a1-r2.json" "$OUTDIR/a1-r2.log" 2>&1 | tee -a "$OUTDIR/chain.log" || true

export PROBE_CACHE_GB=1.0
# Cache is 1 GB; 640m container cannot host it. Keep tight vs host page cache
# but leave headroom for mongod RSS (~1.6–2.0 GB).
export PROBE_MEMORY=2048m
export PROBE_RATIOS=0.5,0.8,1.0,1.2,1.5,2,4,8
export PROBE_DEV=/dev/sda

echo "starting a2-transfer at $(date -Is)" | tee -a "$OUTDIR/chain.log"
bash /tmp/run_cliff.sh a2-transfer
echo "a2 done rc=$? at $(date -Is)" | tee -a "$OUTDIR/chain.log"
