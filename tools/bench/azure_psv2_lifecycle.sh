#!/usr/bin/env bash
# Azure Premium SSD v2 lifecycle: provision → probe → pull → tear down.
#
# Free-credit only. Smallest Premium-capable SKU by default (Standard_D2s_v5).
# Tag: xycalc-psv2-YYYYMMDD. Under ~1 hour wall clock. Deletes VM+disk+RG after.
#
# Usage (Git Bash on COOPER):
#   ./tools/bench/azure_psv2_lifecycle.sh create
#   ./tools/bench/azure_psv2_lifecycle.sh probe          # all IOPS points
#   ./tools/bench/azure_psv2_lifecycle.sh import         # local/ only
#   ./tools/bench/azure_psv2_lifecycle.sh destroy
#   ./tools/bench/azure_psv2_lifecycle.sh run-all        # create→probe→import→destroy
#
# Env overrides:
#   AZ_LOCATION=westcentralus  AZ_ZONE=1  AZ_VM_SIZE=Standard_D2s_v5
#   AZ_DISK_GIB=64  AZ_SSH_PUBKEY=~/.ssh/id_ed25519.pub
#   AZ_IOPS_POINTS="3000 4000 8000"
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TAG="xycalc-psv2-$(date +%Y%m%d)"
STAGE="${ROOT}/tmp/${TAG}"
RG="${AZ_RG:-rg-${TAG}}"
LOCATION="${AZ_LOCATION:-westcentralus}"
ZONE="${AZ_ZONE:-}"  # empty = nonzonal (free-trial friendly)
VM_SIZE="${AZ_VM_SIZE:-Standard_D2s_v5}"
VM_NAME="${AZ_VM_NAME:-psv2probe}"
DISK_NAME="${AZ_DISK_NAME:-psv2data}"
DISK_GIB="${AZ_DISK_GIB:-64}"
SSH_PUBKEY="${AZ_SSH_PUBKEY:-${HOME}/.ssh/id_ed25519.pub}"
ADMIN_USER="${AZ_ADMIN_USER:-azureuser}"
IOPS_POINTS="${AZ_IOPS_POINTS:-3000 4000 8000}"
# Model: settable_mbps = clamp(0.25 * iops, 125, 2000)
RUNTIME="${PROBE_RUNTIME:-20}"

mkdir -p "$STAGE"

log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

need_az() {
  command -v az >/dev/null 2>&1 || die "az CLI not found"
  az account show >/dev/null 2>&1 || die "az not logged in — run: az login"
}

py() {
  if [[ -x "${ROOT}/.venv/Scripts/python.exe" ]]; then
    "${ROOT}/.venv/Scripts/python.exe" "$@"
  elif [[ -x "${ROOT}/.venv/bin/python" ]]; then
    "${ROOT}/.venv/bin/python" "$@"
  else
    python3 "$@"
  fi
}

model_ceiling_mbps() {
  local iops="$1"
  py -c "iops=int('$iops'); print(min(2000, max(125, int(0.25*iops))))"
}

preflight() {
  need_az
  local state sub_id spend quota
  state="$(az account show --query state -o tsv)"
  sub_id="$(az account show --query id -o tsv)"
  [[ "$state" == "Enabled" ]] || die "subscription state=$state"
  # account show omits policies; pull via ARM
  spend="$(az rest --method get --url "https://management.azure.com/subscriptions/${sub_id}?api-version=2020-01-01" --query subscriptionPolicies.spendingLimit -o tsv 2>/dev/null || true)"
  quota="$(az rest --method get --url "https://management.azure.com/subscriptions/${sub_id}?api-version=2020-01-01" --query subscriptionPolicies.quotaId -o tsv 2>/dev/null || true)"
  log "subscription=$sub_id state=$state spendingLimit=${spend:-?} quotaId=${quota:-?}"
  if [[ "${spend:-}" != "On" && "${quota:-}" != FreeTrial* ]]; then
    log "WARNING: spendingLimit!=On and not FreeTrial ($quota). Abort unless AZ_ALLOW_PAID=1."
    [[ "${AZ_ALLOW_PAID:-0}" == "1" ]] || die "refusing paid path; set AZ_ALLOW_PAID=1 to override"
  fi
  [[ -f "$SSH_PUBKEY" ]] || die "SSH pubkey missing: $SSH_PUBKEY"
  log "preflight ok tag=$TAG rg=$RG loc=$LOCATION zone=$ZONE size=$VM_SIZE"
  # Premium SSD v2: max 4 performance changes / 24h; create counts as one.
  # create at first IOPS point, then (n-1) updates — keep IOPS_POINTS length <= 3.
  local n=0
  for _ in $IOPS_POINTS; do n=$((n + 1)); done
  (( n <= 3 )) || die "IOPS_POINTS has $n entries; PSv2 allows only 3 post-create updates (4 incl. create). Use <=3 points."
}

