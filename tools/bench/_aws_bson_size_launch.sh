#!/usr/bin/env bash
# Arm 3 — BSON ~1 MiB vs ~15 MiB random reads on real gp3 (same oversub).
#
#   CONFIRM_BSON_SIZE=1 bash tools/bench/_aws_bson_size_launch.sh
# Soft ≤ \$25 this arm; max 2h. Campaign hard = highest budget (xycalc-hard-cap).
set -euo pipefail

REGION="${AWS_REGION:-us-east-2}"
AMI="${BSON_AMI:-ami-06475e8f54266e38e}"
SUBNET="${BSON_SUBNET:-subnet-075ae18eae38d8b80}"
VPC="${BSON_VPC:-vpc-0a6756ac9903ecdcf}"
TYPE="${BSON_TYPE:-m6i.large}"
GP3_GIB="${BSON_GP3_GIB:-100}"
GP3_IOPS="${BSON_GP3_IOPS:-3000}"
GP3_TP="${BSON_GP3_TP:-125}"
MAX_HOURS="${BSON_MAX_HOURS:-2}"
DAY="$(date +%Y%m%d)"
TAG="xycalc-bson-size-${DAY}"
ROOT_WIN='C:/Users/gmhow/dev/xycalc'
ROOT=/c/Users/gmhow/dev/xycalc
STAGE_WIN="${ROOT_WIN}/tmp/${TAG}"
STAGE="$ROOT/tmp/${TAG}"
mkdir -p "$STAGE"

COST_NOTE="${TYPE} + gp3 ${GP3_GIB}GiB @ ${GP3_IOPS}/${GP3_TP}; soft ≤\$25 / max ${MAX_HOURS}h."

cat > "$STAGE/PLAN.txt" <<PLAN
BSON ~1MiB vs ~15MiB on real gp3 (same oversub)
region=$REGION type=$TYPE ami=$AMI
subnet=$SUBNET vpc=$VPC
data_volume=gp3 ${GP3_GIB}GiB iops=${GP3_IOPS} throughput=${GP3_TP}
mongo=docker mongo:7 --wiredTigerCacheSizeGB 0.5 on /mnt/gp3
probe=tools/bench/bson_doc_size_probe.py
tag=$TAG max_hours=$MAX_HOURS
cost=$COST_NOTE
watcher=reuse _aws_t9c_watcher.sh with T9C_STAGE=$STAGE
PLAN
echo "Wrote $STAGE/PLAN.txt"
cat "$STAGE/PLAN.txt"

if [ "${CONFIRM_BSON_SIZE:-0}" != "1" ]; then
  echo "Scaffold only — set CONFIRM_BSON_SIZE=1 to create EC2 resources."
  exit 0
fi

# Spend gate: credits < 50 refuse; soft stop ~80% of hard (highest budget)
CREDITS=$(aws freetier get-account-plan-state --query 'accountPlanRemainingCredits.amount' --output text 2>/dev/null || echo 200)
HARD=$(aws budgets describe-budgets --account-id 189575358547 --query 'max_by(Budgets, &to_number(BudgetLimit.Amount)).BudgetLimit.Amount' --output text 2>/dev/null || echo 150)
ACTUAL=$(aws budgets describe-budget --account-id 189575358547 --budget-name SwampLink --query 'Budget.CalculatedSpend.ActualSpend.Amount' --output text 2>/dev/null || echo 0)
# also take max actual across budgets
ACTUAL_HARD=$(aws budgets describe-budget --account-id 189575358547 --budget-name xycalc-hard-cap --query 'Budget.CalculatedSpend.ActualSpend.Amount' --output text 2>/dev/null || echo 0)
python - <<PY
credits=float("$CREDITS"); hard=float("$HARD"); actual=max(float("$ACTUAL"), float("$ACTUAL_HARD"))
soft=0.8*hard
print(f"spend_gate credits={credits} hard={hard} soft80={soft:.1f} actual={actual}")
if credits < 50:
    raise SystemExit("Refuse: plan credits < \$50")
if actual >= soft:
    raise SystemExit(f"Refuse: actual \${actual} >= soft 80% of hard (\${soft:.1f})")
PY

rm -rf "$STAGE/pack" && mkdir -p "$STAGE/pack/tools/bench"
cp -a "$ROOT/tools/bench/bson_doc_size_probe.py" "$STAGE/pack/tools/bench/"
(cd "$STAGE/pack" && tar czf "$STAGE/harness.tar.gz" tools)

