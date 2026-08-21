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

# Docker Desktop / Git Bash hosts often lack a real /dev node; trust PROBE_DEV.
if [ -z "${PROBE_DEV:-}" ] && [ ! -b /dev/sda ]; then
    echo "compose.yml throttles PROBE_DEV (default /dev/sda), which is not a block device here." >&2
    echo "Set PROBE_DEV=/dev/xxx, or run on reef/swamplink." >&2
    exit 1
fi
if [ -n "${PROBE_DEV:-}" ] && [ ! -b "${PROBE_DEV}" ]; then
    echo "note: PROBE_DEV=$PROBE_DEV is not a host block device; trusting Docker engine." >&2
fi

OUT="${OUT:-./prefetch-sweep-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUT"
PREFETCHES="${PROBE_PREFETCHES:-1,2,4,8,16}"
RATES="${PROBE_RATES:-400}"
SECONDS_PER="${PROBE_SECONDS:-30}"
export PROBE_DOCS="${PROBE_DOCS:-800000}"

echo "=== prefetch sweep start $(date -Is) out=$OUT docs=$PROBE_DOCS ===" >&2
docker compose up -d --build redis bookkeeping mongo >&2

IFS=',' read -r -a prefs <<< "$PREFETCHES"
combined="$OUT/combined.jsonl"
: > "$combined"

for p in "${prefs[@]}"; do
    p="${p// /}"
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
    # Git Bash on Windows often has no usable host python for /v paths — use
    # a one-shot container with the OUT dir mounted.
    out_dir="$(cd "$(dirname "$combined")" && pwd)"
    # Docker Desktop needs a Windows-style bind for host mounts from Git Bash.
    if out_win="$(cd "$out_dir" && pwd -W 2>/dev/null)"; then
      mount_src="$out_win"
    else
      mount_src="$out_dir"
    fi
    docker run --rm -i -v "${mount_src}:/out" python:3.12-slim \
      python - "/out/$(basename "$log")" "$p" "/out/$(basename "$combined")" <<'PY'
import json, sys
path, prefetch, out = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(path, encoding="utf-8", errors="replace").read()
blob = text.split("===JSON===", 1)[1].strip()
# Trim trailing markers if present.
end = blob.find("\n=====")
if end != -1:
    blob = blob[:end]
doc = json.loads(blob)
doc["prefetchSwept"] = int(prefetch)
with open(out, "a", encoding="utf-8") as f:
    f.write(json.dumps(doc, default=str) + "\n")
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
