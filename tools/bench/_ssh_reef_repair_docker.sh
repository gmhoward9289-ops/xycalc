#!/usr/bin/env bash
set -euo pipefail
# Ensure CRLF for Windows bat
unix2dos /c/Users/gmhow/dev/xycalc/tools/bench/reef_repair_and_docker.bat 2>/dev/null \
  || sed -i 's/$/\r/' /c/Users/gmhow/dev/xycalc/tools/bench/reef_repair_and_docker.bat

scp -o BatchMode=yes \
  /c/Users/gmhow/dev/xycalc/tools/bench/reef_repair_and_docker.bat \
  /c/Users/gmhow/dev/xycalc/tools/bench/reef_wmi_launch.ps1 \
  owner@192.168.68.20:C:/Users/Owner/lab/

ssh -o BatchMode=yes owner@192.168.68.20 \
  'powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\Owner\lab\reef_wmi_launch.ps1'
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c type C:\Users\Owner\lab\wmi-launch.out'

echo '== PsExec -s -i 1 =='
ssh -o BatchMode=yes owner@192.168.68.20 \
  'cmd /c C:\Users\Owner\lab\PsExec64.exe -accepteula -s -i 1 \"C:\Program Files\Docker\Docker\Docker Desktop.exe\"' || true

echo '== poll =='
for i in $(seq 1 36); do
  out=$(ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c docker info -f {{.ServerVersion}}' 2>/dev/null \
    | tr -d '\r' | grep -E '^[0-9]' | tail -1 || true)
  if [ -n "$out" ]; then
    echo "READY $out"
    exit 0
  fi
  # show progress stamps
  if [ $((i % 6)) -eq 0 ]; then
    ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c type C:\Users\Owner\lab\docker-launch.stamp' 2>/dev/null || true
    ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c tasklist /FI "IMAGENAME eq Docker Desktop.exe"' 2>/dev/null | tr -d '\r' | tail -3
  fi
  echo "try $i"
  sleep 5
done
echo FAILED
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c type C:\Users\Owner\lab\repair-schedule.out'
exit 1
