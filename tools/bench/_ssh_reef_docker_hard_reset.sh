#!/usr/bin/env bash
# Hard reset Docker Desktop on reef, then wait for engine.
set -euo pipefail
r() { ssh -o BatchMode=yes owner@192.168.68.20 "cmd /c $1"; }

echo "== stop desktop =="
r 'docker desktop stop' || true
sleep 3
echo "== kill docker procs =="
r 'taskkill /F /IM "Docker Desktop.exe"' || true
r 'taskkill /F /IM "com.docker.backend.exe"' || true
r 'taskkill /F /IM "com.docker.service.exe"' || true
sleep 2
echo "== wsl shutdown =="
# careful quoting — no trailing quote after --shutdown
ssh -o BatchMode=yes owner@192.168.68.20 'wsl.exe --shutdown' || true
sleep 3
echo "== restart com.docker.service =="
r 'sc stop com.docker.service' || true
sleep 2
r 'sc start com.docker.service' || true
sleep 3
r 'sc query com.docker.service'
echo "== stamp for RDP launch =="
r 'echo waiting_for_rdp_launch> C:\Users\Owner\lab\docker-reset.stamp'
echo 'RESET_DONE — launch Docker Desktop.exe in RDP session next'
