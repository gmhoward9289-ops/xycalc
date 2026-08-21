#!/usr/bin/env bash
# Issue #14 / T6 — sweep PROBE_PREFETCH while holding concurrency fixed.
#
# Prefetch is read at Celery app import time, so each value needs a worker
# recreate. Redis + Mongo stay up; drive.py's idempotent load skips reinsert
# after the first driver run.
#
#   cd tools/bench/celery_probe
#   docker compose up -d --build redis bookkeeping mongo
#   PROBE_RATES=400 PROBE_SECONDS=30 ./sweep_prefetch.sh
#
# Smoke:
#   PROBE_PREFETCHES=1,4 PROBE_RATES=50 PROBE_SECONDS=8 PROBE_DOCS=800000 \
#     ./sweep_prefetch.sh
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -b "${PROBE_DEV:-/dev/sda}" ]; then
    echo "compose.yml throttles ${PROBE_DEV:-/dev/sda}, which is not a block device here." >&2
    echo "Edit blkio_config in compose.yml for this host, or run on reef/swamplink." >&2
    exit 1
fi

OUT="${OUT:-./prefetch-sweep-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUT"
PREFETCHES="${PROBE_PREFETCHES:-1,2,4,8,16}"
RATES="${PROBE_RATES:-400}"
SECONDS_PER="${PROBE_SECONDS:-30}"

echo "=== prefetch sweep start $(date -Is) out=$OUT ===" >&2
docker compose up -d --build redis bookkeeping mongo >&2

IFS=',' read -r -a prefs <<< "$PREFETCHES"
combined="$OUT/combined.jsonl"
: > "$combined"

for p in "${prefs[@]}"; p="${p// /}"; do
    echo "--- prefetch=$p $(date -Is) ---" >&2
    PROBE_PREFETCH="$p" \
      docker compose up -d --build --force-recreate worker >&2
    # Give the worker a moment to register before the driver floods Redis.
    sleep 3
    log="$OUT/prefetch-${p}.log"
    set +e
    PROBE_PREFETCH="$p" PROBE_RATES="$RATES" PROBE_SECONDS="$SECONDS_PER" \
      docker compose run --rm --no-deps -T \
        -e PROBE_PREFETCH="$p" \
        -e PROBE_RATES="$RATES" \
        -e PROBE_SECONDS="$SECONDS_PER" \
        driver python drive.py > "$log" 2>"$OUT/prefetch-${p}.err"
    ec=$?
    set -e
    echo "--- prefetch=$p exit $ec ---" >&2
    if [ "$ec" -ne 0 ]; then
        echo "FAILED prefetch=$p (exit $ec); see $log / $OUT/prefetch-${p}.err" >&2
        exit "$ec"
    fi
    if ! grep -q '===JSON===' "$log"; then
        echo "MISSING JSON prefetch=$p" >&2
        exit 1
    fi
    # One JSON object per prefetch value for easy import.
    python - "$log" "$p" "$combined" <<'PY'
import json, sys
path, prefetch, out = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(path, encoding="utf-8", errors="replace").read()
blob = text.split("===JSON===", 1)[1].strip()
doc = json.loads(blob)
doc["prefetchSwept"] = int(prefetch)
with open(out, "a", encoding="utf-8") as f:
    f.write(json.dumps(doc, default=str) + "\n")
# Print a one-line summary for humans.
for r in doc.get("results", []):
    print(
        f"prefetch={prefetch} rate={r.get('targetRatePerSecond')} "
        f"achieved={r.get('achievedRate')} underMax={r.get('understatementMax')} "
        f"underMean={r.get('understatementMean')} drain={r.get('drainSeconds')}",
        file=sys.stderr,
    )
PY
done

echo "=== prefetch sweep complete $(date -Is) ===" >&2
echo "Combined: $combined" >&2
echo "Import: retain sampleSeries; land observations per docs/plans/issue-14-celery-prefetch-backlog.md" >&2
