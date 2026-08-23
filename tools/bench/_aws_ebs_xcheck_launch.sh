#!/usr/bin/env bash
# Arm 1 — real gp3: pair guest iostat (1s) with CloudWatch ExceededChecks.
#
# Drive under then over provisioned IOPS; leave VolumeId + window timestamps
# so COOPER can pull VolumeIOPSExceededCheck / VolumeThroughputExceededCheck.
#
#   CONFIRM_EBS_XCHECK=1 bash tools/bench/_aws_ebs_xcheck_launch.sh
# Soft: m6i.large + 100 GiB gp3 @ 3000/125, max 2h (~\$1). Campaign soft \$125 / hard \$150.
set -euo pipefail

REGION="${AWS_REGION:-us-east-2}"
AMI="${EBS_XCHECK_AMI:-ami-06475e8f54266e38e}"
SUBNET="${EBS_XCHECK_SUBNET:-subnet-075ae18eae38d8b80}"
VPC="${EBS_XCHECK_VPC:-vpc-0a6756ac9903ecdcf}"
TYPE="${EBS_XCHECK_TYPE:-m6i.large}"
GP3_GIB="${EBS_XCHECK_GP3_GIB:-100}"
GP3_IOPS="${EBS_XCHECK_GP3_IOPS:-3000}"
GP3_TP="${EBS_XCHECK_GP3_TP:-125}"
MAX_HOURS="${EBS_XCHECK_MAX_HOURS:-2}"
UNDER_IOPS="${EBS_XCHECK_UNDER_IOPS:-2000}"
UNDER_RUNTIME="${EBS_XCHECK_UNDER_RUNTIME:-180}"
OVER_RUNTIME="${EBS_XCHECK_OVER_RUNTIME:-180}"
RUN_MONGO="${EBS_XCHECK_RUN_MONGO:-0}"
DAY="$(date +%Y%m%d)"
TAG="${EBS_XCHECK_TAG:-xycalc-ebs-xcheck-${DAY}}"
ROOT_WIN='C:/Users/gmhow/dev/xycalc'
ROOT=/c/Users/gmhow/dev/xycalc
STAGE_WIN="${ROOT_WIN}/tmp/${TAG}"
STAGE="$ROOT/tmp/${TAG}"
mkdir -p "$STAGE"

COST_NOTE="${TYPE} us-east-2 + gp3 ${GP3_GIB}GiB @ ${GP3_IOPS}/${GP3_TP}; under ${UNDER_IOPS} IOPS ${UNDER_RUNTIME}s then over ${OVER_RUNTIME}s; mongo=${RUN_MONGO}; max ${MAX_HOURS}h. Soft campaign \$125 / hard \$150."

cat > "$STAGE/PLAN.txt" <<PLAN
EBS ExceededChecks + iostat pairing
region=$REGION type=$TYPE ami=$AMI
subnet=$SUBNET vpc=$VPC
data_volume=gp3 ${GP3_GIB}GiB iops=${GP3_IOPS} throughput=${GP3_TP}
tag=$TAG max_hours=$MAX_HOURS
cost=$COST_NOTE
phases: under(~${UNDER_IOPS} IOPS ${UNDER_RUNTIME}s) then over(unthrottled high QD ${OVER_RUNTIME}s); mongo_after_fio=${RUN_MONGO}
watcher=reuse _aws_ebs_xcheck_watcher.sh with T9C_STAGE=$STAGE
PLAN

echo "Wrote $STAGE/PLAN.txt"
cat "$STAGE/PLAN.txt"

if [ "${CONFIRM_EBS_XCHECK:-0}" != "1" ]; then
  echo "Scaffold only — set CONFIRM_EBS_XCHECK=1 to create EC2 resources."
  exit 0
fi

