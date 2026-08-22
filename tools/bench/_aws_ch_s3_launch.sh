#!/usr/bin/env bash
# Arm 4 — ClickHouse MergeTree on real AWS S3 (s3_stack SKIP_MINIO=1).
#
#   CONFIRM_CH_S3=1 bash tools/bench/_aws_ch_s3_launch.sh
# Soft ≤ \$20; empty+delete bucket after. Campaign hard = highest budget.
set -euo pipefail

REGION="${AWS_REGION:-us-east-2}"
AMI="${CHS3_AMI:-ami-06475e8f54266e38e}"
SUBNET="${CHS3_SUBNET:-subnet-075ae18eae38d8b80}"
VPC="${CHS3_VPC:-vpc-0a6756ac9903ecdcf}"
TYPE="${CHS3_TYPE:-m6i.large}"
ROOT_GIB="${CHS3_ROOT_GIB:-40}"
MAX_HOURS="${CHS3_MAX_HOURS:-2}"
DAY="$(date +%Y%m%d)"
TAG="${CHS3_TAG:-xycalc-ch-s3-${DAY}e}"
BUCKET="${CHS3_BUCKET:-xycalc-ch-s3-${DAY}-$(date +%H%M%S)}"
ROOT_WIN='C:/Users/gmhow/dev/xycalc'
ROOT=/c/Users/gmhow/dev/xycalc
STAGE_WIN="${ROOT_WIN}/tmp/${TAG}"
STAGE="$ROOT/tmp/${TAG}"
mkdir -p "$STAGE"

COST_NOTE="${TYPE} + S3 bucket ${BUCKET}; soft ≤\$20 / max ${MAX_HOURS}h."

cat > "$STAGE/PLAN.txt" <<PLAN
ClickHouse → real S3 via s3_stack (SKIP_MINIO=1)
region=$REGION type=$TYPE ami=$AMI
subnet=$SUBNET vpc=$VPC root=${ROOT_GIB}GiB
bucket=$BUCKET
tag=$TAG max_hours=$MAX_HOURS
cost=$COST_NOTE
watcher=reuse _aws_t9c_watcher.sh with T9C_STAGE=$STAGE
PLAN
echo "Wrote $STAGE/PLAN.txt"
cat "$STAGE/PLAN.txt"

if [ "${CONFIRM_CH_S3:-0}" != "1" ]; then
  echo "Scaffold only — set CONFIRM_CH_S3=1 to create EC2+S3 resources."
  exit 0
fi

CREDITS=$(aws freetier get-account-plan-state --query 'accountPlanRemainingCredits.amount' --output text 2>/dev/null || echo 200)
HARD=$(aws budgets describe-budgets --account-id 189575358547 --query 'max_by(Budgets, &to_number(BudgetLimit.Amount)).BudgetLimit.Amount' --output text 2>/dev/null || echo 150)
ACTUAL=$(aws budgets describe-budget --account-id 189575358547 --budget-name SwampLink --query 'Budget.CalculatedSpend.ActualSpend.Amount' --output text 2>/dev/null || echo 0)
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

echo "$BUCKET" > "$STAGE/bucket.txt"

rm -rf "$STAGE/pack" && mkdir -p "$STAGE/pack/tools/bench"
cp -a "$ROOT/tools/bench/s3_stack" "$STAGE/pack/tools/bench/"
cp -a "$ROOT/tools/bench/celery_probe" "$STAGE/pack/tools/bench/"
# Never ship a prior MinIO results.json into the probe cwd — run_ch_s3 used to
# clobber the real OUT with it after a successful AWS run.
rm -f "$STAGE/pack/tools/bench/s3_stack/results.json" \
      "$STAGE/pack/tools/bench/s3_stack/clickhouse/config.d/storage.local.xml" || true
(cd "$STAGE/pack" && tar czf "$STAGE/harness.tar.gz" tools)

