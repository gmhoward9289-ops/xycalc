#!/usr/bin/env bash
# Quick docker status on reef (one shot).
set -euo pipefail
ssh -o BatchMode=yes -o ConnectTimeout=10 owner@192.168.68.20 'cmd /c docker info' 2>&1 | tail -30
echo EXIT:$?
ssh -o BatchMode=yes -o ConnectTimeout=10 owner@192.168.68.20 'cmd /c tasklist /FI IMAGENAME eq \"Docker Desktop.exe\"' 2>&1 | tail -10
