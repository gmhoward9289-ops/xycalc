#!/usr/bin/env bash
# Bring up Mongo 7 + Redis + Celery + ClickHouse(S3/MinIO) and prove the
# stack works: each service answers, and ClickHouse MergeTree parts sit on
# the s3 disk (not default local).
#
#   cd tools/bench/s3_stack && ./run.sh
#
# Host: Ubuntu 22.04 (or similar) with Docker Engine + Compose v2.
# Leaves the stack running on success; pass --down to tear down after smoke.
set -euo pipefail
cd "$(dirname "$0")"

DOWN_AFTER=0
if [[ "${1:-}" == "--down" ]]; then
  DOWN_AFTER=1
fi

COMPOSE=(docker compose -f compose.yml)
if [[ "${SKIP_MINIO:-0}" == "1" ]]; then
  COMPOSE+=(-f compose.external-s3.yml)
  if [[ -z "${CLICKHOUSE_STORAGE_XML:-}" ]]; then
    echo "SKIP_MINIO=1 requires CLICKHOUSE_STORAGE_XML pointing at a real-bucket config" >&2
    exit 2
  fi
fi

cleanup() {
  if [[ "$DOWN_AFTER" -eq 1 ]]; then
    "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "== compose up (mongo, redis, clickhouse, celery worker${SKIP_MINIO:+; external S3}) ==" >&2
"${COMPOSE[@]}" up -d --build >&2

echo "== wait for clickhouse ==" >&2
for i in $(seq 1 60); do
  if "${COMPOSE[@]}" exec -T clickhouse clickhouse-client --query "SELECT 1" >/dev/null 2>&1; then
    break
  fi
  if [[ "$i" -eq 60 ]]; then
    echo "clickhouse never became ready" >&2
    "${COMPOSE[@]}" logs --tail=80 clickhouse >&2 || true
    exit 1
  fi
  sleep 1
done

echo "== mongo ping ==" >&2
"${COMPOSE[@]}" exec -T mongo mongosh --quiet --eval 'db.runCommand({ ping: 1 })' >&2

echo "== redis ping ==" >&2
"${COMPOSE[@]}" exec -T redis redis-cli ping >&2

echo "== celery worker ping ==" >&2
PING_OUT=$("${COMPOSE[@]}" exec -T worker celery -A tasks inspect ping --timeout 10 2>&1) || true
echo "$PING_OUT" >&2
if ! echo "$PING_OUT" | grep -qi 'pong'; then
  echo "FAIL: celery worker did not answer inspect ping" >&2
  "${COMPOSE[@]}" logs --tail=40 worker >&2 || true
  exit 1
fi

echo "== clickhouse s3-backed MergeTree smoke ==" >&2
PARTS_OUT=$("${COMPOSE[@]}" exec -T clickhouse clickhouse-client --multiquery < sql/smoke.sql)
echo "$PARTS_OUT"

if ! echo "$PARTS_OUT" | grep -qE '\bs3\b'; then
  echo "FAIL: expected system.parts.disk_name = s3 for xycalc_s3.events" >&2
  exit 1
fi

if [[ "${SKIP_MINIO:-0}" != "1" ]]; then
  echo "== minio object listing (bucket should be non-empty) ==" >&2
  OBJECTS=$("${COMPOSE[@]}" run --rm --no-deps \
    -e MINIO_ROOT_USER="${MINIO_ROOT_USER:-minioadmin}" \
    -e MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-minioadminpassword}" \
    -e S3_BUCKET="${S3_BUCKET:-clickhouse}" \
    --entrypoint /bin/sh createbuckets -c '
      mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
      mc ls --recursive "local/$S3_BUCKET" | head -n 20
    ')
  echo "$OBJECTS"
  if [[ -z "$(echo "$OBJECTS" | tr -d '[:space:]')" ]]; then
    echo "FAIL: MinIO bucket is empty after ClickHouse insert" >&2
    exit 1
  fi
fi

echo "== OK: mongo7 + redis + celery + clickhouse(s3) stack is up ==" >&2
if [[ "$DOWN_AFTER" -eq 0 ]]; then
  echo "stack left running. Tear down with: ${COMPOSE[*]} down -v" >&2
fi
