#!/usr/bin/env bash
# Pull CloudWatch for Arm 2 instance+volume ExceededChecks after teardown.
#
#   EBS_INSTCEIL_INSTANCE=i-... EBS_INSTCEIL_VOLUME=vol-... \
#   EBS_INSTCEIL_START=... EBS_INSTCEIL_END=... \
#   bash tools/bench/_aws_ebs_instceil_pull_cw.sh
set -euo pipefail

REGION="${AWS_REGION:-us-east-2}"
STAGE="${EBS_INSTCEIL_STAGE:-/c/Users/gmhow/dev/xycalc/tmp/xycalc-ebs-instceil-20260821}"
INSTANCE="${EBS_INSTCEIL_INSTANCE:-}"
VOLUME="${EBS_INSTCEIL_VOLUME:-}"
START="${EBS_INSTCEIL_START:-}"
END="${EBS_INSTCEIL_END:-}"

if [ -z "$INSTANCE" ] && [ -f "$STAGE/instance.id" ]; then
  INSTANCE=$(tr -d '\r\n' < "$STAGE/instance.id")
fi
if [ -z "$VOLUME" ] && [ -f "$STAGE/data_volume.id" ]; then
  VOLUME=$(tr -d '\r\n' < "$STAGE/data_volume.id")
fi
if [ -z "$START" ] && [ -f "$STAGE/results/window_start.txt" ]; then
  START=$(tr -d '\r\n' < "$STAGE/results/window_start.txt")
fi
if [ -z "$END" ] && [ -f "$STAGE/results/window_end.txt" ]; then
  END=$(tr -d '\r\n' < "$STAGE/results/window_end.txt")
fi
# pad window by 2 minutes either side for CW aggregation
if [ -n "$START" ] && [ -n "$END" ]; then
  START=$(python -c "from datetime import datetime,timedelta; s='$START'.replace('Z','+00:00'); d=datetime.fromisoformat(s)-timedelta(minutes=2); print(d.strftime('%Y-%m-%dT%H:%M:%SZ'))")
  END=$(python -c "from datetime import datetime,timedelta; s='$END'.replace('Z','+00:00'); d=datetime.fromisoformat(s)+timedelta(minutes=5); print(d.strftime('%Y-%m-%dT%H:%M:%SZ'))")
fi

OUT_DIR="${OUT_DIR:-$STAGE/cw}"
mkdir -p "$OUT_DIR"

echo "region=$REGION instance=$INSTANCE volume=$VOLUME window=$START..$END"
echo "out=$OUT_DIR"

for METRIC in InstanceEBSIOPSExceededCheck InstanceEBSThroughputExceededCheck EBSReadOps EBSWriteOps; do
  aws cloudwatch get-metric-statistics \
    --region "$REGION" \
    --namespace AWS/EC2 \
    --metric-name "$METRIC" \
    --dimensions "Name=InstanceId,Value=$INSTANCE" \
    --start-time "$START" \
    --end-time "$END" \
    --period 60 \
    --statistics Maximum Average Sum SampleCount \
    --output json > "$OUT_DIR/${METRIC}.json" || true
done

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
    --output json > "$OUT_DIR/${METRIC}.json" || true
done

python - "$OUT_DIR" <<'PY'
import json, sys
from pathlib import Path
out = Path(sys.argv[1])
for p in sorted(out.glob('*.json')):
    d = json.loads(p.read_text(encoding='utf-8'))
    dps = d.get('Datapoints') or []
    print(f'{p.name}: n={len(dps)}')
    for dp in sorted(dps, key=lambda x: str(x.get('Timestamp', ''))):
        print(f"  {dp.get('Timestamp')} max={dp.get('Maximum')} avg={dp.get('Average')} sum={dp.get('Sum')}")
PY