cat > "$STAGE/run_bson.sh" <<'RUNEOF'
#!/usr/bin/env bash
set -euxo pipefail
mkdir -p /opt/xycalc/results
sudo mkdir -p /mnt/gp3
exec > >(tee -a /opt/xycalc/results/run.log) 2>&1

DEV=""
for i in $(seq 1 30); do
  DEV=$(lsblk -ndo NAME,TYPE | awk '$2=="disk"{print $1}' | grep -v '^nvme0n1$' | head -1 || true)
  if [ -n "$DEV" ]; then break; fi
  sleep 2
done
if [ -z "$DEV" ]; then
  echo FAIL_NO_DATA_DISK | tee /opt/xycalc/results/FAIL
  exit 1
fi
echo "/dev/$DEV" | tee /opt/xycalc/results/data_dev.txt
lsblk | tee /opt/xycalc/results/lsblk.txt
sudo mkfs.xfs -f "/dev/$DEV"
sudo mount "/dev/$DEV" /mnt/gp3
sudo chown ec2-user:ec2-user /mnt/gp3
sudo mkdir -p /mnt/gp3/mongo
df -h /mnt/gp3 | tee /opt/xycalc/results/df.txt

# Docker + mongo on the gp3 volume
if ! command -v docker >/dev/null 2>&1; then
  sudo dnf install -y docker
  sudo systemctl enable --now docker
  sudo usermod -aG docker ec2-user || true
fi
# pymongo on host for the probe
python3 -m ensurepip --upgrade || true
python3 -m pip install --user 'pymongo>=4.6' || sudo python3 -m pip install 'pymongo>=4.6'

sudo docker rm -f xycalc-mongo 2>/dev/null || true
sudo docker run -d --name xycalc-mongo \
  -p 27017:27017 \
  -v /mnt/gp3/mongo:/data/db \
  --memory=2g \
  mongo:7 --wiredTigerCacheSizeGB 0.5
for i in $(seq 1 60); do
  if sudo docker exec xycalc-mongo mongosh --quiet --eval 'db.runCommand({ping:1}).ok' 2>/dev/null | grep -q 1; then
    echo mongo_ready
    break
  fi
  sleep 2
done

export PROBE_URI=mongodb://127.0.0.1:27017
export PROBE_CACHE_GB=0.5
export PROBE_TARGET_OVERSUB=2.0
export PROBE_MIN_OVERSUB=2.0
export PROBE_SECONDS=45
export PROBE_WORKERS=8
export PROBE_DOC_BYTES_LIST=1048576,15728640
export PROBE_OUT=/opt/xycalc/results/bson-size-summary.json
export PATH="$HOME/.local/bin:$PATH"

date -Iseconds | tee /opt/xycalc/results/window_start.txt
python3 /opt/xycalc/tools/bench/bson_doc_size_probe.py \
  > /opt/xycalc/results/bson-size.stdout.json 2> /opt/xycalc/results/bson-size.stderr.txt \
  || { echo FAIL_PROBE | tee /opt/xycalc/results/FAIL; exit 1; }
date -Iseconds | tee /opt/xycalc/results/window_end.txt
sudo docker rm -f xycalc-mongo || true
echo DONE > /opt/xycalc/results/DONE
RUNEOF
python -c "import pathlib; p=pathlib.Path(r'$STAGE_WIN/run_bson.sh'); p.write_bytes(p.read_bytes().replace(b'\r\n', b'\n'))"

cat > "$STAGE/userdata.sh" <<'UDEOF'
#!/bin/bash
set -euxo pipefail
exec > /var/log/xycalc-bson-bootstrap.log 2>&1
dnf install -y python3 sysstat
touch /var/lib/xycalc-bson-ready
UDEOF
python -c "import pathlib; p=pathlib.Path(r'$STAGE_WIN/userdata.sh'); p.write_bytes(p.read_bytes().replace(b'\r\n', b'\n'))"

aws configure set region "$REGION"
KEY="xycalc-bson-${TAG}"
aws ec2 create-key-pair --region "$REGION" --key-name "$KEY" --query KeyMaterial --output text > "$STAGE/${KEY}.pem"
python -c "import pathlib; p=pathlib.Path(r'$STAGE_WIN/${KEY}.pem'); t=p.read_text(encoding='utf-8', errors='ignore'); p.write_text(t.replace('\r\n','\n'), encoding='ascii', newline='\n')"
chmod 600 "$STAGE/${KEY}.pem"

