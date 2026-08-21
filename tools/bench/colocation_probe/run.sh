#!/usr/bin/env bash
# Measure RSS for Mongo + Redis + ClickHouse + Celery colocated.
#   cd tools/bench/colocation_probe && bash ./run.sh
set -euo pipefail
cd "$(dirname "$0")"

OUT="${OUT:-./results.json}"
PY="${XYCALC_PY:-}"
if [ -z "$PY" ]; then
  if command -v python >/dev/null 2>&1; then PY=python
  elif command -v python3 >/dev/null 2>&1; then PY=python3
  else echo "need python" >&2; exit 1; fi
fi
# Phase samples: avoid /tmp on broken Git Bash layouts.
PHASEDIR="${TMPDIR:-.}/xycalc-colocation-$$"
mkdir -p "$PHASEDIR"

cleanup() {
  docker compose down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$PHASEDIR" >/dev/null 2>&1 || true
}
trap cleanup EXIT
cleanup
mkdir -p "$PHASEDIR"

echo "== phase 1: idle (containers up, no data loaded yet) ==" >&2
docker compose up -d --build mongo redis clickhouse worker >&2
sleep 5
"$PY" sample.py idle > "$PHASEDIR/phase_idle.json"

echo "== loading data into mongo (via celery_probe's driver) and clickhouse ==" >&2
docker compose --profile driver run --rm --no-deps -T driver python drive.py >&2 || true
docker compose exec -T clickhouse clickhouse-client --multiquery < clickhouse_load.sql >&2

echo "== phase 2: loaded (data resident, no concurrent traffic) ==" >&2
sleep 3
"$PY" sample.py loaded > "$PHASEDIR/phase_loaded.json"

echo "== phase 3: under light concurrent load ==" >&2
docker compose --profile driver run --rm --no-deps -T driver python drive.py &
DRIVER_PID=$!
sleep 5
"$PY" sample.py under_load > "$PHASEDIR/phase_under_load.json"
wait "$DRIVER_PID" || true

"$PY" - "$OUT" "$PHASEDIR" <<'PYEOF'
import json, sys, os
out, phasedir = sys.argv[1], sys.argv[2]
phases = ["idle", "loaded", "under_load"]
data = {p: json.load(open(os.path.join(phasedir, f"phase_{p}.json"))) for p in phases}
with open(out, "w") as f:
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
