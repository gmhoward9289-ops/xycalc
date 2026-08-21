#!/usr/bin/env bash
# EBS burst-factor probe — issue #4. Measures peak-second vs mean-minute IOPS.
#
#   sudo ./tools/bench/burst_probe.sh > burst.json
#   PROBE_SMOKE=1 sudo ./tools/bench/burst_probe.sh > smoke.json   # short, for wiring
#
# Then import the (already analysed) JSON:
#   python tools/import_burst_probe.py burst.json --machine-class m6i.large
#
# What it does, and why this shape (see docs/plans/issue-4-ebs-burst-factor-iostat.md):
#
#   * It measures on a DEDICATED loop device with --direct-io=on, not the root
#     disk, so another process on the host cannot contaminate the peak, and the
#     host page cache cannot serve the I/O and turn a writeback-timer artifact
#     into a fake burst.
#   * Run 0 (control) is rate-limited to a constant 200 IOPS and MUST come back
#     at ratio ~1.0. It runs first and gates the rest — if the control is wrong,
#     the analysis pipeline is broken and nothing else is trustworthy.
#   * Shape A (steady batch) and Shape B (Poisson-arrival bursty) are the two
#     workload shapes the coefficient's band is meant to span.
#
# This does NOT change ebs.peak-to-mean-iops-ratio. Four runs on one host is not
# the population; the importer records observations and leaves the coefficient
# (and its `estimate` grade) exactly as they are. Do not narrow the band on one
# machine's numbers.
#
# Needs: root (for losetup), fio, sysstat (iostat), ~20 GiB scratch. Runs on a
# small instance — no EBS bandwidth is exercised, the loop device is local.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"

if [ "$(id -u)" -ne 0 ]; then
  echo "must run as root (losetup). Re-run with sudo." >&2
  exit 1
fi
for tool in fio losetup fallocate; do
  command -v "$tool" >/dev/null 2>&1 || { echo "missing $tool" >&2; exit 3; }
done

SIZE_GB="${PROBE_SIZE_GB:-20}"
IMG="${PROBE_IMG:-/var/tmp/xycalc-burst-probe.img}"
OUT="${PROBE_OUT:-$(mktemp -d /tmp/burst-probe.XXXXXX)}"

if [ "${PROBE_SMOKE:-0}" = "1" ]; then
  CONTROL_RUNTIME="${PROBE_CONTROL_RUNTIME:-12}"
  RUNTIME="${PROBE_RUNTIME:-24}"
else
  CONTROL_RUNTIME="${PROBE_CONTROL_RUNTIME:-300}"
  RUNTIME="${PROBE_RUNTIME:-900}"
fi

PY="${PROBE_PYTHON:-python3}"

cleanup() {
  [ -n "${LOOP:-}" ] && losetup -d "$LOOP" 2>/dev/null || true
  [ "${PROBE_KEEP_IMG:-0}" = "1" ] || rm -f "$IMG" 2>/dev/null || true
}
trap cleanup EXIT

echo "allocating ${SIZE_GB} GiB scratch at $IMG ..." >&2
fallocate -l "${SIZE_GB}G" "$IMG"
# --direct-io=on is load-bearing: without it the loop device is served from the
# host page cache backing $IMG — the exact trap that voided two earlier runs.
LOOP="$(losetup --find --show --direct-io=on "$IMG")"
echo "loop device $LOOP (direct-io on)" >&2

# fio common: 1-second IOPS samples, direct I/O, group reporting.
fio_run() {
  local name="$1" ; shift
  echo "=== fio $name ===" >&2
  # iostat cross-check for the same window, in the background (advisory artifact).
  if command -v iostat >/dev/null 2>&1; then
    iostat -x 1 > "$OUT/${name}.iostat.txt" 2>/dev/null &
    local iostat_pid=$!
  fi
  fio --name="$name" --filename="$LOOP" --direct=1 --ioengine=libaio \
      --group_reporting --time_based \
      --write_iops_log="$OUT/${name}" --log_avg_msec=1000 \
      "$@" >"$OUT/${name}.fio.txt" 2>&1 || { echo "fio $name failed" >&2; cat "$OUT/${name}.fio.txt" >&2; exit 4; }
  [ -n "${iostat_pid:-}" ] && kill "$iostat_pid" 2>/dev/null || true
}

# Run 0 — control: constant 200 IOPS. Its ratio must come back ~1.0.
fio_run control --rw=randread --bs=4k --iodepth=4 --rate_iops=200 --runtime="$CONTROL_RUNTIME"

# Run 1 — Shape A: steady batch (sequential write, flat out).
fio_run batch --rw=write --bs=1m --iodepth=8 --runtime="$RUNTIME"

# Run 2 — Shape B: request-driven bursty (Poisson arrivals under the ceiling).
fio_run bursty --rw=randread --bs=4k --iodepth=8 --rate_iops=400 \
  --rate_process=poisson --runtime="$RUNTIME"

# Not `exec`: the cleanup trap (losetup -d + rm of the scratch image) must run on
# exit, and exec would replace this shell before it could fire, leaking the loop
# device and a 20 GiB file.
echo "analysing ..." >&2
"$PY" "$here/burst_probe_analyze.py" \
  "control=$OUT/control_iops.1.log" \
  "batch=$OUT/batch_iops.1.log" \
  "bursty=$OUT/bursty_iops.1.log" \
  --machine "${PROBE_MACHINE:-$(uname -n)}"
exit $?
