#!/usr/bin/env bash
set -ux
OUT=/c/Users/gmhow/dev/xycalc/tools/bench/_t11_pull
mkdir -p "$OUT"
scp -o BatchMode=yes owner@192.168.68.20:C:/Users/Owner/xycalc-results/colocation-share/\* "$OUT/" || true
ls -la "$OUT"
echo '=== sweep ==='
tr -d '\000' < "$OUT/sweep.log" 2>/dev/null | tail -40 || true
echo '=== summary ==='
cat "$OUT/summary.jsonl" 2>/dev/null || true
echo '=== docker ==='
ssh -o BatchMode=yes owner@192.168.68.20 'docker ps --format {{.Names}}'
