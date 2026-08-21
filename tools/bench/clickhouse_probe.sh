#!/usr/bin/env bash
# Disposable ClickHouse insert/part-pressure probe for investigation 012 (T10).
#
#   ./tools/bench/clickhouse_probe.sh                 # full dual-image sweep
#   PROBE_SMOKE=1 ./tools/bench/clickhouse_probe.sh   # one image, short budget
#
# Merges-on on deliberately slow storage (Claim A without permanent STOP MERGES):
#   PROBE_STOP_MERGES=0 PROBE_MERGE_DUTY_CYCLE=0.05 PROBE_BACKGROUND_POOL_SIZE=2 \
#   PROBE_DATA_DIR=/mnt/ch-probe-data/run PROBE_SMOKE=1 PROBE_SMOKE_SIDE=pre23_6 \
#   PROBE_ROWS=5000 PROBE_BATCHES=1,10,100 PROBE_WRITERS=16 \
#     ./tools/bench/clickhouse_probe.sh
# Optional block-IO (pair DATA_DIR with the throttled device):
#   PROBE_DEV=/dev/vdb PROBE_WRITE_BPS=1048576 PROBE_WRITE_IOPS=40 ...
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
READERS="${PROBE_READERS:-4}"
ROWS="${PROBE_ROWS:-300000}"
STEP_CAP="${PROBE_STEP_CAP_S:-120}"
BATCHES="${PROBE_BATCHES:-1,10,100,1000,10000,100000}"
HTTP_PORT="${PROBE_HTTP_PORT:-18123}"
CH_PASSWORD="${PROBE_PASSWORD:-xycalc}"
# Default ON for dual-image threshold confirmation — see clickhouse_probe.py.
# Set PROBE_STOP_MERGES=0 to test whether merges keep up on this box (Claim A).
STOP_MERGES="${PROBE_STOP_MERGES:-1}"

# Optional block-IO cgroup throttle (same pattern as ticket_probe.sh). Scoped
# to the ClickHouse container only. Pair with PROBE_DATA_DIR on that device so
# MergeTree writes actually hit the throttled path (vfs/overlay root I/O may
# not). Keep PROBE_MEMORY tight so the host page cache cannot hide the limit.
DATA_DIR="${PROBE_DATA_DIR:-}"
WRITE_BPS="${PROBE_WRITE_BPS:-}"
READ_BPS="${PROBE_READ_BPS:-}"
WRITE_IOPS="${PROBE_WRITE_IOPS:-}"
READ_IOPS="${PROBE_READ_IOPS:-}"
THROTTLE_DEV="${PROBE_DEV:-}"
# Cap ClickHouse background merge pool (server config.d). Empty = image default.
BG_POOL="${PROBE_BACKGROUND_POOL_SIZE:-}"
FSYNC_INSERTS="${PROBE_FSYNC_INSERTS:-0}"
MERGE_DUTY="${PROBE_MERGE_DUTY_CYCLE:-}"
MERGE_DUTY_PERIOD="${PROBE_MERGE_DUTY_PERIOD_S:-}"

# Dual-image by default: one pre-23.6, one 23.6+. Smoke uses the post side only.
PRE_IMAGE="${PROBE_PRE_IMAGE:-clickhouse/clickhouse-server:23.3}"
POST_IMAGE="${PROBE_POST_IMAGE:-clickhouse/clickhouse-server:24.8}"
PY_IMAGE="${PROBE_PY_IMAGE:-python:3.12-slim}"

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
RESULTS_DIR="$(mktemp -d /tmp/ch-probe-XXXXXX)"
CFG_DIR=""

if [ -n "$WRITE_BPS$READ_BPS$WRITE_IOPS$READ_IOPS" ] && [ -z "$THROTTLE_DEV" ]; then
    if [ -n "$DATA_DIR" ] && src="$(findmnt -n -o SOURCE --target "$DATA_DIR" 2>/dev/null)"; then
        parent="$(lsblk -no PKNAME "$src" 2>/dev/null | head -1 || true)"
        THROTTLE_DEV="$([ -n "$parent" ] && echo "/dev/$parent" || echo "$src")"
    else
        root_src="$(df --output=source / | tail -1)"
        parent="$(lsblk -no PKNAME "$root_src" 2>/dev/null | head -1 || true)"
        THROTTLE_DEV="$([ -n "$parent" ] && echo "/dev/$parent" || echo "$root_src")"
    fi
