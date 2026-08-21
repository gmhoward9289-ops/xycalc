#!/usr/bin/env bash
set -euo pipefail
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c docker desktop status'
echo '---'
ssh -o BatchMode=yes owner@192.168.68.20 "cmd /c docker info"
echo '---'
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c docker ps'
echo '---'
ssh -o BatchMode=yes owner@192.168.68.20 'cmd /c docker version'
