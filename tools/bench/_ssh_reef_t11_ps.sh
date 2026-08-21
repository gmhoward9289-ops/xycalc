#!/usr/bin/env bash
set -euo pipefail
ROOT=/c/Users/gmhow/dev/xycalc
sed -i 's/\r$//' "$ROOT/tools/bench/reef_run_t11_share.ps1" "$ROOT/tools/bench/reef_launch_t11.ps1"
scp -o BatchMode=yes \
  "$ROOT/tools/bench/reef_run_t11_share.ps1" \
  "$ROOT/tools/bench/reef_launch_t11.ps1" \
  "$ROOT/tools/bench/colocation_probe/sample.py" \
  "$ROOT/tools/bench/colocation_probe/compose.yml" \
  "$ROOT/tools/bench/colocation_probe/clickhouse_load.sql" \
  owner@192.168.68.20:C:/Users/Owner/lab/
scp -o BatchMode=yes \
  "$ROOT/tools/bench/reef_run_t11_share.ps1" \
  owner@192.168.68.20:C:/Users/Owner/lab/reef_run_t11_share.ps1
scp -o BatchMode=yes \
  "$ROOT/tools/bench/colocation_probe/sample.py" \
  "$ROOT/tools/bench/colocation_probe/compose.yml" \
  "$ROOT/tools/bench/colocation_probe/clickhouse_load.sql" \
  owner@192.168.68.20:C:/Users/Owner/dev/xycalc/tools/bench/colocation_probe/
ssh -o BatchMode=yes owner@192.168.68.20 \
  'powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\Owner\lab\reef_launch_t11.ps1'
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c type C:\Users\Owner\lab\t11-wmi.out'
echo 'poll 90s...'
sleep 90
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c type C:\Users\Owner\xycalc-results\colocation-share\sweep.log'
echo '---'
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c docker ps --format {{.Names}}'