create() {
  preflight
  log "creating resource group $RG"
  az group create -n "$RG" -l "$LOCATION" \
    --tags "xycalc=${TAG}" "purpose=premium-ssd-v2-probe" "owner=xycalc" >/dev/null

  # Create already at first IOPS point (counts as adjust #1 of 4/24h).
  local first_iops first_mbps
  first_iops="$(echo "$IOPS_POINTS" | awk '{print $1}')"
  first_mbps="$(model_ceiling_mbps "$first_iops")"
  local zone_args=()
  if [ -n "$ZONE" ]; then zone_args=(--zone "$ZONE"); fi
  log "creating Premium SSD v2 disk $DISK_NAME (${DISK_GIB} GiB, zone=${ZONE:-nonzonal}, ${first_iops} IOPS / ${first_mbps} MB/s)"
  az disk create -g "$RG" -n "$DISK_NAME" -l "$LOCATION" "${zone_args[@]}" \
    --sku PremiumV2_LRS --size-gb "$DISK_GIB" \
    --disk-iops-read-write "$first_iops" --disk-mbps-read-write "$first_mbps" \
    --tags "xycalc=${TAG}" >/dev/null
  echo "accepted iops=$first_iops mbps=$first_mbps (at create)" | tee "$STAGE/ceiling-${first_iops}.txt" >&2

  log "creating VM $VM_NAME ($VM_SIZE) Ubuntu 22.04 zone=${ZONE:-nonzonal}"
  az vm create -g "$RG" -n "$VM_NAME" -l "$LOCATION" "${zone_args[@]}" \
    --size "$VM_SIZE" \
    --image Canonical:0001-com-ubuntu-server-jammy:22_04-lts-gen2:latest \
    --admin-username "$ADMIN_USER" \
    --ssh-key-values "$SSH_PUBKEY" \
    --public-ip-sku Standard \
    --nsg-rule SSH \
    --os-disk-size-gb 30 \
    --storage-sku Premium_LRS \
    --tags "xycalc=${TAG}" \
    --output json > "$STAGE/vm-create.json"

  local ip
  ip="$(az vm show -d -g "$RG" -n "$VM_NAME" --query publicIps -o tsv)"
  echo "$ip" > "$STAGE/ip.txt"
  echo "$RG" > "$STAGE/rg.txt"
  echo "$VM_NAME" > "$STAGE/vm.txt"
  echo "$DISK_NAME" > "$STAGE/disk.txt"
  echo "$VM_SIZE" > "$STAGE/vm_size.txt"
  echo "$TAG" > "$STAGE/tag.txt"
  log "VM public IP=$ip — attaching data disk"

  az vm disk attach -g "$RG" --vm-name "$VM_NAME" --name "$DISK_NAME" >/dev/null

  wait_ssh "$ip"
  remote_bootstrap "$ip"
  log "create done. stage=$STAGE"
}

wait_ssh() {
  local ip="$1" i
  log "waiting for SSH on $ip"
  for i in $(seq 1 60); do
    if ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 -o BatchMode=yes \
      "${ADMIN_USER}@${ip}" 'echo ok' >/dev/null 2>&1; then
      log "SSH ready"
      return 0
    fi
    sleep 5
  done
  die "SSH never came up on $ip"
}

ssh_vm() {
  local ip
  ip="$(cat "$STAGE/ip.txt")"
  ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes "${ADMIN_USER}@${ip}" "$@"
}

scp_to() {
  local ip
  ip="$(cat "$STAGE/ip.txt")"
  scp -o StrictHostKeyChecking=accept-new -o BatchMode=yes "$@"
}

