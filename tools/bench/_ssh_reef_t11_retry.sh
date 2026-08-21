#!/usr/bin/env bash
set -euo pipefail
scp -o BatchMode=yes /c/Users/gmhow/dev/xycalc/tools/bench/reef_run_t11_share.bat \
  owner@192.168.68.20:C:/Users/Owner/lab/RUN-T11-SHARE.bat
ssh -o BatchMode=yes owner@192.168.68.20 'powershell -NoProfile -Command "$r=Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='\''cmd.exe /c C:\\Users\\Owner\\lab\\RUN-T11-SHARE.bat'\''}; \"Return=$($r.ReturnValue) Pid=$($r.ProcessId)\""'
sleep 40
echo '=== log tail ==='
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c type C:\Users\Owner\xycalc-results\colocation-share\sweep.log'
echo '=== docker ps ==='
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c docker ps --format {{.Names}}'