fi
if [ -n "$WRITE_BPS$READ_BPS$WRITE_IOPS$READ_IOPS" ]; then
    if [ -z "${PROBE_DEV:-}" ] && [ ! -b "$THROTTLE_DEV" ]; then
        echo "no block device to throttle (tried '$THROTTLE_DEV')." >&2
        echo "Set PROBE_DEV=/dev/xxx explicitly (and prefer PROBE_DATA_DIR on it)." >&2
        exit 1
    fi
    if [ -n "${PROBE_DEV:-}" ] && [ ! -b "$THROTTLE_DEV" ]; then
        echo "note: PROBE_DEV=$THROTTLE_DEV is not a host block device; trusting Docker." >&2
    fi
fi

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
    if [ -n "${CFG_DIR:-}" ]; then
        rm -rf "$CFG_DIR"
        CFG_DIR=""
    fi
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
        echo "workload    ${ROWS} rows, batches=${BATCHES}, writers=${WRITERS}, readers=${READERS}, step_cap=${STEP_CAP}s"
        echo "merges      STOP_MERGES=${STOP_MERGES} (1=isolate part-count ceilings)"
        if [ -n "$DATA_DIR" ]; then
            echo "data        $DATA_DIR -> /var/lib/clickhouse"
        fi
        if [ -n "$WRITE_BPS$READ_BPS$WRITE_IOPS$READ_IOPS" ]; then
            echo "throttle    $THROTTLE_DEV  write_bps=${WRITE_BPS:--} read_bps=${READ_BPS:--} write_iops=${WRITE_IOPS:--} read_iops=${READ_IOPS:--}"
        fi
        if [ -n "$BG_POOL" ]; then
            echo "merges      background_pool_size=${BG_POOL} (config.d)"
        fi
        if [ "$FSYNC_INSERTS" = "1" ]; then
            echo "fsync       every insert (fsync_after_insert=1)"
        fi
        if [ -n "$MERGE_DUTY" ]; then
            echo "merge_duty  cycle=${MERGE_DUTY} period_s=${MERGE_DUTY_PERIOD:-2}"
        fi
        if [ -n "$LOCAL_PY" ]; then
            echo "driver      local $LOCAL_PY (host→published :${HTTP_PORT})"
        else
            echo "driver      docker $PY_IMAGE on $NET"
        fi
    } >&2

    local -a run_args=(
        -d --name "$NAME" --network "$NET"
        --cpus="$CPUS" --memory "$MEMORY" --memory-swap "$MEMORY"
        --ulimit nofile=262144:262144
        -e CLICKHOUSE_PASSWORD="$CH_PASSWORD"
        -p "${HTTP_PORT}:8123"
    )
    if [ -n "$WRITE_BPS" ]; then
        run_args+=(--device-write-bps "${THROTTLE_DEV}:${WRITE_BPS}")
    fi
    if [ -n "$READ_BPS" ]; then
        run_args+=(--device-read-bps "${THROTTLE_DEV}:${READ_BPS}")
    fi
    if [ -n "$WRITE_IOPS" ]; then
        run_args+=(--device-write-iops "${THROTTLE_DEV}:${WRITE_IOPS}")
    fi
    if [ -n "$READ_IOPS" ]; then
        run_args+=(--device-read-iops "${THROTTLE_DEV}:${READ_IOPS}")
    fi
    if [ -n "$DATA_DIR" ]; then
        # Fresh per-run dir; ClickHouse image runs as uid 101.
        if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
            sudo -n rm -rf "$DATA_DIR"
            sudo -n mkdir -p "$DATA_DIR"
            sudo -n chown -R 101:101 "$DATA_DIR"
        else
            rm -rf "$DATA_DIR"
            mkdir -p "$DATA_DIR"
            chown -R 101:101 "$DATA_DIR" 2>/dev/null || true
        fi
        run_args+=(-v "${DATA_DIR}:/var/lib/clickhouse")
    fi
    local cfg_dir=""
    if [ -n "$BG_POOL" ]; then
        # Replace any previous run's config dir; cleanup() removes CFG_DIR.
        if [ -n "$CFG_DIR" ]; then
            rm -rf "$CFG_DIR"
        fi
        CFG_DIR="$(mktemp -d /tmp/ch-cfg-XXXXXX)"
        cfg_dir="$CFG_DIR"
        # ClickHouse runs as uid 101 and must readdir config.d.
        chmod 755 "$cfg_dir"
        cat >"$cfg_dir/xycalc-merges.xml" <<EOF
