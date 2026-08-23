#!/usr/bin/env bash
# Poll ebs-xcheck until DONE/FAIL/timeout. Pull guest results, wait for
# CloudWatch ExceededCheck (or give up), THEN terminate instance + SG + key.
set -eu
ROOT=/c/Users/gmhow/dev/xycalc
REGION="${AWS_REGION:-us-east-2}"
CW_WAIT_SEC="${EBS_XCHECK_CW_WAIT_SEC:-900}"

if [ -n "${T9C_STAGE:-}" ]; then
  STAGE="$T9C_STAGE"
elif [ -f "$ROOT/tmp/t9c-latest-stage.txt" ]; then
  STAGE=$(tr -d '\r\n' < "$ROOT/tmp/t9c-latest-stage.txt")
else
  echo "No xcheck stage — set T9C_STAGE or run launch first." >&2
  exit 1
fi

KEYNAME=$(tr -d '\r\n' < "$STAGE/keyname.txt")
KEY="$STAGE/${KEYNAME}.pem"
IP=$(tr -d '\r\n' < "$STAGE/ip.txt")
IID=$(tr -d '\r\n' < "$STAGE/instance.id")
VOL=$(tr -d '\r\n' < "$STAGE/data_volume.id")
MAX_HOURS=$(tr -d '\r\n' < "$STAGE/max_hours.txt" 2>/dev/null || echo 1)
PULL="$STAGE/results"
CW="$STAGE/cw"
SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=15 -o BatchMode=yes "ec2-user@${IP}")

teardown() {
  echo "Terminating $IID..."
  aws ec2 terminate-instances --region "$REGION" --instance-ids "$IID" --output json >/dev/null || true
  aws ec2 wait instance-terminated --region "$REGION" --instance-ids "$IID" 2>/dev/null || true
  KEYNAME=$(tr -d '\r\n' < "$STAGE/keyname.txt" 2>/dev/null || true)
  SG=$(tr -d '\r\n' < "$STAGE/sg.txt" 2>/dev/null || true)
  sleep 8
  aws ec2 delete-security-group --region "$REGION" --group-id "$SG" 2>/dev/null || true
  aws ec2 delete-key-pair --region "$REGION" --key-name "$KEYNAME" 2>/dev/null || true
  # Volumes are DeleteOnTermination=true; leftover volumes tagged xycalc=ebs-xcheck still get deleted.
  leftover=$(aws ec2 describe-volumes --region "$REGION" \
    --filters "Name=tag:xycalc,Values=ebs-xcheck" "Name=status,Values=available" \
    --query 'Volumes[].VolumeId' --output text 2>/dev/null || true)
  if [ -n "${leftover:-}" ] && [ "$leftover" != "None" ]; then
    for v in $leftover; do
      echo "Deleting leftover volume $v"
      aws ec2 delete-volume --region "$REGION" --volume-id "$v" || true
    done
  fi
  echo TEARDOWN=OK
}

pull_cw() {
  mkdir -p "$CW"
  START="$1"
  END="$2"
  echo "CW pull volume=$VOL window=$START..$END"
  aws cloudwatch list-metrics --region "$REGION" --namespace AWS/EBS \
    --dimensions "Name=VolumeId,Value=$VOL" --output json > "$CW/list-metrics.json" || true
  for METRIC in VolumeIOPSExceededCheck VolumeThroughputExceededCheck VolumeAvgIOPS \
                VolumeReadOps VolumeWriteOps VolumeQueueLength; do
    for PERIOD in 60 300; do
      aws cloudwatch get-metric-statistics \
        --region "$REGION" \
        --namespace AWS/EBS \
        --metric-name "$METRIC" \
        --dimensions "Name=VolumeId,Value=$VOL" \
        --start-time "$START" \
        --end-time "$END" \
        --period "$PERIOD" \
        --statistics Maximum Average Sum SampleCount \
        --output json > "$CW/${METRIC}-p${PERIOD}.json" || true
    done
  done
  python - "$CW" <<'PY'
import json, sys
from pathlib import Path
out = Path(sys.argv[1])
n_iops = 0
for p in sorted(out.glob("Volume*.json")):
    d = json.loads(p.read_text(encoding="utf-8"))
    dps = d.get("Datapoints") or []
    print(f"{p.name}: n={len(dps)}")
    if "VolumeIOPSExceededCheck" in p.name:
        n_iops += len(dps)
    for dp in sorted(dps, key=lambda x: str(x.get("Timestamp", "")))[:8]:
        print(f"  {dp.get('Timestamp')} max={dp.get('Maximum')} avg={dp.get('Average')} sum={dp.get('Sum')}")
print(f"EXCEEDED_IOPS_POINTS={n_iops}")
PY
}