remote_bootstrap() {
  local ip="$1"
  log "bootstrap: install fio + mount Premium SSD v2"
  # Find the newly attached managed disk (not OS, not NVMe temp). Prefer by size.
  ssh_vm bash -s <<'REMOTE'
set -euo pipefail
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq fio python3
# Prefer SCSI managed disk; exclude root and nvme temp.
DEV=""
for d in /dev/sdc /dev/sdd /dev/sdb; do
  if [ -b "$d" ]; then
    # skip if already mounted as root
    mp=$(lsblk -no MOUNTPOINT "$d" 2>/dev/null | head -1 || true)
    [ "$mp" = "/" ] && continue
    DEV="$d"
    break
  fi
done
if [ -z "$DEV" ]; then
  # fallback: largest unmounted disk
  DEV=$(lsblk -dn -o NAME,TYPE,SIZE,MOUNTPOINT | awk '$2=="disk" && $4=="" {print "/dev/"$1}' | tail -1)
fi
[ -n "$DEV" ] && [ -b "$DEV" ] || { echo "no data disk found"; lsblk; exit 1; }
echo "using device $DEV"
sudo mkfs.ext4 -F "$DEV"
sudo mkdir -p /mnt/psv2
sudo mount "$DEV" /mnt/psv2
sudo chown "$(id -un):$(id -gn)" /mnt/psv2
echo "$DEV" | sudo tee /mnt/psv2/DEVICE >/dev/null
lsblk -o NAME,ROTA,TRAN,SIZE,MODEL,MOUNTPOINT "$DEV"
df -h /mnt/psv2
REMOTE

  # Push probe harness
  ssh_vm 'mkdir -p ~/xycalc/tools/bench'
  scp -o StrictHostKeyChecking=accept-new -o BatchMode=yes \
    "$ROOT/tools/bench/azure_premium_v2_probe.sh" \
    "$ROOT/tools/bench/azure_premium_v2_probe.py" \
    "${ADMIN_USER}@${ip}:~/xycalc/tools/bench/"
  ssh_vm 'chmod +x ~/xycalc/tools/bench/azure_premium_v2_probe.sh'
  # Strip CRLF if pushed from Windows
  ssh_vm "sed -i 's/\r$//' ~/xycalc/tools/bench/azure_premium_v2_probe.sh"
}

set_disk_perf() {
  local iops="$1" mbps
  mbps="$(model_ceiling_mbps "$iops")"
  log "az disk update iops=$iops mbps=$mbps (model ceiling)"
  # Capture acceptance/rejection — rejection is itself a measurement.
  if az disk update -g "$RG" -n "$DISK_NAME" \
      --disk-iops-read-write "$iops" --disk-mbps-read-write "$mbps" \
      --output json > "$STAGE/disk-update-${iops}.json" 2>"$STAGE/disk-update-${iops}.err"; then
    echo "accepted iops=$iops mbps=$mbps" | tee "$STAGE/ceiling-${iops}.txt" >&2
  else
    log "REJECTED iops=$iops mbps=$mbps — recording and seeking highest accepted mbps"
    echo "rejected iops=$iops mbps=$mbps" | tee "$STAGE/ceiling-${iops}.txt" >&2
    cat "$STAGE/disk-update-${iops}.err" >&2 || true
    # Binary-ish fall back: try documented alternate caps (1200 pricing-page tension).
    for try in 1200 1000 750 500 250 125; do
      if (( try * 4 > iops )); then continue; fi  # above 0.25*iops slope
      if az disk update -g "$RG" -n "$DISK_NAME" \
          --disk-iops-read-write "$iops" --disk-mbps-read-write "$try" \
          --output json > "$STAGE/disk-update-${iops}-fallback.json" 2>/dev/null; then
        echo "accepted-fallback iops=$iops mbps=$try" | tee "$STAGE/ceiling-${iops}.txt" >&2
        return 0
      fi
    done
    die "could not set any throughput for iops=$iops"
  fi
}