# Spend gate: refuse if SwampLink actual >= 100 or credits < 50
CREDITS=$(aws freetier get-account-plan-state --query 'accountPlanRemainingCredits.amount' --output text 2>/dev/null || echo 200)
ACTUAL=$(aws budgets describe-budget --account-id 189575358547 --budget-name SwampLink --query 'Budget.CalculatedSpend.ActualSpend.Amount' --output text 2>/dev/null || echo 0)
python - <<PY
credits=float("$CREDITS"); actual=float("$ACTUAL")
print(f"spend_gate credits={credits} swamp_actual={actual}")
if credits < 50:
    raise SystemExit("Refuse: plan credits < \$50")
if actual >= 100:
    raise SystemExit("Refuse: SwampLink actual >= \$100 (soft campaign headroom gone)")
PY

cat > "$STAGE/run_xcheck.sh" <<RUNEOF
#!/usr/bin/env bash
set -euxo pipefail
mkdir -p /opt/xycalc/results
sudo mkdir -p /mnt/gp3
exec > >(tee -a /opt/xycalc/results/run.log) 2>&1

DEV=""
for i in \$(seq 1 30); do
  ROOT_SRC=\$(findmnt -no SOURCE / || true)
  ROOT_DISK=\$(lsblk -no PKNAME "\$ROOT_SRC" 2>/dev/null | head -1 || true)
  if [ -z "\$ROOT_DISK" ]; then
    ROOT_DISK=\$(lsblk -no NAME "\$ROOT_SRC" 2>/dev/null | head -1 || echo nvme0n1)
  fi
  DEV=\$(lsblk -dn -o NAME,TYPE | awk -v r="\$ROOT_DISK" '\$2=="disk" && \$1!=r {print \$1; exit}')
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

# Pull mongo image in the background so it is ready after fio (no-op if unused).
sudo dnf install -y docker >/dev/null 2>&1 || true
sudo systemctl enable --now docker >/dev/null 2>&1 || true
sudo docker pull mongo:7 >/dev/null 2>&1 &
PULL_PID=\$!

TEST=/mnt/gp3/xcheck.bin
dd if=/dev/zero of="\$TEST" bs=1M count=4096 status=none
date -Iseconds | tee /opt/xycalc/results/window_start.txt

# --- Phase UNDER: stay under provisioned ${GP3_IOPS} IOPS ---
date -Iseconds | tee /opt/xycalc/results/phase_under_start.txt
iostat -x 1 "/dev/\$DEV" > /opt/xycalc/results/under.iostat.txt 2>/dev/null &
IOSTAT_PID=\$!
fio --name=under --filename="\$TEST" --direct=1 --rw=randread --bs=4k \\
  --ioengine=libaio --iodepth=16 --rate_iops=${UNDER_IOPS} \\
  --time_based --runtime=${UNDER_RUNTIME} --group_reporting \\
  --write_iops_log=/opt/xycalc/results/under --log_avg_msec=1000 \\
  > /opt/xycalc/results/under.fio.txt 2>&1 || true
kill \$IOSTAT_PID 2>/dev/null || true
wait \$IOSTAT_PID 2>/dev/null || true
date -Iseconds | tee /opt/xycalc/results/phase_under_end.txt

sleep 30

# --- Phase OVER: try to exceed provisioned IOPS ---
date -Iseconds | tee /opt/xycalc/results/phase_over_start.txt
iostat -x 1 "/dev/\$DEV" > /opt/xycalc/results/over.iostat.txt 2>/dev/null &
IOSTAT_PID=\$!
fio --name=over --filename="\$TEST" --direct=1 --rw=randread --bs=4k \\
  --ioengine=libaio --iodepth=64 \\
  --time_based --runtime=${OVER_RUNTIME} --group_reporting \\
  --write_iops_log=/opt/xycalc/results/over --log_avg_msec=1000 \\
  > /opt/xycalc/results/over.fio.txt 2>&1 || true
kill \$IOSTAT_PID 2>/dev/null || true
wait \$IOSTAT_PID 2>/dev/null || true
date -Iseconds | tee /opt/xycalc/results/phase_over_end.txt
date -Iseconds | tee /opt/xycalc/results/window_end.txt

