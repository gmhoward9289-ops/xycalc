#!/usr/bin/env bash
set -euo pipefail
echo "waiting for reef to go down..."
for i in $(seq 1 40); do
  if ! ping -n 1 -w 1000 192.168.68.20 >/dev/null 2>&1; then
    echo "down at try $i"
    break
  fi
  sleep 2
done
echo "waiting for reef to come back..."
for i in $(seq 1 90); do
  if ping -n 1 -w 1000 192.168.68.20 >/dev/null 2>&1; then
    echo "ping ok try $i"
    if ssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new owner@192.168.68.20 'cmd /c echo UP' 2>/dev/null | grep -q UP; then
      echo "SSH UP"
      exit 0
    fi
  fi
  sleep 5
done
echo "TIMEOUT waiting for SSH"
exit 1
