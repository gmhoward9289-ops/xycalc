#!/usr/bin/env bash
# Run cache-cliff legs on swamplink; logs + JSON under results/cache-cliff/.
set -euo pipefail
cd /root/dev/xycalc
OUTDIR=results/cache-cliff
mkdir -p "$OUTDIR"
TAG="${1:?tag e.g. a1-r2}"
shift
export PROBE_DEV=/dev/sda
# Defaults can be overridden by env before invoke.
LOG="$OUTDIR/${TAG}.log"
JSON="$OUTDIR/${TAG}.json"
echo "starting $TAG at $(date -Is) -> $JSON" | tee -a "$OUTDIR/runner.log"
set +e
./tools/bench/cache_cliff_probe.sh "$@" >"$JSON" 2>"$LOG"
rc=$?
set -e
echo "finished $TAG rc=$rc at $(date -Is)" | tee -a "$OUTDIR/runner.log"
# Summarize ops/s if JSON present
python3 - "$JSON" <<'PY' || true
import json,sys
p=sys.argv[1]
try:
    raw=open(p).read()
    i=raw.find("{")
    d=json.loads(raw[i:])
except Exception as e:
    print("no parseable json:", e); raise SystemExit
print(f"failedDeviceGuards={d.get('failedDeviceGuards')} legs={len(d.get('legs',[]))}")
print(f"{'ratio':>8} {'ops/s':>10} {'pages/op':>10} {'guard':>6}")
for leg in d.get("legs",[]):
    r=leg["result"]
    print(f"{leg['targetRatio']:8} {r['opsPerSecond']:10.1f} {r['pagesReadIntoCachePerOp']:10.4f} {str(leg.get('deviceByteGuardOk')):>6}")
PY
exit $rc
