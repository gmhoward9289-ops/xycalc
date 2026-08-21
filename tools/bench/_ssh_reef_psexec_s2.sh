#!/usr/bin/env bash
set -euo pipefail
# Session 2 is the active RDP session post-reboot
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c qwinsta'
echo '---'
# PsExec into session 2
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c C:\Users\Owner\lab\PsExec64.exe -accepteula -i 2 -d "C:\Program Files\Docker\Docker\Docker Desktop.exe"'
sleep 5
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c tasklist /FI "IMAGENAME eq Docker Desktop.exe" /V'
echo '---'
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c docker desktop status' || true
