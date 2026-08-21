#!/usr/bin/env bash
set -euo pipefail
for i in $(seq 1 24); do
  st=$(ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c docker desktop status' 2>/dev/null | tr -d '\r' | grep -i Status | head -1 || true)
  ver=$(ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c docker info -f {{.ServerVersion}}' 2>/dev/null | tr -d '\r' | grep -E '^[0-9]' | head -1 || true)
  sess=$(ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c tasklist /FI "IMAGENAME eq Docker Desktop.exe"' 2>/dev/null | tr -d '\r' | grep -c Docker || true)
  echo "try $i st=[$st] ver=[$ver] procs=$sess"
  if echo "$ver" | grep -qE '^[0-9]'; then
    echo READY
    ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c docker info -f OSType={{.OSType}} MemTotal={{.MemTotal}}'
    exit 0
  fi
  sleep 10
done
exit 1
