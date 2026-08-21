#!/usr/bin/env bash
# Attempt to recover Docker Desktop on reef.
set -euo pipefail
remote() { ssh -o BatchMode=yes -o ConnectTimeout=15 owner@192.168.68.20 "cmd /c $1"; }

echo "== stop Desktop / com.docker =="
remote 'taskkill /F /IM "Docker Desktop.exe"' || true
remote 'taskkill /F /IM "com.docker.backend.exe"' || true
remote 'wsl --shutdown' || true
sleep 3
echo "== start Desktop =="
remote 'start \"\" \"C:\Program Files\Docker\Docker\Docker Desktop.exe\"'
echo "== poll 90s =="
for i in $(seq 1 18); do
  if remote 'docker info -f {{.ServerVersion}}' 2>/dev/null | grep -qE '^[0-9]'; then
    echo "READY try=$i"
    remote 'docker info -f OSType={{.OSType}} MemTotal={{.MemTotal}}'
    exit 0
  fi
  echo "try $i not ready"
  sleep 5
done
echo "FAILED"
remote 'docker info' || true
exit 1