SG=$(aws ec2 create-security-group --region "$REGION" --group-name "$KEY" \
  --description "xycalc bson size ephemeral" --vpc-id "$VPC" --query GroupId --output text)
aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$SG" \
  --protocol tcp --port 22 --cidr 0.0.0.0/0 >/dev/null

UD_WIN="${STAGE_WIN}/userdata.sh"
BDM=$(cat <<EOF
[
  {"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":30,"VolumeType":"gp3","DeleteOnTermination":true}},
  {"DeviceName":"/dev/sdf","Ebs":{"VolumeSize":${GP3_GIB},"VolumeType":"gp3","Iops":${GP3_IOPS},"Throughput":${GP3_TP},"DeleteOnTermination":true}}
]
EOF
)

IID=$(aws ec2 run-instances --region "$REGION" \
  --image-id "$AMI" \
  --instance-type "$TYPE" \
  --key-name "$KEY" \
  --security-group-ids "$SG" \
  --subnet-id "$SUBNET" \
  --associate-public-ip-address \
  --block-device-mappings "$BDM" \
  --user-data "fileb://${UD_WIN}" \
  --tag-specifications \
    "ResourceType=instance,Tags=[{Key=Name,Value=${TAG}},{Key=xycalc,Value=bson-size},{Key=KeepUntil,Value=${DAY}}]" \
    "ResourceType=volume,Tags=[{Key=Name,Value=${TAG}-vol},{Key=xycalc,Value=bson-size}]" \
  --query 'Instances[0].InstanceId' --output text)

echo "$IID" > "$STAGE/instance.id"
echo "$TAG" > "$STAGE/tag.txt"
echo "$KEY" > "$STAGE/keyname.txt"
echo "$SG" > "$STAGE/sg.txt"
echo "$MAX_HOURS" > "$STAGE/max_hours.txt"
date -Iseconds > "$STAGE/launched_at.txt"
printf '%s\n' "$STAGE" > "$ROOT/tmp/t9c-latest-stage.txt"

echo "INSTANCE=$IID waiting running..."
aws ec2 wait instance-running --region "$REGION" --instance-ids "$IID"

VOL=$(aws ec2 describe-instances --region "$REGION" --instance-ids "$IID" \
  --query 'Reservations[0].Instances[0].BlockDeviceMappings[?DeviceName==`/dev/sdf`].Ebs.VolumeId | [0]' --output text)
echo "$VOL" > "$STAGE/data_volume.id"
echo "DATA_VOLUME=$VOL"

IP=$(aws ec2 describe-instances --region "$REGION" --instance-ids "$IID" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
echo "$IP" > "$STAGE/ip.txt"
echo "PUBLIC_IP=$IP"

SSH=(ssh -i "$STAGE/${KEY}.pem" -o StrictHostKeyChecking=no -o ConnectTimeout=10
     -o BatchMode=yes "ec2-user@${IP}")
for i in $(seq 1 36); do
  if "${SSH[@]}" 'test -f /var/lib/xycalc-bson-ready'; then
    echo "bootstrap ready"
    break
  fi
  echo "wait bootstrap $i"
  sleep 10
done

scp -i "$STAGE/${KEY}.pem" -o StrictHostKeyChecking=no \
  "$STAGE/harness.tar.gz" "$STAGE/run_bson.sh" "ec2-user@${IP}:/tmp/"
"${SSH[@]}" 'sudo mkdir -p /opt/xycalc && sudo tar xzf /tmp/harness.tar.gz -C /opt/xycalc && sudo cp /tmp/run_bson.sh /opt/xycalc/run_bson.sh && sudo chown -R ec2-user:ec2-user /opt/xycalc && chmod +x /opt/xycalc/run_bson.sh'

"${SSH[@]}" 'mkdir -p /opt/xycalc/results; nohup bash /opt/xycalc/run_bson.sh >/opt/xycalc/results/nohup.out 2>&1 & echo $! > /opt/xycalc/results/probe.pid; echo started pid=$(cat /opt/xycalc/results/probe.pid); sleep 5; tail -30 /opt/xycalc/results/run.log 2>/dev/null || tail -30 /opt/xycalc/results/nohup.out; ls -la /opt/xycalc/results/'

echo "bson-size running on ${IP} (${IID}). Stage: ${STAGE}"
echo "Start watcher: T9C_STAGE=$STAGE bash tools/bench/_aws_t9c_watcher.sh"
