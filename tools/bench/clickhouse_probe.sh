#!/usr/bin/env bash
# Issue #18 / T10 — ClickHouse insert-frequency / parts ceiling.
#
# Pins two images (pre-23.6 and 23.6+) and asserts their
# parts_to_delay_insert / parts_to_throw_insert defaults differ before sweeping.
#
#   ./tools/bench/clickhouse_probe.sh
#   PROBE_ROWS=50000 PROBE_BATCHES=1,10,100 PROBE_IMAGES=23.3,24.8 \
#     PROBE_STEP_TIMEOUT=30 ./tools/bench/clickhouse_probe.sh   # smoke
set -euo pipefail

NAME="${PROBE_NAME:-xycalc-ch-probe-$$-$(date +%s)}"
PY_IMAGE="${PROBE_PY_IMAGE:-python:3.12-slim}"
IMAGES="${PROBE_IMAGES:-clickhouse/clickhouse-server:23.3,clickhouse/clickhouse-server:24.8}"
CPUS="${PROBE_CPUS:-2}"
MEMORY="${PROBE_MEMORY:-2g}"

here="$(cd "$(dirname "$0")" && pwd)"

cleanup() {
    # shellcheck disable=SC2086
    for img in ${NAME}-23 ${NAME}-24 ${NAME}-a ${NAME}-b ${NAME}-driver; do
        docker rm -f "$img" >/dev/null 2>&1 || true
    done
    docker rm -f "$NAME-driver" >/dev/null 2>&1 || true
    # Remove any containers we started with prefix.
    docker ps -aq --filter "name=${NAME}" | xargs -r docker rm -f >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker run -d --name "${NAME}-driver" "$PY_IMAGE" sleep infinity >/dev/null
docker exec "${NAME}-driver" pip install --quiet --no-cache-dir clickhouse-connect >&2
docker cp "$here/clickhouse_probe.py" "${NAME}-driver:/tmp/clickhouse_probe.py"

IFS=',' read -r -a imgs <<< "$IMAGES"
results_dir="$(mktemp -d)"
i=0
for image in "${imgs[@]}"; do
    image="${image// /}"
    cname="${NAME}-ch${i}"
    echo "=== image $image as $cname ===" >&2
    docker run -d --name "$cname" \
        --cpus="$CPUS" --memory="$MEMORY" --memory-swap="$MEMORY" \
        -p "0:8123" \
        "$image" >/dev/null
    # Wait for HTTP.
    for _ in $(seq 1 60); do
        if docker exec "$cname" wget -q -O- 'http://127.0.0.1:8123/ping' 2>/dev/null | grep -q Ok; then
            break
        fi
        # Some images lack wget; try clickhouse-client.
        if docker exec "$cname" clickhouse-client -q 'SELECT 1' >/dev/null 2>&1; then
            break
        fi
        sleep 1
    done
    ip="$(docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$cname")"
    out="$results_dir/ch${i}.json"
    docker exec \
        -e PROBE_CH_URL="http://${ip}:8123" \
        -e PROBE_CH_IMAGE="$image" \
        -e PROBE_ROWS="${PROBE_ROWS:-300000}" \
        -e PROBE_BATCHES="${PROBE_BATCHES:-1,10,100,1000,10000,100000}" \
        -e PROBE_WRITERS="${PROBE_WRITERS:-8}" \
        -e PROBE_STEP_TIMEOUT="${PROBE_STEP_TIMEOUT:-120}" \
        "${NAME}-driver" python /tmp/clickhouse_probe.py > "$out"
    i=$((i + 1))
done

# Require the two images' settings to differ (guard item 6).
python - "$results_dir" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
docs = [json.loads(p.read_text(encoding="utf-8").split("===JSON===", 1)[-1]) for p in sorted(root.glob("*.json"))]
if len(docs) < 2:
    print("REFUSING: need ≥2 images", file=sys.stderr)
    sys.exit(2)
a, b = docs[0]["settings"], docs[1]["settings"]
if a == b:
    print(f"REFUSING: both images report identical merge_tree settings {a}", file=sys.stderr)
    sys.exit(2)
print("===JSON===")
print(json.dumps({"images": docs, "settingsDiffer": True}, indent=1, default=str))
PY
