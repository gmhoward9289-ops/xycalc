#!/usr/bin/env bash
# Azure Premium SSD v2 delivery probe — validates azure.premium-v2-throughput-ceiling.
#
# Run this ON an Azure VM that has a Premium SSD v2 data disk attached, formatted,
# and mounted. It reads the disk's provisioned performance from the Azure control
# plane (`az disk show`) and measures what the disk actually delivers with fio,
# then prints one JSON document that tools/import_azure_probe.py turns into a
# corpus observation and a validation case.
#
#   # 1. Provision a Premium SSD v2 disk, set its throughput to the MAX Azure
#   #    allows for your chosen IOPS (that maximum is exactly what the model
#   #    predicts, and what the validation case checks):
#   az disk update -g "$RG" -n "$DISK" --disk-iops-read-write 8000 --disk-mbps-read-write 2000
#
#   # 2. Attach, format, and mount it on the VM (a fresh data disk, NOT the OS
#   #    disk and NOT the local NVMe temp disk):
#   sudo mkfs.ext4 /dev/sdc && sudo mkdir -p /mnt/psv2 && sudo mount /dev/sdc /mnt/psv2
#
#   # 3. Run the probe:
#   PROBE_RG="$RG" PROBE_DISK="$DISK" PROBE_DEVICE=/dev/sdc \
#     PROBE_TESTFILE=/mnt/psv2/fio.bin \
#     ./tools/bench/azure_premium_v2_probe.sh > probe.json
#
#   # 4. Import (writes to local/ unless --publish):
#   python tools/import_azure_probe.py probe.json --machine-class "Standard_D8s_v5"
#
# If `az` is not on this box, skip PROBE_RG/PROBE_DISK and pass the config that
# `az disk show` would have reported directly:
#   PROBE_PROVISIONED_IOPS=8000 PROBE_SETTABLE_MBPS=2000 PROBE_DISK_SIZE_GIB=256 ...
#
# WARNING: fio writes to PROBE_TESTFILE. Point it at scratch space on the disk
# under test, never at data you want to keep.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"

DEVICE="${PROBE_DEVICE:?set PROBE_DEVICE to the Premium SSD v2 block device, e.g. /dev/sdc}"
TESTFILE="${PROBE_TESTFILE:?set PROBE_TESTFILE to a scratch path on the mounted disk, e.g. /mnt/psv2/fio.bin}"
RUNTIME="${PROBE_RUNTIME:-30}"
IODEPTH="${PROBE_IODEPTH:-32}"
SEQ_BS="${PROBE_SEQ_BS_KIB:-256}"
RAND_BS="${PROBE_RAND_BS_KIB:-4}"

if [ ! -b "$DEVICE" ]; then
  echo "not a block device: $DEVICE" >&2
  exit 1
fi

# --- read the provisioned config from the Azure control plane, or from env ----
IOPS="${PROBE_PROVISIONED_IOPS:-}"
MBPS="${PROBE_SETTABLE_MBPS:-}"
SIZE_GIB="${PROBE_DISK_SIZE_GIB:-}"

if [ -z "$IOPS" ] || [ -z "$MBPS" ]; then
  if command -v az >/dev/null 2>&1 && [ -n "${PROBE_DISK:-}" ] && [ -n "${PROBE_RG:-}" ]; then
    echo "reading disk config from az disk show ${PROBE_DISK}..." >&2
    IOPS="$(az disk show -g "$PROBE_RG" -n "$PROBE_DISK" --query diskIOPSReadWrite -o tsv)"
    MBPS="$(az disk show -g "$PROBE_RG" -n "$PROBE_DISK" --query diskMBpsReadWrite -o tsv)"
    SIZE_GIB="$(az disk show -g "$PROBE_RG" -n "$PROBE_DISK" --query diskSizeGB -o tsv)"
  else
    echo "error: need the disk's provisioned config. Either set PROBE_RG + PROBE_DISK" >&2
    echo "so 'az disk show' can read it, or pass PROBE_PROVISIONED_IOPS and" >&2
    echo "PROBE_SETTABLE_MBPS (and optionally PROBE_DISK_SIZE_GIB) directly." >&2
    exit 2
  fi
fi

echo "device=$DEVICE provisioned_iops=$IOPS settable_mbps=$MBPS size_gib=${SIZE_GIB:-?}" >&2
lsblk -o NAME,ROTA,TRAN,SIZE,MODEL,MOUNTPOINT "$DEVICE" >&2 || true

if ! command -v fio >/dev/null 2>&1; then
  echo "fio not found. Install it first, e.g.:  sudo apt-get update && sudo apt-get install -y fio" >&2
  exit 3
fi

PY="${PROBE_PYTHON:-python3}"

exec "$PY" "$here/azure_premium_v2_probe.py" \
  --test-file "$TESTFILE" \
  --device "$DEVICE" \
  --provisioned-iops "$IOPS" \
  --settable-mbps "$MBPS" \
  ${SIZE_GIB:+--disk-size-gib "$SIZE_GIB"} \
  --seq-bs-kib "$SEQ_BS" \
  --rand-bs-kib "$RAND_BS" \
  --runtime "$RUNTIME" \
  --iodepth "$IODEPTH"
