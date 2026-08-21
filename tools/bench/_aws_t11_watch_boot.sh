#!/usr/bin/env bash
set -eux
SCRIPT=/c/Users/gmhow/dev/xycalc/tools/bench/_aws_t11_watcher.sh
LOGDIR=/c/Users/gmhow/dev/xycalc/tmp/xycalc-t11-20260821-0147
bash -n "$SCRIPT"
date -Iseconds
echo "probe $(date -Iseconds)" >>"$LOGDIR/watcher.log"
# show what orphan is doing
ps -ef | grep -E 't11_watcher|t11_monitor' | grep -v grep || true
# kill orphans and run one short poll cycle manually
pkill -f '_aws_t11_watcher.sh' 2>/dev/null || true
sleep 1
# run monitor once into log
bash /c/Users/gmhow/dev/xycalc/tools/bench/_aws_t11_monitor.sh | tee -a "$LOGDIR/watcher.log" | tail -5
# start detached via nohup from git bash itself
nohup bash "$SCRIPT" 900 >>"$LOGDIR/watcher.log" 2>&1 &
echo $! >"$LOGDIR/watcher.pid"
sleep 2
echo "pid=$(cat $LOGDIR/watcher.pid)"
ps -p "$(cat $LOGDIR/watcher.pid)" || true
tail -20 "$LOGDIR/watcher.log"
