#!/usr/bin/env bash
# Disposable ClickHouse insert/part-pressure probe for investigation 012 (T10).
#
#   ./tools/bench/clickhouse_probe.sh                 # full dual-image sweep
#   PROBE_SMOKE=1 ./tools/bench/clickhouse_probe.sh   # one image, short budget
#
# Starts a uniquely-named ClickHouse container (CPU/memory pinned), runs
# tools/bench/clickhouse_probe.py from a sibling Python driver container with
# a persistent clickhouse-connect client, prints JSON after ===JSON===, and
# removes both containers + network on exit.
#
# Do NOT trust image tags for parts_to_* defaults — the Python harness queries
# system.merge_tree_settings and refuses to conclude if the live values do not
# match the expected side of the 23.6 boundary.
set -euo pipefail

NAME="${PROBE_NAME:-xycalc-ch-probe-$$-$(date +%s)}"
NET="${NAME}-net"
DRIVER="${NAME}-driver"
CPUS="${PROBE_CPUS:-2}"
MEMORY="${PROBE_MEMORY:-2g}"
WRITERS="${PROBE_WRITERS:-8}"
ROWS="${PROBE_ROWS:-300000}"
STEP_CAP="${PROBE_STEP_CAP_S:-120}"
BATCHES="${PROBE_BATCHES:-1,10,100,1000,10000,100000}"

# Dual-image by default: one pre-23.6, one 23.6+. Smoke uses the post side only.
PRE_IMAGE="${PROBE_PRE_IMAGE:-clickhouse/clickhouse-server:23.3}"
POST_IMAGE="${PROBE_POST_IMAGE:-clickhouse/clickhouse-server:24.8}"
PY_IMAGE="${PROBE_PY_IMAGE:-python:3.12-slim}"

here="$(cd "$(dirname "$0")" && pwd)"

cleanup() {
    docker rm -f "$DRIVER" "$NAME" >/dev/null 2>&1 || true
    docker network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT

if docker ps --format '{{.Names}}' | grep -q '^xycalc-ch-probe'; then
    echo "note: another clickhouse_probe run is active on this host. Proceeding —" >&2
    echo "      names are unique per run — but the two will contend for CPU." >&2
fi

run_one() {
    local image="$1"
    local expect_side="$2"   # pre23_6 | post23_6

    cleanup
    docker network create "$NET" >/dev/null

    {
        echo "image       $image  (expect $expect_side defaults)"
        echo "resources   ${CPUS} cpus, ${MEMORY} memory"
        echo "workload    ${ROWS} rows, batches=${BATCHES}, writers=${WRITERS}, step_cap=${STEP_CAP}s"
    } >&2

    docker run -d --name "$NAME" --network "$NET" \
        --cpus="$CPUS" --memory "$MEMORY" --memory-swap "$MEMORY" \
        --ulimit nofile=262144:262144 \
        "$image" >/dev/null

    # Wait for HTTP ping rather than sleeping and hoping.
    for _ in $(seq 1 60); do
        if docker exec "$NAME" wget -q -O- 'http://127.0.0.1:8123/ping' \
            2>/dev/null | grep -q Ok; then
            break
        fi
        # Some images lack wget; fall back to clickhouse-client.
        if docker exec "$NAME" clickhouse-client -q 'SELECT 1' \
            >/dev/null 2>&1; then
            break
        fi
        sleep 1
    done

    docker run -d --name "$DRIVER" --network "$NET" \
        "$PY_IMAGE" sleep infinity >/dev/null

    docker exec "$DRIVER" pip install --quiet --no-cache-dir clickhouse-connect >&2
    docker cp "$here/clickhouse_probe.py" "$DRIVER:/tmp/clickhouse_probe.py"

    docker exec \
        -e PROBE_HOST="$NAME" \
        -e PROBE_EXPECT_SIDE="$expect_side" \
        -e PROBE_IMAGE="$image" \
        -e PROBE_CPUS="$CPUS" \
        -e PROBE_MEMORY="$MEMORY" \
        -e PROBE_WRITERS="$WRITERS" \
        -e PROBE_ROWS="$ROWS" \
        -e PROBE_STEP_CAP_S="$STEP_CAP" \
        -e PROBE_BATCHES="$BATCHES" \
        "$DRIVER" python /tmp/clickhouse_probe.py
}

if [ "${PROBE_SMOKE:-0}" = "1" ]; then
    ROWS="${PROBE_ROWS:-30000}"
    BATCHES="${PROBE_BATCHES:-1,100,1000}"
    STEP_CAP="${PROBE_STEP_CAP_S:-30}"
    export PROBE_ROWS="$ROWS" PROBE_BATCHES="$BATCHES" PROBE_STEP_CAP_S="$STEP_CAP"
    run_one "$POST_IMAGE" post23_6
else
    run_one "$PRE_IMAGE" pre23_6
    echo >&2
    run_one "$POST_IMAGE" post23_6
fi
