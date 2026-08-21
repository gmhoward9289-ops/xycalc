#!/usr/bin/env bash
set -euo pipefail
ssh -o BatchMode=yes root@swamplink bash -s <<'EOF'
set -e
echo "stopping competing stacks (keep cache-cliff + production xycalc)"
# s3_stack compose project
if [ -d /root/dev/xycalc/tools/bench/s3_stack ]; then
  (cd /root/dev/xycalc/tools/bench/s3_stack && docker compose down --remove-orphans) || true
fi
# occ-band containers by name
docker ps -q --filter 'name=xycalc-occ-band' | xargs -r docker rm -f
docker ps -q --filter 'name=xycalc-s3-stack' | xargs -r docker rm -f
echo "remaining:"
docker ps --format '{{.Names}} {{.Status}}'
EOF
