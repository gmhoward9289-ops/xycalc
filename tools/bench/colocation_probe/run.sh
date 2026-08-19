#!/usr/bin/env bash
# Measure what Mongo + Redis + ClickHouse + Celery actually hold in RAM when
# colocated on one host, at three phases: idle, after each service has real
# data, and under light concurrent load. See README.md for what question this
# answers and how the result feeds back into the corpus.
#
#   cd tools/bench/colocation_probe && ./run.sh
#
# Sizing knobs (env vars, same shape as celery_probe):
#   MONGO_MEM, REDIS_MEM, CLICKHOUSE_MEM, WORKER_MEM   per-container cgroup caps
#   MONGO_CACHE_GB                                     WiredTiger cache size
#   PROBE_DOCS                                         Mongo doc count for the worker/driver
set -euo pipefail
cd "$(dirname "$0")"

OUT="${OUT:-./results.json}"
cleanup() { docker compose down -v --remove-orphans >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup

echo "== phase 1: idle (containers up, no data loaded yet) ==" >&2
docker compose up -d --build mongo redis clickhouse worker >&2
sleep 5   # let each service finish its own startup before sampling "idle"
python3 sample.py idle > /tmp/phase_idle.json

echo "== loading data into mongo (via celery_probe's driver) and clickhouse ==" >&2
docker compose --profile driver run --rm --no-deps -T driver python drive.py >&2 || true
docker compose exec -T clickhouse clickhouse-client --multiquery < clickhouse_load.sql >&2

echo "== phase 2: loaded (data resident, no concurrent traffic) ==" >&2
sleep 3
python3 sample.py loaded > /tmp/phase_loaded.json

echo "== phase 3: under light concurrent load ==" >&2
docker compose --profile driver run --rm --no-deps -T driver python drive.py &
DRIVER_PID=$!
sleep 5
python3 sample.py under_load > /tmp/phase_under_load.json
wait "$DRIVER_PID" || true

python3 - "$OUT" <<'PYEOF'
import json, sys
phases = ["idle", "loaded", "under_load"]
data = {p: json.load(open(f"/tmp/phase_{p}.json")) for p in phases}
with open(sys.argv[1], "w") as f:
    json.dump(data, f, indent=2)

print("\n=== RSS by service across phases ===")
services = sorted({s for p in data.values() for s in p["services"]})
print(f"{'service':<12}" + "".join(f"{p:>16}" for p in phases))
for s in services:
    row = f"{s:<12}"
    for p in phases:
        v = data[p]["services"].get(s, {}).get("mem_used", "-")
        row += f"{v:>16}"
    print(row)
PYEOF

echo "wrote $OUT" >&2
