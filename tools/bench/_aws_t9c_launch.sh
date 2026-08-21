#!/usr/bin/env bash
# T9 Arm C — real EBS gp3 on a short-lived m6i.large (us-east-2).
#
# Default: write PLAN.txt only (no EC2 resources).
# Real launch when Arm A+B done:
#   CONFIRM_T9C_LAUNCH=1 T9_ARM_AB_DONE=1 ./tools/bench/_aws_t9c_launch.sh
# Soft gate override (George explicit all-approved when A+B not landed):
#   CONFIRM_T9C_LAUNCH=1 GEORGE_T9C_OVERRIDE=1 ./tools/bench/_aws_t9c_launch.sh
#
# Soft cap ~$5 / MAX_HOURS (default 2). Tag: xycalc-t9c-YYYYMMDD.
# Start watcher immediately after launch:
#   bash tools/bench/_aws_t9c_watcher.sh
set -euo pipefail

REGION="${AWS_REGION:-us-east-2}"
AMI="${T9C_AMI:-ami-06475e8f54266e38e}"
SUBNET="${T9C_SUBNET:-subnet-075ae18eae38d8b80}"
VPC="${T9C_VPC:-vpc-0a6756ac9903ecdcf}"
TYPE="${T9C_TYPE:-m6i.large}"
GP3_GIB="${T9C_GP3_GIB:-100}"
GP3_IOPS="${T9C_GP3_IOPS:-3000}"
GP3_TP="${T9C_GP3_TP:-125}"
MAX_HOURS="${T9C_MAX_HOURS:-2}"
DAY="$(date +%Y%m%d)"
TAG="xycalc-t9c-${DAY}"
ROOT_WIN='C:/Users/gmhow/dev/xycalc'
ROOT=/c/Users/gmhow/dev/xycalc
STAGE_WIN="${ROOT_WIN}/tmp/${TAG}"
STAGE="$ROOT/tmp/${TAG}"
mkdir -p "$STAGE"

COST_NOTE="m6i.large us-east-2 ~\$0.10/hr + gp3 ${GP3_GIB} GiB @ ${GP3_IOPS} IOPS/${GP3_TP} MiB/s; soft max ${MAX_HOURS}h (~\$5)."

cat > "$STAGE/PLAN.txt" <<PLAN
T9 Arm C — real gp3 crossover
region=$REGION type=$TYPE ami=$AMI
subnet=$SUBNET vpc=$VPC
data_volume=gp3 ${GP3_GIB}GiB iops=${GP3_IOPS} throughput=${GP3_TP}
tag=$TAG max_hours=$MAX_HOURS
cost=$COST_NOTE
probe=io_crossover_probe.py on dedicated gp3 (arm=gp3-real)
watcher=tools/bench/_aws_t9c_watcher.sh (terminate on DONE/FAIL/timeout)
gates: CONFIRM_T9C_LAUNCH=${CONFIRM_T9C_LAUNCH:-0} T9_ARM_AB_DONE=${T9_ARM_AB_DONE:-0} GEORGE_T9C_OVERRIDE=${GEORGE_T9C_OVERRIDE:-0}
PLAN

echo "Wrote $STAGE/PLAN.txt"
cat "$STAGE/PLAN.txt"

if [ "${CONFIRM_T9C_LAUNCH:-0}" != "1" ]; then
  echo "Scaffold only — set CONFIRM_T9C_LAUNCH=1 to create EC2 resources."
  exit 0
fi

if [ "${T9_ARM_AB_DONE:-0}" != "1" ] && [ "${GEORGE_T9C_OVERRIDE:-0}" != "1" ]; then
  echo "Refuse launch: T9 Arm A+B not marked done. Set T9_ARM_AB_DONE=1 or GEORGE_T9C_OVERRIDE=1." >&2
  exit 2
fi

if [ "${T9_ARM_AB_DONE:-0}" != "1" ]; then
  echo "NOTE: launching with GEORGE_T9C_OVERRIDE=1 (Arm A+B soft gate bypassed by explicit approval)." | tee "$STAGE/OVERRIDE.txt"
fi

rm -rf "$STAGE/pack" && mkdir -p "$STAGE/pack/tools/bench"
cp -a "$ROOT/tools/bench/io_crossover_probe.py" "$STAGE/pack/tools/bench/"
(cd "$STAGE/pack" && tar czf "$STAGE/harness.tar.gz" tools)

# Remote runner (uploaded separately — avoids nested-quote hell)
cat > "$STAGE/run_t9c.sh" <<RUNEOF
#!/usr/bin/env bash
set -euxo pipefail
mkdir -p /opt/xycalc/results
sudo mkdir -p /mnt/gp3
exec > >(tee -a /opt/xycalc/results/run.log) 2>&1

DEV=""
for i in \$(seq 1 30); do
  DEV=\$(lsblk -ndo NAME,TYPE | awk '\$2=="disk"{print \$1}' | grep -v '^nvme0n1\$' | head -1 || true)
  if [ -n "\$DEV" ]; then break; fi
  sleep 2
done
if [ -z "\$DEV" ]; then
  echo FAIL_NO_DATA_DISK | tee /opt/xycalc/results/FAIL
  exit 1
