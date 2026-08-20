#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
docker compose down -v --remove-orphans >/dev/null 2>&1 || true
docker compose up -d --build redis mongo worker
sleep 5
docker compose ps
docker compose run --rm --no-deps -T driver python -c 'import socket; print("mongo", socket.gethostbyname("mongo"))'
env PROBE_ACKS_LATE=1 PROBE_PREFETCH=16 PROBE_RATES=200 PROBE_SECONDS=30 \
  docker compose run --rm --no-deps -T driver python drive.py
