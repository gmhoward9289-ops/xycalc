#!/usr/bin/env bash
# Arm 2 — Instance EBS ceiling vs volume ceiling.
#
# Skinny Nitro (t3.medium: max ~11800 IOPS / ~261 MiB/s) + fat gp3 (16000/1000)
# so InstanceEBSIOPSExceededCheck can fire while VolumeIOPSExceededCheck stays 0
# (or document what actually happens).
#
#   CONFIRM_EBS_INSTCEIL=1 bash tools/bench/_aws_ebs_instceil_launch.sh
# Soft ≤ \$15 this arm; max 2h. Campaign soft \$125 / hard \$150.
set -euo pipefail

REGION="${AWS_REGION:-us-east-2}"
AMI="${EBS_INSTCEIL_AMI:-ami-06475e8f54266e38e}"
SUBNET="${EBS_INSTCEIL_SUBNET:-subnet-075ae18eae38d8b80}"
VPC="${EBS_INSTCEIL_VPC:-vpc-0a6756ac9903ecdcf}"
TYPE="${EBS_INSTCEIL_TYPE:-t3.medium}"
GP3_GIB="${EBS_INSTCEIL_GP3_GIB:-100}"
GP3_IOPS="${EBS_INSTCEIL_GP3_IOPS:-16000}"
GP3_TP="${EBS_INSTCEIL_GP3_TP:-1000}"
# Documented instance EBS max (from describe-instance-types); override if TYPE changes
INST_MAX_IOPS="${EBS_INSTCEIL_INST_MAX_IOPS:-11800}"
INST_MAX_TP_MIB="${EBS_INSTCEIL_INST_MAX_TP_MIB:-261}"
MAX_HOURS="${EBS_INSTCEIL_MAX_HOURS:-2}"
DAY="$(date +%Y%m%d)"
TAG="xycalc-ebs-instceil-${DAY}"
ROOT_WIN='C:/Users/gmhow/dev/xycalc'
ROOT=/c/Users/gmhow/dev/xycalc
STAGE_WIN="${ROOT_WIN}/tmp/${TAG}"
STAGE="$ROOT/tmp/${TAG}"
mkdir -p "$STAGE"

COST_NOTE="${TYPE} us-east-2 ~\$0.04/hr + gp3 ${GP3_GIB}GiB @ ${GP3_IOPS}/${GP3_TP}; max ${MAX_HOURS}h soft ≤\$15. Campaign soft \$125 / hard \$150."

cat > "$STAGE/PLAN.txt" <<PLAN
Instance EBS ceiling vs volume ceiling
region=$REGION type=$TYPE ami=$AMI
subnet=$SUBNET vpc=$VPC
data_volume=gp3 ${GP3_GIB}GiB iops=${GP3_IOPS} throughput=${GP3_TP}
instance_ebs_max_iops=${INST_MAX_IOPS} instance_ebs_max_tp_mib=${INST_MAX_TP_MIB}
hypothesis: drive > instance max IOPS but < volume provisioned → InstanceEBSIOPSExceededCheck>0, VolumeIOPSExceededCheck=0
tag=$TAG max_hours=$MAX_HOURS
cost=$COST_NOTE
phases: drive_hard(QD64 randread 16k ~6m) then optional tp probe
watcher=reuse _aws_t9c_watcher.sh with T9C_STAGE=$STAGE
PLAN

echo "Wrote $STAGE/PLAN.txt"
cat "$STAGE/PLAN.txt"

if [ "${CONFIRM_EBS_INSTCEIL:-0}" != "1" ]; then
  echo "Scaffold only — set CONFIRM_EBS_INSTCEIL=1 to create EC2 resources."
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

cat > "$STAGE/run_instceil.sh" <<RUNEOF
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

# Capture instance metadata for CW pairing
curl -s -H "X-aws-ec2-metadata-token: \$(curl -s -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')" \
  http://169.254.169.254/latest/meta-data/instance-id | tee /opt/xycalc/results/instance_id.txt || true
echo "${TYPE}" | tee /opt/xycalc/results/instance_type.txt
echo "${INST_MAX_IOPS}" | tee /opt/xycalc/results/inst_max_iops.txt
echo "${GP3_IOPS}" | tee /opt/xycalc/results/vol_provisioned_iops.txt

