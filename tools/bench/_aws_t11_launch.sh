#!/usr/bin/env bash
# Full T11 on EC2 us-east-2. No instance-role needed: we scp + ssh from COOPER.
set -euxo pipefail
REGION=us-east-2
AMI=ami-06475e8f54266e38e
SUBNET=subnet-075ae18eae38d8b80
VPC=vpc-0a6756ac9903ecdcf
TYPE=r6i.2xlarge
TAG="xycalc-t11-$(date +%Y%m%d-%H%M)"
ROOT_WIN='C:/Users/gmhow/dev/xycalc'
ROOT=/c/Users/gmhow/dev/xycalc
STAGE_WIN="${ROOT_WIN}/tmp/${TAG}"
STAGE="$ROOT/tmp/${TAG}"
mkdir -p "$STAGE"

# Pack harness (LF scripts)
rm -rf "$STAGE/pack" && mkdir -p "$STAGE/pack/tools/bench"
cp -a "$ROOT/tools/bench/colocation_probe" "$STAGE/pack/tools/bench/"
cp -a "$ROOT/tools/bench/celery_probe" "$STAGE/pack/tools/bench/"
find "$STAGE/pack" -name '*.sh' -exec sed -i 's/\r$//' {} \;
(cd "$STAGE/pack" && tar czf "$STAGE/harness.tar.gz" tools)

# Userdata: docker only
cat > "$STAGE/userdata.sh" <<'UDEOF'
#!/bin/bash
set -euxo pipefail
exec > /var/log/xycalc-t11-bootstrap.log 2>&1
dnf install -y docker
systemctl enable --now docker
mkdir -p /usr/local/lib/docker/cli-plugins
curl -fsSL https://github.com/docker/compose/releases/download/v2.29.7/docker-compose-linux-x86_64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
touch /var/lib/xycalc-docker-ready
UDEOF

aws configure set region "$REGION"
KEY="xycalc-t11-${TAG}"
aws ec2 create-key-pair --region "$REGION" --key-name "$KEY" --query KeyMaterial --output text > "$STAGE/${KEY}.pem"
# Windows aws may write UTF-16; force unix pem
python -c "import pathlib; p=pathlib.Path(r'$STAGE_WIN/${KEY}.pem'); t=p.read_text(encoding='utf-8', errors='ignore'); p.write_text(t.replace('\r\n','\n'), encoding='ascii', newline='\n')"
chmod 600 "$STAGE/${KEY}.pem"

SG=$(aws ec2 create-security-group --region "$REGION" --group-name "$KEY" \
  --description "xycalc T11 ephemeral" --vpc-id "$VPC" --query GroupId --output text)
aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$SG" \
  --protocol tcp --port 22 --cidr 0.0.0.0/0 >/dev/null

# fileb:// needs Windows path for AWS CLI on Windows
UD_WIN="${STAGE_WIN}/userdata.sh"
python -c "import pathlib; p=pathlib.Path(r'$STAGE_WIN/userdata.sh'); p.write_bytes(p.read_bytes().replace(b'\r\n',b'\n'))"

IID=$(aws ec2 run-instances --region "$REGION" \
  --image-id "$AMI" \
  --instance-type "$TYPE" \
  --key-name "$KEY" \
  --security-group-ids "$SG" \
  --subnet-id "$SUBNET" \
  --associate-public-ip-address \
  --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":100,"VolumeType":"gp3","DeleteOnTermination":true}}]' \
  --user-data "fileb://${UD_WIN}" \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=${TAG}},{Key=xycalc,Value=t11}]" \
  --query 'Instances[0].InstanceId' --output text)

echo "$IID" > "$STAGE/instance.id"
echo "$TAG" > "$STAGE/tag.txt"
echo "$KEY" > "$STAGE/keyname.txt"
echo "$SG" > "$STAGE/sg.txt"
echo "INSTANCE=$IID waiting running..."
aws ec2 wait instance-running --region "$REGION" --instance-ids "$IID"
IP=$(aws ec2 describe-instances --region "$REGION" --instance-ids "$IID" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
echo "$IP" > "$STAGE/ip.txt"
echo "PUBLIC_IP=$IP"

# Wait SSH + docker ready
SSH=(ssh -i "$STAGE/${KEY}.pem" -o StrictHostKeyChecking=no -o ConnectTimeout=10
     -o BatchMode=yes "ec2-user@${IP}")
for i in $(seq 1 36); do
  if "${SSH[@]}" 'test -f /var/lib/xycalc-docker-ready && docker info >/dev/null'; then
    echo "docker ready"
    break
  fi
  echo "wait docker $i"
  sleep 10
done

scp -i "$STAGE/${KEY}.pem" -o StrictHostKeyChecking=no \
  "$STAGE/harness.tar.gz" "ec2-user@${IP}:/tmp/harness.tar.gz"
"${SSH[@]}" 'sudo mkdir -p /opt/xycalc && sudo tar xzf /tmp/harness.tar.gz -C /opt/xycalc && sudo chown -R ec2-user:ec2-user /opt/xycalc'

# Detached nohup sweep — survives SSH drop
"${SSH[@]}" 'bash -s' <<'REMOTE'
set -euxo pipefail
cd /opt/xycalc/tools/bench/colocation_probe
chmod +x share_sweep.sh run.sh
mkdir -p /opt/xycalc/results
nohup env MONGO_MEM_GB=8 SHARE_PCTS=50,60,70,80 OVERSUB=2.5 \
  REDIS_MEM=4g CLICKHOUSE_MEM=8g WORKER_MEM=2g \
  OUTDIR=/opt/xycalc/results \
  bash ./share_sweep.sh > /opt/xycalc/results/sweep.log 2>&1 &
echo $! > /opt/xycalc/results/sweep.pid
echo started pid=$(cat /opt/xycalc/results/sweep.pid)
REMOTE

sleep 15
"${SSH[@]}" 'tail -20 /opt/xycalc/results/sweep.log; docker ps --format {{.Names}}'
echo "T11 running on ${IP} (${IID}). Pull later: scp -i ${STAGE}/${KEY}.pem -r ec2-user@${IP}:/opt/xycalc/results ."
echo "Teardown: aws ec2 terminate-instances --region ${REGION} --instance-ids ${IID}"
