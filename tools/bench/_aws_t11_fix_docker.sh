#!/usr/bin/env bash
set -euxo pipefail
STAGE=/c/Users/gmhow/dev/xycalc/tmp/xycalc-t11-20260821-0147
KEY="$STAGE/xycalc-t11-xycalc-t11-20260821-0147.pem"
IP=$(cat "$STAGE/ip.txt")
SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=no -o BatchMode=yes "ec2-user@${IP}")

"${SSH[@]}" 'sudo usermod -aG docker ec2-user; sudo chmod 666 /var/run/docker.sock; docker info | head -5'

# Kill failed sweep if any
"${SSH[@]}" 'kill $(cat /opt/xycalc/results/sweep.pid 2>/dev/null) 2>/dev/null || true'

"${SSH[@]}" 'bash -s' <<'REMOTE'
set -euxo pipefail
cd /opt/xycalc/tools/bench/colocation_probe
mkdir -p /opt/xycalc/results
: > /opt/xycalc/results/summary.jsonl
nohup env MONGO_MEM_GB=8 SHARE_PCTS=50,60,70,80 OVERSUB=2.5 \
  REDIS_MEM=4g CLICKHOUSE_MEM=8g WORKER_MEM=2g \
  OUTDIR=/opt/xycalc/results \
  bash ./share_sweep.sh > /opt/xycalc/results/sweep.log 2>&1 &
echo $! > /opt/xycalc/results/sweep.pid
sleep 20
tail -30 /opt/xycalc/results/sweep.log
docker ps --format '{{.Names}}'
REMOTE

echo "T11 restarted on ${IP}"
