#!/usr/bin/env bash
# Issue #2 Phase 1 — isolated concurrency levels with plateau detection.
# Fresh mongod per level (ticket_probe.sh unique container names).
#
#   ./tools/bench/run_issue2_phase1.sh
#   PROBE_SECONDS=60 ./tools/bench/run_issue2_phase1.sh   # shorter smoke
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
OUT="${OUT:-/tmp/xycalc-issue2-phase1-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUT"
SECONDS_PER="${PROBE_SECONDS:-300}"
DOCS="${PROBE_DOCS:-1500000}"

echo "=== issue #2 phase 1 out=$OUT seconds=$SECONDS_PER ===" >&2
for level in 16 32 64; do
  echo "--- level=$level $(date -Is) ---" >&2
  log="$OUT/level-${level}.log"
  set +e
  PROBE_LEVELS="$level" PROBE_SECONDS="$SECONDS_PER" PROBE_DOCS="$DOCS" \
    ./tools/bench/ticket_probe.sh > "$log" 2>"$OUT/level-${level}.err"
  ec=$?
  set -e
  echo "--- level=$level exit $ec ---" >&2
  if [ "$ec" -ne 0 ]; then
    echo "FAILED level=$level; see $log / $OUT/level-${level}.err" >&2
    exit "$ec"
  fi
  # Extract convergence one-liner for the human watching.
  python3 - "$log" <<'PY' || true
import json, sys
text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
if "===JSON===" not in text:
    print("no JSON", file=sys.stderr); raise SystemExit(0)
doc = json.loads(text.split("===JSON===", 1)[1].strip())
for r in doc.get("results", doc.get("levels", [doc] if "convergence" in doc else [])):
    if not isinstance(r, dict):
        continue
    c = r.get("convergence") or {}
    print(
        f"c={r.get('concurrency')} ops/s={r.get('opsPerSecond')} "
        f"ticketsMax={r.get('ticketsMax')} holdMs={r.get('holdTimeMs')} "
        f"predHold={r.get('predictedCeilingHold')} "
        f"verdict={c.get('verdict')} relDelta={c.get('relDelta')}",
        file=sys.stderr,
    )
PY
done
echo "=== issue #2 phase 1 complete $(date -Is) ===" >&2
echo "OUT=$OUT" >&2
