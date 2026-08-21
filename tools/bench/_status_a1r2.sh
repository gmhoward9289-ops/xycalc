#!/usr/bin/env bash
set -euo pipefail
ssh -o BatchMode=yes root@swamplink bash -s <<'EOF'
echo "=== a1-r2 ==="
ps -p 2047602 -o pid,etime,cmd 2>/dev/null || echo "runner gone"
grep -E 'ratio |ops/s|finished|failed|=== ratio' /root/dev/xycalc/results/cache-cliff/a1-r2.log | tail -40
echo "=== disk ==="
df -h / | tail -1
EOF
