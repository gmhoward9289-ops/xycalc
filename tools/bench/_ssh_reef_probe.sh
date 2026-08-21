#!/usr/bin/env bash
set -euo pipefail
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c docker version'
echo '---'
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c dir C:\Users\Owner\dev\xycalc'
echo '---'
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c wsl -l -v'
echo '---'
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c wsl -e bash -lc "uname -a; free -g | head -2; which docker; docker version --format Server={{.Server.Os}}"'
