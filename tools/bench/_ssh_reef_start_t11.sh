#!/usr/bin/env bash
# Sync colocation share_sweep to reef and start constrained/full T11 via WMI bat.
set -euo pipefail
ROOT=/c/Users/gmhow/dev/xycalc
# Ensure LF for sh, CRLF for bats if needed
sed -i 's/\r$//' "$ROOT/tools/bench/colocation_probe/share_sweep.sh" \
  "$ROOT/tools/bench/colocation_probe/run.sh" 2>/dev/null || true

ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c mkdir C:\Users\Owner\dev\xycalc\tools\bench\colocation_probe 2>nul & mkdir C:\Users\Owner\xycalc-results\colocation-share 2>nul'
scp -o BatchMode=yes -r "$ROOT/tools/bench/colocation_probe/"* \
  owner@192.168.68.20:C:/Users/Owner/dev/xycalc/tools/bench/colocation_probe/
scp -o BatchMode=yes \
  "$ROOT/tools/bench/celery_probe/Dockerfile" \
  "$ROOT/tools/bench/celery_probe/drive.py" \
  "$ROOT/tools/bench/celery_probe/tasks.py" \
  owner@192.168.68.20:C:/Users/Owner/dev/xycalc/tools/bench/celery_probe/

# Runner bat for WSL (Docker Desktop Linux engine)
python - <<'PY'
from pathlib import Path
bat = r'''@echo off
setlocal
set OUT=C:\Users\Owner\xycalc-results\colocation-share
mkdir "%OUT%" 2>nul
wsl -e bash -lc "cd /mnt/c/Users/Owner/dev/xycalc/tools/bench/colocation_probe && chmod +x share_sweep.sh run.sh && MONGO_MEM_GB=8 SHARE_PCTS=50,60,70,80 OVERSUB=2.5 REDIS_MEM=4g CLICKHOUSE_MEM=8g WORKER_MEM=2g OUTDIR=/mnt/c/Users/Owner/xycalc-results/colocation-share ./share_sweep.sh" > "%OUT%\sweep.log" 2>&1
echo DONE >> "%OUT%\sweep.log"
'''
p = Path(r'C:/Users/gmhow/dev/xycalc/tools/bench/reef_run_t11_share.bat')
p.write_bytes(bat.replace('\n','\r\n').encode())
print('wrote', p)
PY

scp -o BatchMode=yes "$ROOT/tools/bench/reef_run_t11_share.bat" \
  owner@192.168.68.20:C:/Users/Owner/Desktop/RUN-T11-SHARE.bat

# Launch via WMI so it survives SSH disconnect
ssh -o BatchMode=yes owner@192.168.68.20 'powershell -NoProfile -Command "$r=Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='\''cmd.exe /c C:\\Users\\Owner\\Desktop\\RUN-T11-SHARE.bat'\''}; \"Return=$($r.ReturnValue) Pid=$($r.ProcessId)\""'
echo 'T11 share sweep launched'
