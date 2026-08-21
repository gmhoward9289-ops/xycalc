#!/usr/bin/env bash
# Start Docker Desktop on reef (Windows) and wait until the engine answers.
set -euo pipefail
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c "start \"\" \"C:\Program Files\Docker\Docker\Docker Desktop.exe\""' || true
echo "launched Desktop; polling..."
for i in $(seq 1 60); do
  if ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c "docker info -f {{.ServerVersion}}"' 2>/dev/null | grep -qE '^[0-9]'; then
    echo "docker ready after ${i} tries"
    ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c "docker info -f OSType={{.OSType}} MemTotal={{.MemTotal}}"'
    exit 0
  fi
  sleep 5
done
echo "docker still not ready after ~5 min" >&2
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c "docker info"' || true
exit 1