probe_one() {
  local iops="$1" skip_update="${2:-0}" ip device
  ip="$(cat "$STAGE/ip.txt")"
  if [[ "$skip_update" != "1" ]]; then
    set_disk_perf "$iops"
    sleep 15
  else
    log "skipping disk update for iops=$iops (already set at create)"
  fi
  device="$(ssh_vm 'cat /mnt/psv2/DEVICE')"
  log "running fio probe on $device @ ${iops} IOPS (runtime=${RUNTIME}s)"
  ssh_vm bash -s <<REMOTE
set -euo pipefail
export PROBE_RG='$RG'
export PROBE_DISK='$DISK_NAME'
export PROBE_DEVICE='$device'
export PROBE_TESTFILE=/mnt/psv2/fio-${iops}.bin
export PROBE_RUNTIME='$RUNTIME'
export PROBE_IODEPTH=32
cd ~/xycalc
./tools/bench/azure_premium_v2_probe.sh > ~/probe-${iops}.json
wc -c ~/probe-${iops}.json
REMOTE
  scp -o StrictHostKeyChecking=accept-new -o BatchMode=yes \
    "${ADMIN_USER}@${ip}:~/probe-${iops}.json" "$STAGE/probe-${iops}.json"
  log "pulled $STAGE/probe-${iops}.json"
}

probe() {
  need_az
  [[ -f "$STAGE/ip.txt" ]] || die "no stage at $STAGE — run create first"
  RG="$(cat "$STAGE/rg.txt")"
  DISK_NAME="$(cat "$STAGE/disk.txt")"
  local first=1
  for iops in $IOPS_POINTS; do
    if [[ "$first" == "1" ]]; then
      probe_one "$iops" 1
      first=0
    else
      probe_one "$iops" 0
    fi
  done
  log "all probes done"
}

import_local() {
  local size when stem
  size="$(cat "$STAGE/vm_size.txt" 2>/dev/null || echo "$VM_SIZE")"
  when="$(date +%Y-%m-%d)"
  local py="${ROOT}/.venv/Scripts/python.exe"
  [[ -x "$py" ]] || py="${ROOT}/.venv/bin/python"
  [[ -x "$py" ]] || py="python3"
  for f in "$STAGE"/probe-*.json; do
    [[ -f "$f" ]] || continue
    stem="azure-psv2-$(basename "$f" .json | sed 's/probe-//')-${when}"
    log "import $f → local/ (tag=$stem)"
    "$py" "$ROOT/tools/import_azure_probe.py" "$f" \
      --machine-class "$size" \
      --tag "$stem" \
      --observed-on "$when"
  done
  log "import complete — review local/ then --publish if clean. Do NOT write into ebs.*"
}

destroy() {
  need_az
  local rg="${1:-}"
  if [[ -z "$rg" && -f "$STAGE/rg.txt" ]]; then
    rg="$(cat "$STAGE/rg.txt")"
  fi
  rg="${rg:-$RG}"
  log "DESTROY resource group $rg (VM+disk+NIC+PIP+NSG)"
  az group delete -n "$rg" --yes --no-wait
  log "delete requested (async). Confirm with: az group exists -n $rg && az vm list -o table && az disk list -o table"
}

confirm_zero() {
  need_az
  log "leftover check:"
  az group list --query "[?tags.xycalc!=null || contains(name,'xycalc-psv2')].{name:name,loc:location,tags:tags}" -o table || true
  az vm list --query "[?tags.xycalc!=null].{name:name,rg:resourceGroup,size:hardwareProfile.vmSize}" -o table || true
  az disk list --query "[?tags.xycalc!=null || contains(name,'psv2')].{name:name,rg:resourceGroup,sku:sku.name}" -o table || true
}

run_all() {
  create
  probe
  import_local
  destroy
  sleep 30
  confirm_zero
  log "run-all finished. Review local/ YAML before publish."
}

cmd="${1:-}"
case "$cmd" in
  preflight) preflight ;;
  create) create ;;
  probe) probe ;;
  import) import_local ;;
  destroy) destroy "${2:-}" ;;
  confirm-zero) confirm_zero ;;
  run-all) run_all ;;
  *)
    cat <<EOF
Usage: $0 {preflight|create|probe|import|destroy|confirm-zero|run-all}
Stage dir: $STAGE
EOF
    exit 2
    ;;
esac
