#!/usr/bin/env bash
# Issue #16 / T8 — retry-policy stall/recover sweep over PROBE_RETRY_POLICY.
#
#   cd tools/bench/celery_probe
#   ./run_stall_recover.sh
#
# Policies: none (no retry), immediate (countdown=0 = plan's "no backoff"),
# exponential, jitter. Visibility timeout is raised so broker redelivery
# does not confound retries.
#
# Smoke:
#   PROBE_BASELINE_SECONDS=8 PROBE_STALL_SECONDS=12 PROBE_RECOVERY_TIMEOUT=30 \
#   PROBE_RATES=50 PROBE_DOCS=800000 PROBE_POLICIES=none,immediate \
#   PROBE_STALL_MODE=pause ./run_stall_recover.sh
set -euo pipefail
cd "$(dirname "$0")"

if [ -z "${PROBE_DEV:-}" ] && [ ! -b /dev/sda ]; then
    echo "compose.yml throttles PROBE_DEV (default /dev/sda), which is not a block device here." >&2
    exit 1
fi
if [ -n "${PROBE_DEV:-}" ] && [ ! -b "${PROBE_DEV}" ]; then
    echo "note: PROBE_DEV=$PROBE_DEV is not a host block device; trusting Docker engine." >&2
fi

OUT="${OUT:-./stall-recover-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUT"
POLICIES="${PROBE_POLICIES:-none,immediate,exponential,jitter}"
export PROBE_VISIBILITY_TIMEOUT="${PROBE_VISIBILITY_TIMEOUT:-600}"
export PROBE_STALL_MODE="${PROBE_STALL_MODE:-cgroup}"
export PROBE_DOCS="${PROBE_DOCS:-800000}"

echo "=== stall/recover sweep start $(date -Is) out=$OUT docs=$PROBE_DOCS ===" >&2
docker compose up -d --build redis bookkeeping mongo >&2

# Resolve mongo container name for the driver.
MONGO_NAME="$(docker compose ps -q mongo | xargs -r docker inspect -f '{{.Name}}' | sed 's#^/##')"
export PROBE_MONGO_CONTAINER="${PROBE_MONGO_CONTAINER:-$MONGO_NAME}"

IFS=',' read -r -a pols <<< "$POLICIES"
combined="$OUT/combined.jsonl"
: > "$combined"

for pol in "${pols[@]}"; do
    pol="${pol// /}"
    echo "--- policy=$pol $(date -Is) ---" >&2
    PROBE_RETRY_POLICY="$pol" \
      docker compose up -d --build --force-recreate worker >&2
    sleep 3
    log="$OUT/policy-${pol}.log"
    set +e
    PROBE_RETRY_POLICY="$pol" \
    PROBE_MONGO_CONTAINER="$PROBE_MONGO_CONTAINER" \
    PROBE_STALL_MODE="$PROBE_STALL_MODE" \
    PROBE_VISIBILITY_TIMEOUT="$PROBE_VISIBILITY_TIMEOUT" \
    PROBE_RATES="${PROBE_RATES:-300}" \
    PROBE_BASELINE_SECONDS="${PROBE_BASELINE_SECONDS:-60}" \
    PROBE_STALL_SECONDS="${PROBE_STALL_SECONDS:-90}" \
    PROBE_RECOVERY_TIMEOUT="${PROBE_RECOVERY_TIMEOUT:-180}" \
      docker compose --profile stall run --rm --no-deps -T stall-driver \
        > "$log" 2>"$OUT/policy-${pol}.err"
    ec=$?
    set -e
    echo "--- policy=$pol exit $ec ---" >&2
    if ! grep -q '===JSON===' "$log"; then
        echo "MISSING JSON policy=$pol (exit $ec)" >&2
        cat "$OUT/policy-${pol}.err" >&2 || true
        exit 1
    fi
    # Git Bash on Windows often has no usable host python for /v paths — use
    # a one-shot container with the OUT dir mounted (same pattern as sweep_prefetch).
    out_dir="$(cd "$(dirname "$combined")" && pwd)"
    if out_win="$(cd "$out_dir" && pwd -W 2>/dev/null)"; then
      mount_src="$out_win"
    else
      mount_src="$out_dir"
    fi
    docker run --rm -i -v "${mount_src}:/out" python:3.12-slim \
      python - "/out/$(basename "$log")" "$pol" "/out/$(basename "$combined")" <<'PY'
import json, sys
path, policy, out = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(path, encoding="utf-8", errors="replace").read()
blob = text.split("===JSON===", 1)[1].strip()
end = blob.find("\n=====")
if end != -1:
    blob = blob[:end]
doc = json.loads(blob)
doc["policySwept"] = policy
with open(out, "a", encoding="utf-8") as f:
    f.write(json.dumps(doc, default=str) + "\n")
g = doc.get("guards", {})
stall = doc.get("stall", {})
print(
    f"policy={policy} guards_ok={g.get('ok')} "
    f"stall_retries={stall.get('retriesDelta')} "
    f"amplification={stall.get('amplification')} "
    f"recovered={doc.get('recovery', {}).get('recovered')}",
    file=sys.stderr,
)
PY
done

echo "=== stall/recover sweep complete $(date -Is) ===" >&2
echo "Combined: $combined" >&2