# Soft max-hours from launched_at
if [ -f "$STAGE/launched_at.txt" ]; then
  launched=$(tr -d '\r\n' < "$STAGE/launched_at.txt")
  now_epoch=$(date +%s)
  launched_epoch=$(date -d "$launched" +%s 2>/dev/null || python -c "from datetime import datetime; print(int(datetime.fromisoformat('$launched'.replace('Z','+00:00')).timestamp()))" 2>/dev/null || echo 0)
  if [ "$launched_epoch" != "0" ]; then
    elapsed=$(( now_epoch - launched_epoch ))
    limit=$(( MAX_HOURS * 3600 ))
    if [ "$elapsed" -ge "$limit" ]; then
      echo "STATE=TIMEOUT elapsed=${elapsed}s limit=${limit}s"
      mkdir -p "$PULL"
      scp -i "$KEY" -o StrictHostKeyChecking=no -r "ec2-user@${IP}:/opt/xycalc/results/." "$PULL/" 2>/dev/null || true
      date -Iseconds > "$PULL/pulled_at.txt" 2>/dev/null || true
      teardown
      exit 0
    fi
  fi
fi

STATUS=$("${SSH[@]}" 'bash -s' <<'EOS' || echo CHECK_FAIL
set +e
alive=0
[ -f /opt/xycalc/results/probe.pid ] && ps -p "$(cat /opt/xycalc/results/probe.pid)" >/dev/null 2>&1 && alive=1
if [ -f /opt/xycalc/results/DONE ]; then
  echo STATE=DONE
elif [ -f /opt/xycalc/results/FAIL ]; then
  echo STATE=FAIL
  cat /opt/xycalc/results/FAIL
elif [ "$alive" -eq 1 ]; then
  echo STATE=RUNNING
else
  echo STATE=STALLED
fi
echo "alive=$alive"
echo '---log---'
tail -25 /opt/xycalc/results/run.log 2>/dev/null | tr -d '\000'
EOS
)

echo "$STATUS"

finish_with_cw() {
  echo 'Pulling guest results...'
  mkdir -p "$PULL"
  scp -i "$KEY" -o StrictHostKeyChecking=no -r "ec2-user@${IP}:/opt/xycalc/results/." "$PULL/"
  date -Iseconds > "$PULL/pulled_at.txt"
  python - "$PULL" <<'PY'
from pathlib import Path
import json, sys
root = Path(sys.argv[1])
def read(p):
    f = root / p
    return f.read_text(encoding="utf-8", errors="ignore").strip() if f.exists() else None
ws = read("window_start.txt")
we = read("window_end.txt")
print(f"WINDOW_START={ws}")
print(f"WINDOW_END={we}")
(root / "window_for_cw.txt").write_text(f"{ws}\n{we}\n", encoding="utf-8")
PY
  WS=$(python -c "from pathlib import Path; t=Path(r'$PULL/window_start.txt'); print(t.read_text().strip() if t.exists() else '')")
  WE=$(python -c "from pathlib import Path; t=Path(r'$PULL/window_end.txt'); print(t.read_text().strip() if t.exists() else '')")
  if [ -z "$WS" ] || [ -z "$WE" ]; then
    WS=$(python -c "from datetime import datetime,timedelta,timezone; print((datetime.now(timezone.utc)-timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ'))")
    WE=$(python -c "from datetime import datetime,timezone; print(datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))")
  fi
  # CloudWatch end must be after the last minute; pad 5 minutes.
  WE_PAD=$(python -c "from datetime import datetime,timedelta; s='$WE'.replace('Z','+00:00'); d=datetime.fromisoformat(s)+timedelta(minutes=5); print(d.strftime('%Y-%m-%dT%H:%M:%SZ'))")
  WS_PAD=$(python -c "from datetime import datetime,timedelta; s='$WS'.replace('Z','+00:00'); d=datetime.fromisoformat(s)-timedelta(minutes=2); print(d.strftime('%Y-%m-%dT%H:%M:%SZ'))")
  deadline=$(( $(date +%s) + CW_WAIT_SEC ))
  while true; do
    out=$(pull_cw "$WS_PAD" "$WE_PAD" 2>&1 | tee -a "$STAGE/cw-pull.log")
    echo "$out"
    if echo "$out" | grep -q 'EXCEEDED_IOPS_POINTS=[1-9]'; then
      echo "ExceededCheck datapoints landed."
      break
    fi
    now=$(date +%s)
    if [ "$now" -ge "$deadline" ]; then
      echo "CW wait expired (${CW_WAIT_SEC}s) — ExceededCheck still empty. Tearing down anyway."
      break
    fi
    echo "ExceededCheck still empty; sleep 60s..."
    sleep 60
  done
  teardown
  ls -la "$PULL" || true
  echo '=== xcheck-summary ==='
  cat "$PULL/xcheck-summary.json" 2>/dev/null || true
}

if echo "$STATUS" | grep -q 'STATE=DONE'; then
  finish_with_cw
elif echo "$STATUS" | grep -q 'STATE=FAIL\|STATE=TIMEOUT'; then
  echo 'FAIL/TIMEOUT — pulling whatever exists, then teardown'
  mkdir -p "$PULL"
  scp -i "$KEY" -o StrictHostKeyChecking=no -r "ec2-user@${IP}:/opt/xycalc/results/." "$PULL/" 2>/dev/null || true
  date -Iseconds > "$PULL/pulled_at.txt" 2>/dev/null || true
  teardown
  exit 1
elif echo "$STATUS" | grep -q 'STATE=STALLED\|CHECK_FAIL'; then
  echo STALLED_NEEDS_ATTENTION
  exit 2
else
  echo STILL_RUNNING
fi
