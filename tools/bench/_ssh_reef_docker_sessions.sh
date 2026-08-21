#!/usr/bin/env bash
set -euo pipefail
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c tasklist /FI "IMAGENAME eq Docker Desktop.exe" /V'
echo '---'
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c docker info -f {{.ServerVersion}}'
echo '---'
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c sc query com.docker.service'
