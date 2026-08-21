#!/usr/bin/env bash
# One-shot: verify host + sync readiness for cache_cliff on swamplink.
set -euo pipefail
echo "=== host ==="
hostname
uname -a
free -h | head -2
df -h / /var/lib/docker | tail -n +1
echo "=== device ==="
df --output=source / | tail -1
lsblk -d -o NAME,SIZE,TYPE
echo "=== harness ==="
ls -la /root/dev/xycalc/tools/bench/
wc -c /root/dev/xycalc/tools/bench/cache_cliff_probe.sh \
      /root/dev/xycalc/tools/bench/cache_cliff_probe.py || true
echo "=== docker ==="
docker version --format 'Server {{.Server.Os}} {{.Server.Version}}'
docker images --format '{{.Repository}}:{{.Tag}}' | grep -E '^(mongo|python):' || true
docker ps --format '{{.Names}}' | grep -E 'xycalc-(ticket|cache-cliff)' \
  && echo 'WARNING: competing probe' || echo 'no competing probes'
echo "=== results dir ==="
mkdir -p /root/dev/xycalc/results/cache-cliff
ls -la /root/dev/xycalc/results/
