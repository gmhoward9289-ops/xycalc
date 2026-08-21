#!/usr/bin/env bash
set -euo pipefail
ssh -o BatchMode=yes -o ConnectTimeout=15 owner@192.168.68.20 'cmd /c taskkill /F /IM "Docker Desktop.exe" /T'
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c taskkill /F /IM com.docker.backend.exe /T' || true
ssh -o BatchMode=yes owner@192.168.68.20 'wsl.exe --shutdown' || true
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c sc stop com.docker.service' || true
sleep 3
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c sc start com.docker.service' || true
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c sc query com.docker.service'
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c docker desktop status' || true
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c tasklist /FI "IMAGENAME eq Docker Desktop.exe"'
