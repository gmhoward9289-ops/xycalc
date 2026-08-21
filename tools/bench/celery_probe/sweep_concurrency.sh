#!/usr/bin/env bash
# Investigation 012 — concurrency ladder at fixed offered rate (validation).
#
# Holds prefetch/acks/timeout fixed; sweeps PROBE_CONCURRENCY. Each value
# force-recreates the worker (concurrency is a celery CLI flag).
#
#   cd tools/bench/celery_probe
#   PROBE_RATES=200 PROBE_SECONDS=30 ./sweep_concurrency.sh
#
# Smoke:
#   PROBE_CONCURRENCIES=4,8 PROBE_RATES=50 PROBE_SECONDS=8 PROBE_DOCS=800000 \
#     ./sweep_concurrency.sh
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -b "${PROBE_DEV:-/dev/sda}" ]; then
    echo "compose.yml throttles ${PROBE_DEV:-/dev/sda}, which is not a block device here." >&2
    echo "Edit blkio_config in compose.yml for this host, or run on reef/swamplink." >&2
    exit 1
fi

OUT="${OUT:-./concurrency-sweep-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUT"
CONCS="${PROBE_CONCURRENCIES:-1,2,4,8,16}"
RATES="${PROBE_RATES:-200}"
SECONDS_PER="${PROBE_SECONDS:-30}"

echo "=== concurrency sweep start $(date -Is) out=$OUT ===" >&2
docker compose up -d --build redis bookkeeping mongo >&2

IFS=',' read -r -a levels <<< "$CONCS"
combined="$OUT/combined.jsonl"
: > "$combined"

for c in "${levels[@]}"; do
    c="${c// /}"
    echo "--- concurrency=$c $(date -Is) ---" >&2
    PROBE_CONCURRENCY="$c" \
      docker compose up -d --build --force-recreate worker >&2
    sleep 3
    log="$OUT/concurrency-${c}.log"
    set +e
    PROBE_CONCURRENCY="$c" PROBE_RATES="$RATES" PROBE_SECONDS="$SECONDS_PER" \
      docker compose run --rm --no-deps -T \
        -e PROBE_CONCURRENCY="$c" \
        -e PROBE_RATES="$RATES" \
        -e PROBE_SECONDS="$SECONDS_PER" \
        driver python drive.py > "$log" 2>"$OUT/concurrency-${c}.err"
    ec=$?
    set -e
    echo "--- concurrency=$c exit $ec ---" >&2
    if [ "$ec" -ne 0 ]; then
        echo "FAILED concurrency=$c (exit $ec); see $log / $OUT/concurrency-${c}.err" >&2
        exit "$ec"
    fi
    if ! grep -q '===JSON===' "$log"; then
        echo "MISSING JSON concurrency=$c" >&2
        exit 1
    fi
    python3 - "$log" "$c" "$combined" <<'PY'
import json, sys
path, conc, out = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(path, encoding="utf-8", errors="replace").read()
blob = text.split("===JSON===", 1)[1].strip()
doc = json.loads(blob)
doc["concurrencySwept"] = int(conc)
with open(out, "a", encoding="utf-8") as f:
    f.write(json.dumps(doc, default=str) + "\n")
for r in doc.get("results", []):
    print(
        f"concurrency={conc} rate={r.get('targetRatePerSecond')} "
        f"done={r.get('completedPerSecond')} depth={r.get('queueDepthMax')} "
        f"drain={r.get('drainSeconds')}",
        file=sys.stderr,
    )
PY
done

echo "=== concurrency sweep complete $(date -Is) ===" >&2
echo "Combined: $combined" >&2