TEST=/mnt/gp3/instceil.bin
# 8 GiB file — enough for random 16k working set
dd if=/dev/zero of="\$TEST" bs=1M count=8192 status=none
sync
date -Iseconds | tee /opt/xycalc/results/window_start.txt

# --- Phase DRIVE: uncapped high QD — aim past instance IOPS pipe ---
date -Iseconds | tee /opt/xycalc/results/phase_drive_start.txt
iostat -x 1 "/dev/\$DEV" > /opt/xycalc/results/drive.iostat.txt 2>/dev/null &
IOSTAT_PID=\$!
fio --name=drive --filename="\$TEST" --direct=1 --rw=randread --bs=16k \\
  --ioengine=libaio --iodepth=64 --numjobs=2 \\
  --time_based --runtime=360 --group_reporting \\
  --write_iops_log=/opt/xycalc/results/drive --log_avg_msec=1000 \\
  > /opt/xycalc/results/drive.fio.txt 2>&1 || true
kill \$IOSTAT_PID 2>/dev/null || true
wait \$IOSTAT_PID 2>/dev/null || true
date -Iseconds | tee /opt/xycalc/results/phase_drive_end.txt

sleep 20

# --- Phase TP: large-block sequential to stress instance throughput ceiling ---
date -Iseconds | tee /opt/xycalc/results/phase_tp_start.txt
iostat -x 1 "/dev/\$DEV" > /opt/xycalc/results/tp.iostat.txt 2>/dev/null &
IOSTAT_PID=\$!
fio --name=tp --filename="\$TEST" --direct=1 --rw=read --bs=1M \\
  --ioengine=libaio --iodepth=32 --numjobs=2 \\
  --time_based --runtime=120 --group_reporting \\
  --write_bw_log=/opt/xycalc/results/tp --log_avg_msec=1000 \\
  > /opt/xycalc/results/tp.fio.txt 2>&1 || true
kill \$IOSTAT_PID 2>/dev/null || true
wait \$IOSTAT_PID 2>/dev/null || true
date -Iseconds | tee /opt/xycalc/results/phase_tp_end.txt
date -Iseconds | tee /opt/xycalc/results/window_end.txt

python3 - <<'PY'
import json, pathlib, statistics
root = pathlib.Path("/opt/xycalc/results")

def peak_mean_from_fio_log(path, kind="iops"):
    suffix = "_iops.1.log" if kind == "iops" else "_bw.1.log"
    vals = []
    p = pathlib.Path(str(path) + suffix)
    if not p.exists():
        # numjobs>1 may produce .1 / .2 — merge
        for alt in sorted(pathlib.Path(".").glob(str(path) + ("_iops" if kind == "iops" else "_bw") + ".*.log")):
            pass
        parent = p.parent
        stem = path.name
        pattern = f"{stem}_{'iops' if kind=='iops' else 'bw'}.*.log"
        for alt in sorted(parent.glob(pattern)):
            for line in alt.read_text(errors="ignore").splitlines():
                parts = line.strip().split(",")
                if len(parts) >= 2:
                    try:
                        vals.append(float(parts[1]))
                    except ValueError:
                        pass
        if not vals:
            return None
    else:
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

def iostat_bytes(path):
    text = path.read_text(errors="ignore") if path.exists() else ""
    return {"bytes": len(text), "lines": text.count(chr(10))}

out = {
    "arm": "ebs-instance-ceiling",
    "instance_type": "${TYPE}",
    "instance_max_iops_doc": ${INST_MAX_IOPS},
    "instance_max_tp_mib_doc": ${INST_MAX_TP_MIB},
    "provisioned_iops": ${GP3_IOPS},
    "provisioned_tp_mibs": ${GP3_TP},
    "drive": peak_mean_from_fio_log(root / "drive", "iops"),
    "tp_bw_kib": peak_mean_from_fio_log(root / "tp", "bw"),
    "drive_iostat": iostat_bytes(root / "drive.iostat.txt"),
    "tp_iostat": iostat_bytes(root / "tp.iostat.txt"),
    "window_start": (root / "window_start.txt").read_text().strip() if (root / "window_start.txt").exists() else None,
    "window_end": (root / "window_end.txt").read_text().strip() if (root / "window_end.txt").exists() else None,
    "phase_drive_start": (root / "phase_drive_start.txt").read_text().strip() if (root / "phase_drive_start.txt").exists() else None,
    "phase_drive_end": (root / "phase_drive_end.txt").read_text().strip() if (root / "phase_drive_end.txt").exists() else None,
    "phase_tp_start": (root / "phase_tp_start.txt").read_text().strip() if (root / "phase_tp_start.txt").exists() else None,
    "phase_tp_end": (root / "phase_tp_end.txt").read_text().strip() if (root / "phase_tp_end.txt").exists() else None,
}
(root / "instceil-summary.json").write_text(json.dumps(out, indent=2))
print(json.dumps(out, indent=2))
PY