IAM_USER="${CHS3_IAM_USER:-xycalc-ch-s3-${DAY}e}"
echo "$IAM_USER" > "$STAGE/iam_user.txt"
aws iam create-user --user-name "$IAM_USER" >/dev/null || true
POLICY_DOC=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:*"],
      "Resource": [
        "arn:aws:s3:::${BUCKET}",
        "arn:aws:s3:::${BUCKET}/*"
      ]
    }
  ]
}
EOF
)
aws iam put-user-policy --user-name "$IAM_USER" --policy-name xycalc-ch-s3-inline --policy-document "$POLICY_DOC"
CREDS=$(aws iam create-access-key --user-name "$IAM_USER" --output json)
AK=$(python -c "import json,sys; print(json.load(sys.stdin)['AccessKey']['AccessKeyId'])" <<<"$CREDS")
SK=$(python -c "import json,sys; print(json.load(sys.stdin)['AccessKey']['SecretAccessKey'])" <<<"$CREDS")
echo "$AK" > "$STAGE/access_key_id.txt"
printf '%s' "$SK" > "$STAGE/secret_access_key.txt"
chmod 600 "$STAGE/secret_access_key.txt"

aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
  --create-bucket-configuration LocationConstraint="$REGION" >/dev/null
echo "BUCKET=$BUCKET"

python - <<PY
from pathlib import Path
ak = Path(r"$STAGE_WIN/access_key_id.txt").read_text().strip()
sk = Path(r"$STAGE_WIN/secret_access_key.txt").read_text().strip()
bucket = "$BUCKET"
region = "$REGION"
xml = f"""<clickhouse>
    <storage_configuration>
        <disks>
            <s3>
                <type>s3</type>
                <endpoint>https://s3.{region}.amazonaws.com/{bucket}//</endpoint>
                <access_key_id>{ak}</access_key_id>
                <secret_access_key>{sk}</secret_access_key>
                <region>{region}</region>
                <metadata_path>/var/lib/clickhouse/disks/s3/</metadata_path>
            </s3>
            <s3_cache>
                <type>cache</type>
                <disk>s3</disk>
                <path>/var/lib/clickhouse/disks/s3_cache/</path>
                <max_size>2Gi</max_size>
            </s3_cache>
        </disks>
        <policies>
            <s3_main>
                <volumes>
                    <main>
                        <disk>s3</disk>
                    </main>
                </volumes>
            </s3_main>
        </policies>
    </storage_configuration>
</clickhouse>
"""
Path(r"$STAGE_WIN/storage.local.xml").write_text(xml, encoding="utf-8", newline="\n")
print("wrote storage.local.xml")
PY

cat > "$STAGE/run_ch_s3.sh" <<'RUNEOF'
#!/usr/bin/env bash
set -euxo pipefail
mkdir -p /opt/xycalc/results
exec > >(tee -a /opt/xycalc/results/run.log) 2>&1

if ! command -v docker >/dev/null 2>&1; then
  sudo dnf install -y docker
  sudo systemctl enable --now docker
fi
sudo systemctl start docker || true
sudo usermod -aG docker ec2-user || true
# Ephemeral probe: open socket so compose works without re-login / newgrp.
# Also used if docker.sock is recreated after daemon restart.
sudo chmod 666 /var/run/docker.sock || true

if ! docker compose version >/dev/null 2>&1; then
  sudo mkdir -p /usr/local/lib/docker/cli-plugins
  sudo curl -fsSL https://github.com/docker/compose/releases/download/v2.29.7/docker-compose-linux-x86_64 \
    -o /usr/local/lib/docker/cli-plugins/docker-compose
  sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
fi

# Wait until docker works for this process (group may not be active yet).
# Do NOT pipe docker info to head under pipefail — SIGPIPE aborts the probe.
docker_ready=0
for i in $(seq 1 60); do
  sudo chmod 666 /var/run/docker.sock 2>/dev/null || true
  if docker info >/opt/xycalc/results/docker-info.txt 2>/opt/xycalc/results/docker-info.err; then
    docker_ready=1
    break
  fi
  if sg docker -c 'docker info' >/opt/xycalc/results/docker-info.txt 2>/opt/xycalc/results/docker-info.err; then
    docker_ready=1
    break
  fi
  echo "wait docker $i"
  sleep 2
done
if [ "$docker_ready" != "1" ]; then
  echo FAIL_DOCKER | tee /opt/xycalc/results/FAIL
  cat /opt/xycalc/results/docker-info.err || true
  exit 1
