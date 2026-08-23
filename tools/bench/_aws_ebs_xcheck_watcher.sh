#!/usr/bin/env bash
# Poll until xcheck monitor reports TEARDOWN=OK / FAIL / TIMEOUT.
set -u
ROOT=/c/Users/gmhow/dev/xycalc
STAGE="${T9C_STAGE:-}"
if [ -z "$STAGE" ] && [ -f "$ROOT/tmp/t9c-latest-stage.txt" ]; then
  STAGE=$(tr -d '\r\n' < "$ROOT/tmp/t9c-latest-stage.txt")
fi
if [ -z "$STAGE" ]; then
  echo "No stage — launch first or set T9C_STAGE" >&2
  exit 1
fi
LOG="$STAGE/watcher.log"
MON=/c/Users/gmhow/dev/xycalc/tools/bench/_aws_ebs_xcheck_monitor.sh
INTERVAL="${1:-60}"

export T9C_STAGE="$STAGE"
echo "$(date -Iseconds) xcheck watcher start interval=${INTERVAL}s stage=$STAGE" | tee -a "$LOG"
while true; do
  out=$(bash "$MON" 2>&1) || true
  echo "$(date -Iseconds)" >>"$LOG"
  echo "$out" >>"$LOG"
  echo "$out"
  if echo "$out" | grep -qx 'TEARDOWN=OK'; then
    echo "$(date -Iseconds) watcher exit teardown" | tee -a "$LOG"
    exit 0
  fi
  if echo "$out" | grep -q 'STATE=FAIL\|STATE=TIMEOUT'; then
    echo "$(date -Iseconds) terminal fail — monitor already tore down" | tee -a "$LOG"
    exit 1
  fi
  if echo "$out" | grep -qx 'STALLED_NEEDS_ATTENTION'; then
    echo "$(date -Iseconds) STALLED — keep polling (max-hours still enforced)" | tee -a "$LOG"
  fi
  sleep "$INTERVAL"
done
