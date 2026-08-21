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

# Git Bash rewrites Docker argv paths; disable for this script.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

NAME="${PROBE_NAME:-xycalc-ch-probe-$$-$(date +%s)}"
PY_IMAGE="${PROBE_PY_IMAGE:-python:3.12-slim}"
IMAGES="${PROBE_IMAGES:-clickhouse/clickhouse-server:23.3,clickhouse/clickhouse-server:24.8}"
CPUS="${PROBE_CPUS:-2}"
MEMORY="${PROBE_MEMORY:-2g}"

here="$(cd "$(dirname "$0")" && pwd)"

# Docker Desktop credential helper often fails in headless SSH sessions.
# Prefer a pre-loaded image; otherwise try an empty DOCKER_CONFIG pull.
pull_image() {
    local image="$1"
    if docker image inspect "$image" >/dev/null 2>&1; then
        return 0
    fi
    echo "pulling $image (anon config; no credential helper)..." >&2
    local cfg
    cfg="$(mktemp -d)"
    printf '%s\n' '{"auths":{}}' >"$cfg/config.json"
    if ! DOCKER_CONFIG="$cfg" docker pull "$image"; then
        rm -rf "$cfg"
        echo "FAILED to pull $image — pre-load via docker load" >&2
        return 1
    fi
    rm -rf "$cfg"
}

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
if here_win="$(cd "$(dirname "$0")" && pwd -W 2>/dev/null)"; then
    docker cp "${here_win%/}\clickhouse_probe.py" "${NAME}-driver:/tmp/clickhouse_probe.py"
else
    docker cp "$here/clickhouse_probe.py" "${NAME}-driver:/tmp/clickhouse_probe.py"
fi

IFS=',' read -r -a imgs <<< "$IMAGES"
results_dir="$(mktemp -d)"
i=0
for image in "${imgs[@]}"; do
    image="${image// /}"
    cname="${NAME}-ch${i}"
    echo "=== image $image as $cname ===" >&2
    pull_image "$image"
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
    set +e
    docker exec \
        -e PROBE_CH_URL="http://${ip}:8123" \
        -e PROBE_CH_IMAGE="$image" \
        -e PROBE_ROWS="${PROBE_ROWS:-300000}" \
        -e PROBE_BATCHES="${PROBE_BATCHES:-1,10,100,1000,10000,100000}" \
        -e PROBE_WRITERS="${PROBE_WRITERS:-8}" \
        -e PROBE_STEP_TIMEOUT="${PROBE_STEP_TIMEOUT:-120}" \
        "${NAME}-driver" python /tmp/clickhouse_probe.py > "$out"
    ec=$?
    set -e
    # Exit 2 = delay threshold not crossed (honest non-onset). Keep JSON.
    if [ "$ec" -ne 0 ] && [ "$ec" -ne 2 ]; then
        echo "clickhouse_probe failed for $image exit=$ec" >&2
        exit "$ec"
    fi
    if [ "$ec" -eq 2 ]; then
        echo "note: $image guards.ok=false (delay onset not reached); retaining JSON" >&2
    fi
    i=$((i + 1))
done

# Require the two images' settings to differ (guard item 6).
# Still publish when either image failed to cross delay threshold — that is a
# valid negative result for the insert-frequency question under these knobs.
# Git Bash hosts often lack a usable `python` — aggregate via a one-shot container.
if results_win="$(cd "$results_dir" && pwd -W 2>/dev/null)"; then
    results_mount="$results_win"
else
    results_mount="$results_dir"
fi
docker run --rm -i -v "${results_mount}:/out" python:3.12-slim python - <<'PY'
import json, pathlib, sys
root = pathlib.Path("/out")
docs = []
for p in sorted(root.glob("*.json")):
    text = p.read_text(encoding="utf-8")
    if "===JSON===" not in text:
        print(f"REFUSING: {p.name} has no JSON marker", file=sys.stderr)
        sys.exit(2)
    docs.append(json.loads(text.split("===JSON===", 1)[-1]))
if len(docs) < 2:
    print("REFUSING: need ≥2 images", file=sys.stderr)
    sys.exit(2)
a, b = docs[0]["settings"], docs[1]["settings"]
if a.get("parts_to_delay_insert") == b.get("parts_to_delay_insert") and a.get(
    "parts_to_throw_insert"
) == b.get("parts_to_throw_insert"):
    print(f"REFUSING: both images report identical merge_tree settings {a}", file=sys.stderr)
    sys.exit(2)
any_onset = any(
    any(r.get("crossedDelayThreshold") for r in d.get("results") or []) for d in docs
)
print("===JSON===")
print(
    json.dumps(
        {
            "images": docs,
            "settingsDiffer": True,
            "delayOnsetObserved": any_onset,
        },
        indent=1,
        default=str,
    )
)
sys.exit(0)
PY
