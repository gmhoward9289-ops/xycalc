#!/usr/bin/env bash
# Bring up Docker Desktop on reef via the active RDP session.
set -euo pipefail
r() { ssh -o BatchMode=yes owner@192.168.68.20 "cmd /c $1"; }
rp() { ssh -o BatchMode=yes owner@192.168.68.20 "powershell -NoProfile -Command $1"; }

echo "== start Task Scheduler =="
r 'sc start Schedule' || true
sleep 2
r 'sc query Schedule'

echo "== WMI Create START-DOCKER.bat =="
rp "\$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='cmd.exe /c C:\\Users\\Owner\\Desktop\\START-DOCKER.bat'}; \$r | Format-List"

echo "== also PsExec -i 1 Desktop.exe =="
r 'C:\Users\Owner\lab\PsExec64.exe -accepteula -i 1 -d \"C:\Program Files\Docker\Docker\Docker Desktop.exe\"' || true

echo "== schtasks /IT if Schedule is up =="
r 'schtasks /Delete /TN xycalc-start-docker /F' || true
r 'schtasks /Create /TN xycalc-start-docker /TR C:\Users\Owner\Desktop\START-DOCKER.bat /SC ONCE /ST 23:59 /IT /F' || true
r 'schtasks /Run /TN xycalc-start-docker' || true

echo "== poll =="
for i in $(seq 1 36); do
  ver=$(r 'docker info -f {{.ServerVersion}}' 2>/dev/null | tr -d '\r' | tail -1 || true)
  if echo "$ver" | grep -qE '^[0-9]'; then
    echo "READY $ver after try $i"
    r 'docker info -f OSType={{.OSType}} MemTotal={{.MemTotal}}'
    exit 0
  fi
  echo "try $i ver=[$ver]"
  sleep 5
done
echo "still down"
r 'tasklist /FI "IMAGENAME eq Docker Desktop.exe"'
r 'tasklist /FI "IMAGENAME eq com.docker.backend.exe"'
exit 1
