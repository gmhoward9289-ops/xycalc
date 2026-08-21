#!/usr/bin/env bash
# Re-pull CloudWatch for Arm 1 volume after teardown (ExceededChecks may lag).
# Volume deleted; classic VolumeReadOps already seen - re-check ExceededChecks.
#
#   bash tools/bench/_aws_ebs_xcheck_pull_cw.sh
set -euo pipefail

REGION="${AWS_REGION:-us-east-2}"
VOLUME="${EBS_XCHECK_VOLUME:-vol-0859940aeff344ecc}"
START="${EBS_XCHECK_START:-2026-08-21T17:09:00Z}"
END="${EBS_XCHECK_END:-2026-08-21T17:19:00Z}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT_DIR="${OUT_DIR:-$ROOT/tmp/xycalc-ebs-xcheck-20260821/cw}"
mkdir -p "$OUT_DIR"

echo "region=$REGION volume=$VOLUME window=$START..$END"
echo "out=$OUT_DIR"

for METRIC in VolumeIOPSExceededCheck VolumeThroughputExceededCheck VolumeAvgIOPS VolumeReadOps VolumeWriteOps VolumeQueueLength; do
  aws cloudwatch get-metric-statistics \
    --region "$REGION" \
    --namespace AWS/EBS \
    --metric-name "$METRIC" \
    --dimensions "Name=VolumeId,Value=$VOLUME" \
    --start-time "$START" \
    --end-time "$END" \
    --period 60 \
    --statistics Maximum Average Sum SampleCount \
    --output json > "$OUT_DIR/${METRIC}.json"
done

python - "$OUT_DIR" <<'PY'
import json, sys
from pathlib import Path
out = Path(sys.argv[1])
for p in sorted(out.glob('Volume*.json')):
    d = json.loads(p.read_text(encoding='utf-8'))
    dps = d.get('Datapoints') or []
    print(f'{p.name}: n={len(dps)}')
    for dp in sorted(dps, key=lambda x: str(x.get('Timestamp', ''))):
        print(f"  {dp.get('Timestamp')} max={dp.get('Maximum')} avg={dp.get('Average')}")
PY

echo
echo "If VolumeIOPSExceededCheck n>0: update artifacts/aws-ebs-xcheck-20260821.md and observation YAML."
