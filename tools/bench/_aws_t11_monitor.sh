#!/usr/bin/env bash
# Check AWS T11; if DONE, pull results and terminate instance.
set -eu
STAGE=/c/Users/gmhow/dev/xycalc/tmp/xycalc-t11-20260821-0147
KEY="$STAGE/xycalc-t11-xycalc-t11-20260821-0147.pem"
IP=$(cat "$STAGE/ip.txt")
IID=$(cat "$STAGE/instance.id")
REGION=us-east-2
PULL=/c/Users/gmhow/dev/xycalc/tmp/xycalc-t11-20260821-0147/results
SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=15 -o BatchMode=yes "ec2-user@${IP}")

STATUS=$("${SSH[@]}" 'bash -s' <<'EOS' || echo CHECK_FAIL
set +e
njson=$(ls /opt/xycalc/results/share*.json 2>/dev/null | wc -l)
alive=0
[ -f /opt/xycalc/results/sweep.pid ] && ps -p "$(cat /opt/xycalc/results/sweep.pid)" >/dev/null 2>&1 && alive=1
if [ -f /opt/xycalc/results/DONE ] || grep -qx 'DONE' /opt/xycalc/results/sweep.log 2>/dev/null; then
  echo STATE=DONE
elif [ "$njson" -ge 4 ] && [ "$alive" -eq 0 ]; then
  echo STATE=DONE
elif [ "$alive" -eq 1 ]; then
  echo STATE=RUNNING
else
  echo STATE=STALLED
fi
echo "json_files=$njson alive=$alive"
echo '---log---'
tail -20 /opt/xycalc/results/sweep.log 2>/dev/null | tr -d '\000'
echo '---docker---'
docker ps --format '{{.Names}}' 2>/dev/null
echo '---pid---'
ps -p "$(cat /opt/xycalc/results/sweep.pid 2>/dev/null)" -o etime=,cmd= 2>/dev/null || echo dead
EOS
)

echo "$STATUS"

if echo "$STATUS" | grep -q 'STATE=DONE'; then
  echo 'Pulling results...'
  mkdir -p "$PULL"
  scp -i "$KEY" -o StrictHostKeyChecking=no -r "ec2-user@${IP}:/opt/xycalc/results/." "$PULL/"
  date -Iseconds > "$PULL/pulled_at.txt"
  echo "Terminating $IID..."
  aws ec2 terminate-instances --region "$REGION" --instance-ids "$IID" --output json
  # Best-effort cleanup of ephemeral SG + key (ignore failures)
  KEYNAME=$(cat "$STAGE/keyname.txt" 2>/dev/null || true)
  SG=$(cat "$STAGE/sg.txt" 2>/dev/null || true)
  sleep 5
  aws ec2 delete-security-group --region "$REGION" --group-id "$SG" 2>/dev/null || true
  aws ec2 delete-key-pair --region "$REGION" --key-name "$KEYNAME" 2>/dev/null || true
  echo TEARDOWN=OK
  ls -la "$PULL"
  echo '=== summary ==='
  cat "$PULL/summary.jsonl" 2>/dev/null || true
elif echo "$STATUS" | grep -q 'STATE=STALLED'; then
  echo STALLED_NEEDS_ATTENTION
  exit 2
else
  echo STILL_RUNNING
fi
