#!/usr/bin/env bash
# Detached AWS T11 watcher: poll until DONE, pull, terminate. Survives Cursor shell kills.
set -u
LOG=/c/Users/gmhow/dev/xycalc/tmp/xycalc-t11-20260821-0147/watcher.log
MON=/c/Users/gmhow/dev/xycalc/tools/bench/_aws_t11_monitor.sh
INTERVAL="${1:-900}"

echo "$(date -Iseconds) watcher start interval=${INTERVAL}s" >>"$LOG"
while true; do
  out=$(bash "$MON" 2>&1) || true
  echo "$(date -Iseconds)" >>"$LOG"
  echo "$out" >>"$LOG"
  # Match whole lines only — never set -x noise like "+ grep STATE=DONE"
  if echo "$out" | grep -qx 'TEARDOWN=OK'; then
    echo "$(date -Iseconds) watcher exit teardown" >>"$LOG"
    exit 0
  fi
  if echo "$out" | grep -qx 'STATE=DONE'; then
    echo "$(date -Iseconds) STATE=DONE seen — re-running monitor for pull/terminate" >>"$LOG"
    bash "$MON" >>"$LOG" 2>&1 || true
    echo "$(date -Iseconds) watcher exit teardown" >>"$LOG"
    exit 0
  fi
  if echo "$out" | grep -qx 'STALLED_NEEDS_ATTENTION'; then
    echo "$(date -Iseconds) STALLED — will keep polling" >>"$LOG"
  fi
  sleep "$INTERVAL"
done