python3 - <<'PY'
import json, pathlib, statistics, re
root = pathlib.Path("/opt/xycalc/results")

def peak_mean_from_fio_log(path):
    # fio *_iops.1.log: time_ms, iops, ...
    vals = []
    p = pathlib.Path(str(path) + "_iops.1.log")
    if not p.exists():
        return None
    for line in p.read_text(errors="ignore").splitlines():
        parts = line.strip().split(",")
        if len(parts) >= 2:
            try:
                vals.append(float(parts[1]))
            except ValueError:
                pass
    if not vals:
        return None
    return {
        "n": len(vals),
        "mean": statistics.mean(vals),
        "peak": max(vals),
        "peak_to_mean": (max(vals) / statistics.mean(vals)) if statistics.mean(vals) else None,
        "p50": statistics.median(vals),
    }

def iostat_r_await_peak(path):
    # crude: last numeric column groups — keep raw path for offline
    text = path.read_text(errors="ignore") if path.exists() else ""
    return {"bytes": len(text), "lines": text.count(chr(10))}

out = {
    "arm": "ebs-exceeded-checks",
    "provisioned_iops": ${GP3_IOPS},
    "provisioned_tp_mibs": ${GP3_TP},
    "under": peak_mean_from_fio_log(root / "under"),
    "over": peak_mean_from_fio_log(root / "over"),
    "under_iostat": iostat_r_await_peak(root / "under.iostat.txt"),
    "over_iostat": iostat_r_await_peak(root / "over.iostat.txt"),
    "window_start": (root / "window_start.txt").read_text().strip() if (root / "window_start.txt").exists() else None,
    "window_end": (root / "window_end.txt").read_text().strip() if (root / "window_end.txt").exists() else None,
    "phase_under_start": (root / "phase_under_start.txt").read_text().strip() if (root / "phase_under_start.txt").exists() else None,
    "phase_under_end": (root / "phase_under_end.txt").read_text().strip() if (root / "phase_under_end.txt").exists() else None,
    "phase_over_start": (root / "phase_over_start.txt").read_text().strip() if (root / "phase_over_start.txt").exists() else None,
    "phase_over_end": (root / "phase_over_end.txt").read_text().strip() if (root / "phase_over_end.txt").exists() else None,
}
(root / "xcheck-summary.json").write_text(json.dumps(out, indent=2))
print(json.dumps(out, indent=2))
PY

if [ "${RUN_MONGO}" = "1" ]; then
  set +e
  date -Iseconds | tee /opt/xycalc/results/phase_mongo_start.txt
  wait \$PULL_PID 2>/dev/null || true
  sudo mkdir -p /mnt/gp3/mongo
  sudo docker rm -f xycalc-mongo 2>/dev/null || true
  sudo docker run -d --name xycalc-mongo --network host \\
    -v /mnt/gp3/mongo:/data/db \\
    mongo:7 --wiredTigerCacheSizeGB 2
  for i in \$(seq 1 60); do
    if sudo docker exec xycalc-mongo mongosh --quiet --eval 'db.runCommand({ping:1})' >/dev/null 2>&1; then
      echo mongo_ready
      break
    fi
    sleep 2
  done
  python3 -m pip install --user pymongo >/dev/null 2>&1 || sudo python3 -m pip install pymongo
  export PROBE_URI=mongodb://127.0.0.1:27017
  export PROBE_MODE=timeseries
  export PROBE_LEVELS=8
  export PROBE_SECONDS=180
  export PROBE_DOCS=400000
  export PROBE_CACHE_GB=2
  export PROBE_MIN_OVERSUB=0.01
  python3 /opt/xycalc/ticket_probe.py > /opt/xycalc/results/ticket_probe.out 2>&1
  sudo docker exec xycalc-mongo mongosh --quiet --eval 'db.serverStatus().mem' \\
    > /opt/xycalc/results/mongo_status.json 2>/dev/null
  date -Iseconds | tee /opt/xycalc/results/phase_mongo_end.txt
  set -e