fi
echo "/dev/\$DEV" | tee /opt/xycalc/results/data_dev.txt
lsblk | tee /opt/xycalc/results/lsblk.txt
sudo mkfs.xfs -f "/dev/\$DEV"
sudo mount "/dev/\$DEV" /mnt/gp3
sudo chown ec2-user:ec2-user /mnt/gp3
df -h /mnt/gp3 | tee /opt/xycalc/results/df.txt

FILE_MB=\${PROBE_FILE_MB:-4096}
RUNTIME=\${PROBE_RUNTIME:-12}
TEST=/mnt/gp3/io-probe-\${FILE_MB}m.bin
dd if=/dev/zero of="\$TEST" bs=1M count="\$FILE_MB" status=none

python3 /opt/xycalc/tools/bench/io_crossover_probe.py \\
  --test-file "\$TEST" \\
  --device "/dev/\$DEV" \\
  --arm gp3-real \\
  --sizes-kib 4,8,16,32,64,128,256,512,1024 \\
  --runtime "\$RUNTIME" \\
  --iodepth 32 \\
  --throttle-iops ${GP3_IOPS} \\
  --throttle-bps \$((${GP3_TP} * 1024 * 1024)) \\
  > /opt/xycalc/results/probe.stdout 2> /opt/xycalc/results/probe.stderr
ec=\$?
if [ \$ec -eq 0 ] && grep -q '===JSON===' /opt/xycalc/results/probe.stderr; then
  awk '/^===JSON===/{f=1;next} f' /opt/xycalc/results/probe.stderr > /opt/xycalc/results/io-crossover-gp3.json
  echo DONE > /opt/xycalc/results/DONE
else
  echo FAIL > /opt/xycalc/results/FAIL
  echo exit=\$ec >> /opt/xycalc/results/FAIL
  exit 1
fi
RUNEOF
python -c "import pathlib; p=pathlib.Path(r'$STAGE_WIN/run_t9c.sh'); p.write_bytes(p.read_bytes().replace(b'\r\n',b'\n'))"

cat > "$STAGE/userdata.sh" <<'UDEOF'
#!/bin/bash
set -euxo pipefail
exec > /var/log/xycalc-t9c-bootstrap.log 2>&1
dnf install -y fio python3
touch /var/lib/xycalc-t9c-ready
UDEOF
python -c "import pathlib; p=pathlib.Path(r'$STAGE_WIN/userdata.sh'); p.write_bytes(p.read_bytes().replace(b'\r\n',b'\n'))"

aws configure set region "$REGION"
KEY="xycalc-t9c-${TAG}"
aws ec2 create-key-pair --region "$REGION" --key-name "$KEY" --query KeyMaterial --output text > "$STAGE/${KEY}.pem"
python -c "import pathlib; p=pathlib.Path(r'$STAGE_WIN/${KEY}.pem'); t=p.read_text(encoding='utf-8', errors='ignore'); p.write_text(t.replace('\r\n','\n'), encoding='ascii', newline='\n')"
chmod 600 "$STAGE/${KEY}.pem"

SG=$(aws ec2 create-security-group --region "$REGION" --group-name "$KEY" \
  --description "xycalc T9c ephemeral" --vpc-id "$VPC" --query GroupId --output text)
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
    "ResourceType=instance,Tags=[{Key=Name,Value=${TAG}},{Key=xycalc,Value=t9c},{Key=xycalc-test,Value=t9c}]" \
    "ResourceType=volume,Tags=[{Key=Name,Value=${TAG}-vol},{Key=xycalc,Value=t9c}]" \
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
IP=$(aws ec2 describe-instances --region "$REGION" --instance-ids "$IID" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
echo "$IP" > "$STAGE/ip.txt"
echo "PUBLIC_IP=$IP"

SSH=(ssh -i "$STAGE/${KEY}.pem" -o StrictHostKeyChecking=no -o ConnectTimeout=10
     -o BatchMode=yes "ec2-user@${IP}")
for i in $(seq 1 36); do
  if "${SSH[@]}" 'test -f /var/lib/xycalc-t9c-ready'; then
    echo "bootstrap ready"
    break
  fi
  echo "wait bootstrap $i"
  sleep 10
done

scp -i "$STAGE/${KEY}.pem" -o StrictHostKeyChecking=no \
  "$STAGE/harness.tar.gz" "$STAGE/run_t9c.sh" "ec2-user@${IP}:/tmp/"
"${SSH[@]}" 'sudo mkdir -p /opt/xycalc && sudo tar xzf /tmp/harness.tar.gz -C /opt/xycalc && sudo cp /tmp/run_t9c.sh /opt/xycalc/run_t9c.sh && sudo chown -R ec2-user:ec2-user /opt/xycalc && chmod +x /opt/xycalc/run_t9c.sh'

"${SSH[@]}" 'mkdir -p /opt/xycalc/results; nohup bash /opt/xycalc/run_t9c.sh >/opt/xycalc/results/nohup.out 2>&1 & echo $! > /opt/xycalc/results/probe.pid; echo started pid=$(cat /opt/xycalc/results/probe.pid); sleep 3; tail -20 /opt/xycalc/results/run.log 2>/dev/null || tail -20 /opt/xycalc/results/nohup.out; ls -la /opt/xycalc/results/'

echo "T9c running on ${IP} (${IID}). Stage: ${STAGE}"
echo "Start watcher NOW: T9C_STAGE=$STAGE bash tools/bench/_aws_t9c_watcher.sh"
echo "Teardown: aws ec2 terminate-instances --region ${REGION} --instance-ids ${IID}"
