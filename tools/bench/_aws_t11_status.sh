#!/usr/bin/env bash
set -ux
STAGE=/c/Users/gmhow/dev/xycalc/tmp/xycalc-t11-20260821-0147
KEY="$STAGE/xycalc-t11-xycalc-t11-20260821-0147.pem"
IP=$(cat "$STAGE/ip.txt")
ssh -i "$KEY" -o StrictHostKeyChecking=no -o BatchMode=yes "ec2-user@${IP}" \
  'tail -25 /opt/xycalc/results/sweep.log; echo ---; docker ps --format {{.Names}}; echo ---; cat /opt/xycalc/results/sweep.pid; ps -p $(cat /opt/xycalc/results/sweep.pid) -o pid,etime,cmd || echo dead'
