#!/usr/bin/env bash
# Check AWS T9c; on DONE/FAIL pull results and terminate. Soft max-hours kill.
set -eu
ROOT=/c/Users/gmhow/dev/xycalc
REGION="${AWS_REGION:-us-east-2}"

if [ -n "${T9C_STAGE:-}" ]; then
  STAGE="$T9C_STAGE"
elif [ -f "$ROOT/tmp/t9c-latest-stage.txt" ]; then
  STAGE=$(tr -d '\r\n' < "$ROOT/tmp/t9c-latest-stage.txt")
elif [ -L "$ROOT/tmp/t9c-latest" ]; then
  STAGE=$(readlink -f "$ROOT/tmp/t9c-latest" 2>/dev/null || readlink "$ROOT/tmp/t9c-latest")
else
  echo "No T9C stage — set T9C_STAGE or run launch first." >&2
  exit 1
fi

KEYNAME=$(tr -d '\r\n' < "$STAGE/keyname.txt")
KEY="$STAGE/${KEYNAME}.pem"
IP=$(tr -d '\r\n' < "$STAGE/ip.txt")
IID=$(tr -d '\r\n' < "$STAGE/instance.id")
MAX_HOURS=$(tr -d '\r\n' < "$STAGE/max_hours.txt" 2>/dev/null || echo 2)
PULL="$STAGE/results"
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
  echo TEARDOWN=OK
}

# Soft max-hours from launched_at
if [ -f "$STAGE/launched_at.txt" ]; then
  launched=$(tr -d '\r\n' < "$STAGE/launched_at.txt")
  # GNU date on Git Bash: convert to epoch if possible
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
echo '---pid---'
ps -p "$(cat /opt/xycalc/results/probe.pid 2>/dev/null)" -o etime=,cmd= 2>/dev/null || echo dead
EOS
)

echo "$STATUS"

if echo "$STATUS" | grep -q 'STATE=DONE'; then
  echo 'Pulling results...'
  mkdir -p "$PULL"
  scp -i "$KEY" -o StrictHostKeyChecking=no -r "ec2-user@${IP}:/opt/xycalc/results/." "$PULL/"
  date -Iseconds > "$PULL/pulled_at.txt"
  teardown
  ls -la "$PULL"
  echo '=== crossover json head ==='
  head -40 "$PULL/io-crossover-gp3.json" 2>/dev/null || true
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