<clickhouse>
  <!-- Cap merge concurrency. Must keep merge_tree free-entry knobs <= pool*ratio
       or the server refuses to start (BAD_ARGUMENTS). -->
  <background_pool_size>${BG_POOL}</background_pool_size>
  <background_merges_mutations_concurrency_ratio>1</background_merges_mutations_concurrency_ratio>
  <merge_tree>
    <number_of_free_entries_in_pool_to_execute_mutation>0</number_of_free_entries_in_pool_to_execute_mutation>
    <number_of_free_entries_in_pool_to_lower_max_size_of_merge>0</number_of_free_entries_in_pool_to_lower_max_size_of_merge>
  </merge_tree>
</clickhouse>
EOF
        chmod 644 "$cfg_dir/xycalc-merges.xml"
        # Bind the file only — replacing the whole config.d dir hides image defaults.
        run_args+=(-v "${cfg_dir}/xycalc-merges.xml:/etc/clickhouse-server/config.d/xycalc-merges.xml:ro")
    fi

    docker run "${run_args[@]}" "$image" >/dev/null

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
    # Throttle / data-dir metadata for the JSON (empty strings omitted by Python).
    export PROBE_THROTTLE_DEV="${THROTTLE_DEV:-}"
    export PROBE_WRITE_BPS="${WRITE_BPS:-}"
    export PROBE_READ_BPS="${READ_BPS:-}"
    export PROBE_WRITE_IOPS="${WRITE_IOPS:-}"
    export PROBE_READ_IOPS="${READ_IOPS:-}"
    export PROBE_DATA_DIR="${DATA_DIR:-}"
    export PROBE_BACKGROUND_POOL_SIZE="${BG_POOL:-}"
    export PROBE_FSYNC_INSERTS="$FSYNC_INSERTS"
    export PROBE_MERGE_DUTY_CYCLE="${MERGE_DUTY:-1}"
    export PROBE_MERGE_DUTY_PERIOD_S="${MERGE_DUTY_PERIOD:-2}"

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
            PROBE_READERS="$READERS" \
            PROBE_ROWS="$ROWS" \
            PROBE_STEP_CAP_S="$STEP_CAP" \
            PROBE_BATCHES="$BATCHES" \
            PROBE_STOP_MERGES="$STOP_MERGES" \
            PROBE_THROTTLE_DEV="${THROTTLE_DEV:-}" \
            PROBE_WRITE_BPS="${WRITE_BPS:-}" \
            PROBE_READ_BPS="${READ_BPS:-}" \
            PROBE_WRITE_IOPS="${WRITE_IOPS:-}" \
            PROBE_READ_IOPS="${READ_IOPS:-}" \
            PROBE_DATA_DIR="${DATA_DIR:-}" \
            PROBE_BACKGROUND_POOL_SIZE="${BG_POOL:-}" \
            PROBE_FSYNC_INSERTS="$FSYNC_INSERTS" \
            PROBE_MERGE_DUTY_CYCLE="${MERGE_DUTY:-1}" \
            PROBE_MERGE_DUTY_PERIOD_S="${MERGE_DUTY_PERIOD:-2}" \
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
                    -e PROBE_READERS="$READERS" \
                    -e PROBE_ROWS="$ROWS" \
                    -e PROBE_STEP_CAP_S="$STEP_CAP" \
                    -e PROBE_BATCHES="$BATCHES" \
                    -e PROBE_STOP_MERGES="$STOP_MERGES" \
                    -e "PROBE_THROTTLE_DEV=${THROTTLE_DEV:-}" \
                    -e "PROBE_WRITE_BPS=${WRITE_BPS:-}" \
                    -e "PROBE_READ_BPS=${READ_BPS:-}" \
                    -e "PROBE_WRITE_IOPS=${WRITE_IOPS:-}" \
                    -e "PROBE_READ_IOPS=${READ_IOPS:-}" \
                    -e "PROBE_DATA_DIR=${DATA_DIR:-}" \
                    -e "PROBE_BACKGROUND_POOL_SIZE=${BG_POOL:-}" \
                    -e "PROBE_FSYNC_INSERTS=$FSYNC_INSERTS" \
                    -e "PROBE_MERGE_DUTY_CYCLE=${MERGE_DUTY:-1}" \
                    -e "PROBE_MERGE_DUTY_PERIOD_S=${MERGE_DUTY_PERIOD:-2}" \
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
                    -e PROBE_READERS="$READERS" \
                    -e PROBE_ROWS="$ROWS" \
                    -e PROBE_STEP_CAP_S="$STEP_CAP" \
                    -e PROBE_BATCHES="$BATCHES" \
                    -e PROBE_STOP_MERGES="$STOP_MERGES" \
                    -e "PROBE_THROTTLE_DEV=${THROTTLE_DEV:-}" \
                    -e "PROBE_WRITE_BPS=${WRITE_BPS:-}" \
                    -e "PROBE_READ_BPS=${READ_BPS:-}" \
                    -e "PROBE_WRITE_IOPS=${WRITE_IOPS:-}" \
                    -e "PROBE_READ_IOPS=${READ_IOPS:-}" \
                    -e "PROBE_DATA_DIR=${DATA_DIR:-}" \
                    -e "PROBE_BACKGROUND_POOL_SIZE=${BG_POOL:-}" \
                    -e "PROBE_FSYNC_INSERTS=$FSYNC_INSERTS" \
                    -e "PROBE_MERGE_DUTY_CYCLE=${MERGE_DUTY:-1}" \
                    -e "PROBE_MERGE_DUTY_PERIOD_S=${MERGE_DUTY_PERIOD:-2}" \
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
    # Default post-23.6; set PROBE_SMOKE_SIDE=pre23_6 to hit the lower delay
    # threshold first when validating merges-on + slow disk (guard 3).
    SMOKE_SIDE="${PROBE_SMOKE_SIDE:-post23_6}"
    if [ "$SMOKE_SIDE" = "pre23_6" ]; then
        run_one "$PRE_IMAGE" pre23_6 "$RESULTS_DIR/pre.json"
    else
        run_one "$POST_IMAGE" post23_6 "$RESULTS_DIR/post.json"
    fi
