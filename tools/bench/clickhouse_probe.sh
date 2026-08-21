#!/usr/bin/env bash
# Disposable ClickHouse insert/part-pressure probe for investigation 012 (T10).
#
#   ./tools/bench/clickhouse_probe.sh                 # full dual-image sweep
#   PROBE_SMOKE=1 ./tools/bench/clickhouse_probe.sh   # one image, short budget
#
# Starts a uniquely-named ClickHouse container (CPU/memory pinned). Prefers a
# host-side Python with clickhouse-connect (PROBE_LOCAL=1 or auto-detect) so
# bridge-network egress limits cannot block pip; falls back to a driver
# container on the same Docker network. Prints JSON after ===JSON=== (one
# object for smoke, {"images":[...]} for the dual sweep) and cleans up on exit.
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
HTTP_PORT="${PROBE_HTTP_PORT:-18123}"
CH_PASSWORD="${PROBE_PASSWORD:-xycalc}"
# Default ON for dual-image threshold confirmation — see clickhouse_probe.py.
# Set PROBE_STOP_MERGES=0 to test whether merges keep up on this box (Claim A).
STOP_MERGES="${PROBE_STOP_MERGES:-1}"

# Dual-image by default: one pre-23.6, one 23.6+. Smoke uses the post side only.
PRE_IMAGE="${PROBE_PRE_IMAGE:-clickhouse/clickhouse-server:23.3}"
POST_IMAGE="${PROBE_POST_IMAGE:-clickhouse/clickhouse-server:24.8}"
PY_IMAGE="${PROBE_PY_IMAGE:-python:3.12-slim}"

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
RESULTS_DIR="$(mktemp -d /tmp/ch-probe-XXXXXX)"

# Host venv / python with clickhouse-connect — preferred when Docker bridge
# cannot reach PyPI (common on restricted cloud agent networks).
LOCAL_PY=""
if [ "${PROBE_LOCAL:-auto}" != "0" ]; then
    for cand in "${PROBE_PYTHON:-}" "$repo/.venv/bin/python" python3; do
        [ -n "$cand" ] || continue
        if "$cand" -c 'import clickhouse_connect' >/dev/null 2>&1; then
            LOCAL_PY="$cand"
            break
        fi
    done