fi
head -8 /opt/xycalc/results/docker-info.txt || true

sudo mkdir -p /opt/xycalc
sudo tar xzf /tmp/harness.tar.gz -C /opt/xycalc
sudo cp /tmp/storage.local.xml /opt/xycalc/tools/bench/s3_stack/clickhouse/config.d/storage.local.xml
sudo cp /tmp/storage.local.xml /opt/xycalc/tools/bench/s3_stack/clickhouse/config.d/storage.xml
sudo chown -R ec2-user:ec2-user /opt/xycalc

cd /opt/xycalc/tools/bench/s3_stack
chmod +x perf.sh run.sh sample.py || true

export MONGO_CACHE_GB=0.5
export MONGO_MEM=2g
# 800k ≈ 579 MB ≈ 1.1× a 0.5 GB WT cache — refuse gate needs ≥2.0×.
# 1.5M matched the swamplink MinIO observation (≈1086 MB / 2.02×).
export PROBE_DOCS=1500000
export PROBE_MIN_OVERSUB=2.0
export PROBE_RATES=50
export PROBE_SECONDS=20
export PROBE_CONCURRENCY=4
export CH_ROWS=2000000
export SKIP_MINIO=1
export CLICKHOUSE_STORAGE_XML=./clickhouse/config.d/storage.local.xml
export OUT=/opt/xycalc/results/s3_stack_results.json

# chmod 666 on the socket above is enough for this ephemeral probe.
# Do NOT wrap via `sg docker -c` — on Amazon Linux that can drop exports
# (SKIP_MINIO disappears and sample.py then requires MinIO).
date -Iseconds | tee /opt/xycalc/results/window_start.txt
./perf.sh --down > /opt/xycalc/results/perf.stdout.txt 2> /opt/xycalc/results/perf.stderr.txt \
  || {
    echo FAIL_PERF | tee /opt/xycalc/results/FAIL
    tail -120 /opt/xycalc/results/perf.stderr.txt
    for f in /tmp/s3_stack_mongo_load.err /tmp/s3_stack_mongo_load.out /tmp/s3_stack_drive.err; do
      if [ -f "$f" ]; then
        echo "===== $f ====="
        tail -80 "$f"
        cp -f "$f" /opt/xycalc/results/ 2>/dev/null || true
      fi
    done
    exit 1
  }
# perf.sh already wrote $OUT (= /opt/xycalc/results/s3_stack_results.json).
# Do NOT overwrite with the packaged stub tools/bench/s3_stack/results.json.
if [ ! -s /opt/xycalc/results/s3_stack_results.json ]; then
  echo "FAIL: missing s3_stack_results.json after perf" | tee /opt/xycalc/results/FAIL
  exit 1
fi
# Keep a cwd copy for local inspection without clobbering OUT.
cp -f /opt/xycalc/results/s3_stack_results.json ./results.live.json 2>/dev/null || true
date -Iseconds | tee /opt/xycalc/results/window_end.txt
echo DONE > /opt/xycalc/results/DONE
RUNEOF
python -c "import pathlib; p=pathlib.Path(r'$STAGE_WIN/run_ch_s3.sh'); p.write_bytes(p.read_bytes().replace(b'\r\n', b'\n'))"

# Fast ready marker — do not block on dnf in userdata
cat > "$STAGE/userdata.sh" <<'UDEOF'
#!/bin/bash
set -euxo pipefail
exec > /var/log/xycalc-ch-s3-bootstrap.log 2>&1
touch /var/lib/xycalc-ch-s3-ready
dnf install -y python3 curl || true
UDEOF
python -c "import pathlib; p=pathlib.Path(r'$STAGE_WIN/userdata.sh'); p.write_bytes(p.read_bytes().replace(b'\r\n', b'\n'))"