else
    # Distinct data dirs when a shared PROBE_DATA_DIR parent is given.
    if [ -n "$DATA_DIR" ]; then
        DATA_DIR_BASE="$DATA_DIR"
        DATA_DIR="${DATA_DIR_BASE}/pre"
    fi
    run_one "$PRE_IMAGE" pre23_6 "$RESULTS_DIR/pre.json"
    echo >&2
    # Different host port for the second container (first cleaned up, but keep explicit).
    HTTP_PORT="${PROBE_HTTP_PORT_POST:-$((HTTP_PORT + 1))}"
    if [ -n "${DATA_DIR_BASE:-}" ]; then
        DATA_DIR="${DATA_DIR_BASE}/post"
    fi
    run_one "$POST_IMAGE" post23_6 "$RESULTS_DIR/post.json"

    # Combine + assert settings differ (guard 6 across images).
    python3 - "$RESULTS_DIR" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
docs = []
for name in ("pre.json", "post.json"):
    path = root / name
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        print(f"REFUSING: empty result file {path}", file=sys.stderr)
        sys.exit(2)
    docs.append(json.loads(text))

pre_settings = docs[0]["settings"]
post_settings = docs[1]["settings"]
for key in ("parts_to_delay_insert", "parts_to_throw_insert"):
    if pre_settings.get(key) == post_settings.get(key):
        print(
            "REFUSING: both images report identical %s=%s"
            % (key, pre_settings.get(key)),
            file=sys.stderr,
        )
        sys.exit(2)

out = {
    "probe": "clickhouse_probe",
    "images": docs,
    "settingsDiffer": True,
}
print("===JSON===")
print(json.dumps(out, indent=2))
PY
fi