fi

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
    local out_json="$3"

    cleanup
    docker network create "$NET" >/dev/null

    {
        echo "image       $image  (expect $expect_side defaults)"
        echo "resources   ${CPUS} cpus, ${MEMORY} memory"
        echo "workload    ${ROWS} rows, batches=${BATCHES}, writers=${WRITERS}, step_cap=${STEP_CAP}s"
        echo "merges      STOP_MERGES=${STOP_MERGES} (1=isolate part-count ceilings)"
        if [ -n "$LOCAL_PY" ]; then
            echo "driver      local $LOCAL_PY (host→published :${HTTP_PORT})"
        else
            echo "driver      docker $PY_IMAGE on $NET"
        fi
    } >&2

    docker run -d --name "$NAME" --network "$NET" \
        --cpus="$CPUS" --memory "$MEMORY" --memory-swap "$MEMORY" \
        --ulimit nofile=262144:262144 \
        -e CLICKHOUSE_PASSWORD="$CH_PASSWORD" \
        -p "${HTTP_PORT}:8123" \
        "$image" >/dev/null

    # Wait for HTTP ping rather than sleeping and hoping.
    for _ in $(seq 1 90); do
        if docker exec "$NAME" wget -q -O- 'http://127.0.0.1:8123/ping' \
            2>/dev/null | grep -q Ok; then
            break
        fi
        if docker exec "$NAME" clickhouse-client -q 'SELECT 1' \
            >/dev/null 2>&1; then
            break
        fi
        # Host-side readiness when using local driver.
        if curl -fsS "http://127.0.0.1:${HTTP_PORT}/ping" 2>/dev/null | grep -q Ok; then
            break
        fi
        sleep 1
    done

    local raw
    if [ -n "$LOCAL_PY" ]; then
        raw="$(
            PROBE_HOST=127.0.0.1 \
            PROBE_PORT="$HTTP_PORT" \
            PROBE_PASSWORD="$CH_PASSWORD" \
            PROBE_EXPECT_SIDE="$expect_side" \
            PROBE_IMAGE="$image" \
            PROBE_CPUS="$CPUS" \
            PROBE_MEMORY="$MEMORY" \
            PROBE_WRITERS="$WRITERS" \
            PROBE_ROWS="$ROWS" \
            PROBE_STEP_CAP_S="$STEP_CAP" \
            PROBE_BATCHES="$BATCHES" \
            PROBE_STOP_MERGES="$STOP_MERGES" \
            "$LOCAL_PY" "$here/clickhouse_probe.py"
        )"
    else
        docker run -d --name "$DRIVER" --network "$NET" \
            "$PY_IMAGE" sleep infinity >/dev/null
        # Host-network pip when bridge egress is broken (Errno 101).
        if ! docker exec "$DRIVER" pip install --quiet --no-cache-dir \
                clickhouse-connect >/dev/null 2>&1; then
            echo "bridge pip failed; retrying install via host network..." >&2
            docker rm -f "$DRIVER" >/dev/null 2>&1 || true
            docker run -d --name "$DRIVER" --network host \
                "$PY_IMAGE" sleep infinity >/dev/null
            docker exec "$DRIVER" pip install --quiet --no-cache-dir \
                clickhouse-connect >&2
            # Host-network driver talks to published port on localhost.
            docker cp "$here/clickhouse_probe.py" "$DRIVER:/tmp/clickhouse_probe.py"
            raw="$(
                docker exec \
                    -e PROBE_HOST=127.0.0.1 \
                    -e PROBE_PORT="$HTTP_PORT" \
                    -e PROBE_PASSWORD="$CH_PASSWORD" \
                    -e PROBE_EXPECT_SIDE="$expect_side" \
                    -e PROBE_IMAGE="$image" \
                    -e PROBE_CPUS="$CPUS" \
                    -e PROBE_MEMORY="$MEMORY" \
                    -e PROBE_WRITERS="$WRITERS" \
                    -e PROBE_ROWS="$ROWS" \
                    -e PROBE_STEP_CAP_S="$STEP_CAP" \
                    -e PROBE_BATCHES="$BATCHES" \
                    -e PROBE_STOP_MERGES="$STOP_MERGES" \
                    "$DRIVER" python /tmp/clickhouse_probe.py
            )"
        else
            docker cp "$here/clickhouse_probe.py" "$DRIVER:/tmp/clickhouse_probe.py"
            raw="$(
                docker exec \
                    -e PROBE_HOST="$NAME" \
                    -e PROBE_PASSWORD="$CH_PASSWORD" \
                    -e PROBE_EXPECT_SIDE="$expect_side" \
                    -e PROBE_IMAGE="$image" \
                    -e PROBE_CPUS="$CPUS" \
                    -e PROBE_MEMORY="$MEMORY" \
                    -e PROBE_WRITERS="$WRITERS" \
                    -e PROBE_ROWS="$ROWS" \
                    -e PROBE_STEP_CAP_S="$STEP_CAP" \
                    -e PROBE_BATCHES="$BATCHES" \
                    -e PROBE_STOP_MERGES="$STOP_MERGES" \
                    "$DRIVER" python /tmp/clickhouse_probe.py
            )"
        fi
    fi

    printf '%s\n' "$raw" | awk 'f;/^===JSON===$/{f=1}' > "$out_json"
    # Also stream the marker+JSON for live followers.
    printf '%s\n' "$raw"
}

if [ "${PROBE_SMOKE:-0}" = "1" ]; then
    ROWS="${PROBE_ROWS:-30000}"
    BATCHES="${PROBE_BATCHES:-1,100,1000}"
    STEP_CAP="${PROBE_STEP_CAP_S:-30}"
    export PROBE_ROWS="$ROWS" PROBE_BATCHES="$BATCHES" PROBE_STEP_CAP_S="$STEP_CAP"
    run_one "$POST_IMAGE" post23_6 "$RESULTS_DIR/post.json"
else
    run_one "$PRE_IMAGE" pre23_6 "$RESULTS_DIR/pre.json"
    echo >&2
    # Different host port for the second container (first cleaned up, but keep explicit).
    HTTP_PORT="${PROBE_HTTP_PORT_POST:-$((HTTP_PORT + 1))}"
    run_one "$POST_IMAGE" post23_6 "$RESULTS_DIR/post.json"

    # Combine + assert settings differ (guard 6 across images).
    python3 - "$RESULTS_DIR" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
docs = []
for name in ("pre.json", "post.json"):
    p = root / name
    docs.append(json.loads(p.read_text(encoding="utf-8")))
a, b = docs[0]["settings"], docs[1]["settings"]
keys = ("parts_to_delay_insert", "parts_to_throw_insert")
if all(a.get(k) == b.get(k) for k in keys):
    print(
        f"REFUSING: both images report identical parts_to_* settings { {k: a.get(k) for k in keys} }",
        file=sys.stderr,
    )
    sys.exit(2)
print("===JSON===")
print(json.dumps({"probe": "clickhouse_probe", "images": docs, "settingsDiffer": True}, indent=2))
PY
fi
