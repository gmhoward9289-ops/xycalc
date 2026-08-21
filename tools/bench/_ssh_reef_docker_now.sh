#!/usr/bin/env bash
set -euo pipefail
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c type C:\Users\Owner\lab\docker-launch.stamp'
echo '---'
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c type C:\Users\Owner\lab\repair-schedule.out'
echo '---'
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c tasklist /FI "IMAGENAME eq Docker Desktop.exe"'
echo '---'
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c docker info'
echo '---'
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c qwinsta'
