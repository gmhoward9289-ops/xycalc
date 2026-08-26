#!/bin/sh
# Grow Redis RSS inside its own cgroup (neighbor anon). Does not share
# Mongo's memory.max. Watch cadvisor: redis container_memory_rss up,
# mongo container_memory_cache may fall only if the *host/parent* is tight.
#
#   sh scripts/neighbor_fill.sh
# After it finishes, prints a Grafana time-range URL for the Redis board
# (empty unless Prometheus scraped this run).

set -e
START=$(date +%s)
MB="${XYLAB_REDIS_MB:-400}"
echo "filling redis with ~${MB} MiB of keys"
i=0
while [ "$i" -lt "$MB" ]; do
  # ~1 MiB payload per key
  docker exec xycalc-lab-redis redis-cli -x set "hog:$i" < /dev/zero 2>/dev/null || \
    docker exec xycalc-lab-redis redis-cli set "hog:$i" "$(python -c 'print(\"x\"*1048576)')"
  i=$((i + 1))
done
docker exec xycalc-lab-redis redis-cli info memory | grep -E 'used_memory_human|maxmemory_human|used_memory_rss_human'
END=$(date +%s)
py="${XYCALC_PYTHON:-python3}"
link="$(dirname "$0")/../grafana_link.py"
if [ -f "$link" ]; then
  echo "=== watch live (empty unless this host is scraped) ==="
  "$py" "$link" --uid xycalc-redis-celery --from-ts "$START" --to-ts "$END" || true
fi