echo DONE > /opt/xycalc/results/DONE
RUNEOF
python -c "import pathlib; p=pathlib.Path(r'$STAGE_WIN/run_instceil.sh'); p.write_bytes(p.read_bytes().replace(b'\r\n', b'\n'))"

cat > "$STAGE/userdata.sh" <<'UDEOF'
#!/bin/bash
set -euxo pipefail
exec > /var/log/xycalc-ebs-instceil-bootstrap.log 2>&1
dnf install -y fio python3 sysstat
touch /var/lib/xycalc-ebs-instceil-ready
UDEOF
python -c "import pathlib; p=pathlib.Path(r'$STAGE_WIN/userdata.sh'); p.write_bytes(p.read_bytes().replace(b'\r\n', b'\n'))"

aws configure set region "$REGION"
KEY="xycalc-ebs-instceil-${TAG}"
aws ec2 create-key-pair --region "$REGION" --key-name "$KEY" --query KeyMaterial --output text > "$STAGE/${KEY}.pem"
python -c "import pathlib; p=pathlib.Path(r'$STAGE_WIN/${KEY}.pem'); t=p.read_text(encoding='utf-8', errors='ignore'); p.write_text(t.replace('\r\n','\n'), encoding='ascii', newline='\n')"
chmod 600 "$STAGE/${KEY}.pem"

SG=$(aws ec2 create-security-group --region "$REGION" --group-name "$KEY" \
  --description "xycalc ebs instceil ephemeral" --vpc-id "$VPC" --query GroupId --output text)
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
  --ebs-optimized \
  --key-name "$KEY" \
  --security-group-ids "$SG" \
  --subnet-id "$SUBNET" \
  --associate-public-ip-address \
  --block-device-mappings "$BDM" \
  --user-data "fileb://${UD_WIN}" \
  --tag-specifications \
    "ResourceType=instance,Tags=[{Key=Name,Value=${TAG}},{Key=xycalc,Value=ebs-instceil},{Key=KeepUntil,Value=${DAY}}]" \
    "ResourceType=volume,Tags=[{Key=Name,Value=${TAG}-vol},{Key=xycalc,Value=ebs-instceil}]" \
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
  if "${SSH[@]}" 'test -f /var/lib/xycalc-ebs-instceil-ready'; then
    echo "bootstrap ready"
    break
  fi
  echo "wait bootstrap $i"
  sleep 10
done

scp -i "$STAGE/${KEY}.pem" -o StrictHostKeyChecking=no \
  "$STAGE/run_instceil.sh" "ec2-user@${IP}:/tmp/"
"${SSH[@]}" 'sudo mkdir -p /opt/xycalc && sudo cp /tmp/run_instceil.sh /opt/xycalc/run_instceil.sh && sudo chown -R ec2-user:ec2-user /opt/xycalc && chmod +x /opt/xycalc/run_instceil.sh'

"${SSH[@]}" 'mkdir -p /opt/xycalc/results; nohup bash /opt/xycalc/run_instceil.sh >/opt/xycalc/results/nohup.out 2>&1 & echo $! > /opt/xycalc/results/probe.pid; echo started pid=$(cat /opt/xycalc/results/probe.pid); sleep 3; tail -20 /opt/xycalc/results/run.log 2>/dev/null || tail -20 /opt/xycalc/results/nohup.out; ls -la /opt/xycalc/results/'

echo "ebs-instceil running on ${IP} (${IID}). Stage: ${STAGE}"
echo "Start watcher: T9C_STAGE=$STAGE bash tools/bench/_aws_t9c_watcher.sh"
echo "Teardown: aws ec2 terminate-instances --region ${REGION} --instance-ids ${IID}"
