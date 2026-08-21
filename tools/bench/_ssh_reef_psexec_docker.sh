#!/usr/bin/env bash
# Launch Docker Desktop into reef's active RDP session (ID 1), then poll.
set -euo pipefail
remote() { ssh -o BatchMode=yes owner@192.168.68.20 "cmd /c $1"; }

echo "== sessions =="
remote 'qwinsta'
echo "== kill stuck docker gui (keep service) =="
remote 'taskkill /F /IM "Docker Desktop.exe"' || true
sleep 2
echo "== PsExec into session 1 =="
# -i 1 = interactive session 1 (rdp-tcp#0 Owner)
# -d = don't wait
remote 'C:\Users\Owner\lab\PsExec64.exe -accepteula -i 1 -d "C:\Users\Owner\Desktop\START-DOCKER.bat"'
echo "== poll docker engine =="
for i in $(seq 1 40); do
  if remote 'docker info -f {{.ServerVersion}}' 2>/dev/null | grep -qE '^[0-9]'; then
    echo "READY after try $i"
    remote 'docker info -f OSType={{.OSType}} MemTotal={{.MemTotal}} ServerVersion={{.ServerVersion}}'
    exit 0
  fi
  echo "try $i ..."
  sleep 5
done
echo "FAILED"
remote 'tasklist /FI "IMAGENAME eq Docker Desktop.exe"'
remote 'sc query com.docker.service'
remote 'docker info'
exit 1
