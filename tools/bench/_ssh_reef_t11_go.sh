#!/usr/bin/env bash
set -euo pipefail
ROOT_WIN='C:/Users/gmhow/dev/xycalc'
ROOT=/c/Users/gmhow/dev/xycalc
python -c "p=r'$ROOT_WIN/tools/bench/reef_run_t11_share.bat'; d=open(p,'rb').read().replace(b'\r\n',b'\n').replace(b'\n',b'\r\n'); open(p,'wb').write(d)"
sed -i 's/\r$//' "$ROOT/tools/bench/_ssh_reef_t11_go.sh" \
  "$ROOT/tools/bench/reef_launch_t11.ps1" \
  "$ROOT/tools/bench/colocation_probe/share_sweep.sh" \
  "$ROOT/tools/bench/colocation_probe/run.sh"

scp -o BatchMode=yes \
  "$ROOT/tools/bench/reef_run_t11_share.bat" \
  owner@192.168.68.20:C:/Users/Owner/lab/RUN-T11-SHARE.bat
scp -o BatchMode=yes \
  "$ROOT/tools/bench/reef_launch_t11.ps1" \
  "$ROOT/tools/bench/colocation_probe/share_sweep.sh" \
  "$ROOT/tools/bench/colocation_probe/run.sh" \
  "$ROOT/tools/bench/colocation_probe/compose.yml" \
  "$ROOT/tools/bench/colocation_probe/sample.py" \
  "$ROOT/tools/bench/colocation_probe/clickhouse_load.sql" \
  owner@192.168.68.20:C:/Users/Owner/dev/xycalc/tools/bench/colocation_probe/
# also put scripts in place
scp -o BatchMode=yes \
  "$ROOT/tools/bench/colocation_probe/share_sweep.sh" \
  "$ROOT/tools/bench/colocation_probe/run.sh" \
  owner@192.168.68.20:C:/Users/Owner/dev/xycalc/tools/bench/colocation_probe/
scp -o BatchMode=yes "$ROOT/tools/bench/reef_launch_t11.ps1" \
  owner@192.168.68.20:C:/Users/Owner/lab/reef_launch_t11.ps1

ssh -o BatchMode=yes owner@192.168.68.20 \
  'powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\Owner\lab\reef_launch_t11.ps1'
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c type C:\Users\Owner\lab\t11-wmi.out'
echo 'waiting 120s...'
sleep 120
echo '=== sweep.log ==='
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c type C:\Users\Owner\xycalc-results\colocation-share\sweep.log'
echo '=== docker ==='
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c docker ps --format {{.Names}}'
