#!/usr/bin/env bash
# After cache-cliff a2-transfer finishes, run a constrained T11 share sweep.
# swamplink has ~7.6 GiB — this is shape/oversub pressure, not reef-scale.
set -euo pipefail
cd /root/dev/xycalc
OUTDIR=results/cache-cliff
echo "waiting for a2 / cliff probes to finish..." | tee -a "$OUTDIR/chain-t11.log"
while pgrep -f 'run_cliff.sh|chain_a2.sh|cache_cliff_probe.sh' >/dev/null 2>&1; do
  sleep 60
done
echo "cliff clear at $(date -Is)" | tee -a "$OUTDIR/chain-t11.log"

cd tools/bench/colocation_probe
chmod +x share_sweep.sh run.sh 2>/dev/null || true
export MONGO_MEM_GB=2
export SHARE_PCTS=50,60,70,80
export OVERSUB=2.5
export REDIS_MEM=1g
export CLICKHOUSE_MEM=2g
export WORKER_MEM=1g
export OUTDIR=/root/dev/xycalc/results/colocation-share
mkdir -p "$OUTDIR"
echo "starting T11 share sweep (constrained) at $(date -Is)" | tee -a /root/dev/xycalc/results/cache-cliff/chain-t11.log
./share_sweep.sh 2>&1 | tee /root/dev/xycalc/results/colocation-share/sweep.log
echo "t11 done at $(date -Is)" | tee -a /root/dev/xycalc/results/cache-cliff/chain-t11.log