cat > "$STAGE/teardown_s3_iam.sh" <<TEAR
#!/usr/bin/env bash
set -euo pipefail
REGION="$REGION"
BUCKET=\$(tr -d '\\r\\n' < "$STAGE/bucket.txt")
IAM_USER=\$(tr -d '\\r\\n' < "$STAGE/iam_user.txt")
AK=\$(tr -d '\\r\\n' < "$STAGE/access_key_id.txt" 2>/dev/null || true)
echo "empty+delete bucket=\$BUCKET"
aws s3 rm "s3://\$BUCKET" --recursive --region "\$REGION" 2>/dev/null || true
aws s3api delete-bucket --bucket "\$BUCKET" --region "\$REGION" 2>/dev/null || true
if [ -n "\$AK" ]; then
  aws iam delete-access-key --user-name "\$IAM_USER" --access-key-id "\$AK" 2>/dev/null || true
fi
aws iam delete-user-policy --user-name "\$IAM_USER" --policy-name xycalc-ch-s3-inline 2>/dev/null || true
aws iam delete-user --user-name "\$IAM_USER" 2>/dev/null || true
echo S3_IAM_TEARDOWN=OK
TEAR
python -c "import pathlib; p=pathlib.Path(r'$STAGE_WIN/teardown_s3_iam.sh'); p.write_bytes(p.read_bytes().replace(b'\r\n', b'\n'))"
chmod +x "$STAGE/teardown_s3_iam.sh"

aws configure set region "$REGION"
KEY="xycalc-ch-s3-${TAG}"
aws ec2 create-key-pair --region "$REGION" --key-name "$KEY" --query KeyMaterial --output text > "$STAGE/${KEY}.pem"
python -c "import pathlib; p=pathlib.Path(r'$STAGE_WIN/${KEY}.pem'); t=p.read_text(encoding='utf-8', errors='ignore'); p.write_text(t.replace('\r\n','\n'), encoding='ascii', newline='\n')"
chmod 600 "$STAGE/${KEY}.pem"

SG=$(aws ec2 create-security-group --region "$REGION" --group-name "$KEY" \
  --description "xycalc ch s3 ephemeral" --vpc-id "$VPC" --query GroupId --output text)
aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$SG" \
  --protocol tcp --port 22 --cidr 0.0.0.0/0 >/dev/null

UD_WIN="${STAGE_WIN}/userdata.sh"
BDM=$(cat <<EOF
[
  {"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":${ROOT_GIB},"VolumeType":"gp3","DeleteOnTermination":true}}
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
    "ResourceType=instance,Tags=[{Key=Name,Value=${TAG}},{Key=xycalc,Value=ch-s3},{Key=KeepUntil,Value=${DAY}}]" \
    "ResourceType=volume,Tags=[{Key=Name,Value=${TAG}-root},{Key=xycalc,Value=ch-s3}]" \
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
  if "${SSH[@]}" 'test -f /var/lib/xycalc-ch-s3-ready'; then
    echo "bootstrap ready"
    break
  fi
  echo "wait bootstrap $i"
  sleep 10
done

scp -i "$STAGE/${KEY}.pem" -o StrictHostKeyChecking=no \
  "$STAGE/harness.tar.gz" "$STAGE/storage.local.xml" "$STAGE/run_ch_s3.sh" "ec2-user@${IP}:/tmp/"
"${SSH[@]}" 'sudo mkdir -p /opt/xycalc && sudo cp /tmp/run_ch_s3.sh /opt/xycalc/run_ch_s3.sh && sudo chown -R ec2-user:ec2-user /opt/xycalc && chmod +x /opt/xycalc/run_ch_s3.sh'

"${SSH[@]}" 'mkdir -p /opt/xycalc/results; nohup bash /opt/xycalc/run_ch_s3.sh >/opt/xycalc/results/nohup.out 2>&1 & echo $! > /opt/xycalc/results/probe.pid; echo started pid=$(cat /opt/xycalc/results/probe.pid); sleep 8; tail -40 /opt/xycalc/results/run.log 2>/dev/null || tail -40 /opt/xycalc/results/nohup.out'

echo "ch-s3 running on ${IP} (${IID}). Stage: ${STAGE}"
echo "After TEARDOWN=OK also run: bash $STAGE/teardown_s3_iam.sh"
echo "Start watcher: T9C_STAGE=$STAGE bash tools/bench/_aws_t9c_watcher.sh"
