#!/usr/bin/env bash
# Redis broker maxmemory eviction probe (issue #15 / roadmap T7).
#
#   ./run_evict.sh
#   PROBE_MAXMEMORY_POLICY=allkeys-lru ./run_evict.sh
#   PROBE_ENQUEUE_ATTEMPTS=500 PROBE_MAXMEMORY=32mb ./run_evict.sh   # smoke
#
# Sweep all three policies (plan default):
#   for policy in noeviction allkeys-lru volatile-lru; do
#     PROBE_MAXMEMORY_POLICY=$policy ./run_evict.sh
#   done
set -euo pipefail
cd "$(dirname "$0")"

cleanup() { docker compose --profile evict down -v --remove-orphans >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup

export PROBE_MAXMEMORY="${PROBE_MAXMEMORY:-16mb}"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-xycalc-evict-probe}"

docker compose --profile evict up -d --build redis bookkeeping >&2
docker compose --profile evict run --rm --no-deps --build -T evict-driver python evict_probe.py