fi

echo DONE > /opt/xycalc/results/DONE
RUNEOF
python -c "import pathlib; p=pathlib.Path(r'$STAGE_WIN/run_xcheck.sh'); p.write_bytes(p.read_bytes().replace(b'\r\n', b'\n'))"

cat > "$STAGE/userdata.sh" <<'UDEOF'
#!/bin/bash
set -euxo pipefail
exec > /var/log/xycalc-ebs-xcheck-bootstrap.log 2>&1
dnf install -y fio python3 sysstat docker
systemctl enable --now docker || true
touch /var/lib/xycalc-ebs-xcheck-ready
UDEOF
python -c "import pathlib; p=pathlib.Path(r'$STAGE_WIN/userdata.sh'); p.write_bytes(p.read_bytes().replace(b'\r\n', b'\n'))"

aws configure set region "$REGION"
KEY="xycalc-ebs-xcheck-${TAG}"
aws ec2 create-key-pair --region "$REGION" --key-name "$KEY" --query KeyMaterial --output text > "$STAGE/${KEY}.pem"
python -c "import pathlib; p=pathlib.Path(r'$STAGE_WIN/${KEY}.pem'); t=p.read_text(encoding='utf-8', errors='ignore'); p.write_text(t.replace('\r\n','\n'), encoding='ascii', newline='\n')"
chmod 600 "$STAGE/${KEY}.pem"

SG=$(aws ec2 create-security-group --region "$REGION" --group-name "$KEY" \
  --description "xycalc ebs xcheck ephemeral" --vpc-id "$VPC" --query GroupId --output text)
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
    "ResourceType=instance,Tags=[{Key=Name,Value=${TAG}},{Key=xycalc,Value=ebs-xcheck},{Key=KeepUntil,Value=${DAY}}]" \
    "ResourceType=volume,Tags=[{Key=Name,Value=${TAG}-vol},{Key=xycalc,Value=ebs-xcheck}]" \
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

# Capture data volume id for CloudWatch after run
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
  if "${SSH[@]}" 'test -f /var/lib/xycalc-ebs-xcheck-ready'; then
    echo "bootstrap ready"
    break
  fi
  echo "wait bootstrap $i"
  sleep 10
done

scp -i "$STAGE/${KEY}.pem" -o StrictHostKeyChecking=no \
  "$STAGE/run_xcheck.sh" \
  "$ROOT/tools/bench/ticket_probe.py" \
  "$ROOT/tools/bench/mongo_tickets.py" \
  "ec2-user@${IP}:/tmp/"
"${SSH[@]}" 'sudo mkdir -p /opt/xycalc && sudo cp /tmp/run_xcheck.sh /opt/xycalc/run_xcheck.sh && sudo cp /tmp/ticket_probe.py /tmp/mongo_tickets.py /opt/xycalc/ && sudo chown -R ec2-user:ec2-user /opt/xycalc && chmod +x /opt/xycalc/run_xcheck.sh'

"${SSH[@]}" 'mkdir -p /opt/xycalc/results; nohup bash /opt/xycalc/run_xcheck.sh >/opt/xycalc/results/nohup.out 2>&1 & echo $! > /opt/xycalc/results/probe.pid; echo started pid=$(cat /opt/xycalc/results/probe.pid); sleep 3; tail -20 /opt/xycalc/results/run.log 2>/dev/null || tail -20 /opt/xycalc/results/nohup.out; ls -la /opt/xycalc/results/'

echo "ebs-xcheck running on ${IP} (${IID}). Stage: ${STAGE}"
echo "Start watcher: T9C_STAGE=$STAGE bash tools/bench/_aws_ebs_xcheck_watcher.sh"
echo "Teardown: aws ec2 terminate-instances --region ${REGION} --instance-ids ${IID}"
