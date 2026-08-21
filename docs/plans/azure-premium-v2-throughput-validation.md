# Plan — validate azure.premium-v2-throughput-ceiling against a real disk

## 1. The question

`azure.premium-v2-throughput-ceiling` is documented-but-unvalidated: it
reproduces Azure's own worked examples by construction (3,000 IOPS → 750 MB/s,
4,000 → 1,000, 8,000 → 2,000), which proves the arithmetic matches the doc, not
that a running Azure disk behaves the way the doc says. This plan moves it off
`n=0` with a measurement from a live Premium SSD v2 disk. It runs on free Azure
credits: a small VM plus one Premium SSD v2 data disk for under an hour.

## 2. What would falsify it

Two distinct claims, deliberately not averaged together:

- **The control-plane ceiling.** The model predicts the maximum throughput you
  can *set* for a given provisioned IOPS. Falsified if Azure accepts a
  `disk-mbps-read-write` that the model says it should reject, or rejects one it
  says should be allowed — i.e. the live control plane does not enforce
  0.25 MB/s per IOPS / the 125 floor / the 2,000 cap as documented.
- **Delivery (a separate finding, not this model).** Whether the disk actually
  *sustains* its provisioned throughput under load. The model does not predict
  this, so a shortfall is not a failure of the ceiling model — it is a finding
  about the "provisioned 99.9% of the time" claim, recorded as its own
  observation.

The pricing-page vs technical-reference tension (1,200 vs 2,000 MB/s max) is
directly testable here: provision 8,000 IOPS and try to set 2,000 MB/s. Whether
Azure accepts it settles which page is right for that region and disk.

## 3. Method

Harness: `tools/bench/azure_premium_v2_probe.sh` + `azure_premium_v2_probe.py`.

1. Provision a Premium SSD v2 disk and set its throughput to the maximum Azure
   allows for the chosen IOPS (that maximum is exactly the model's prediction):
   `az disk update -g $RG -n $DISK --disk-iops-read-write 8000 --disk-mbps-read-write 2000`.
   If Azure rejects the value, that rejection is itself the measurement — record
   the highest it *does* accept.
2. Attach, format, and mount the disk on the VM (a fresh data disk — never the
   OS disk, never the local NVMe temp disk).
3. Run the probe. It reads the accepted config back from `az disk show`
   (`diskIOPSReadWrite`, `diskMBpsReadWrite`) and runs fio `--direct=1`:
   large-block sequential for delivered MB/s, small-block random for delivered
   IOPS.
4. Import: `python tools/import_azure_probe.py probe.json --machine-class <VM size>`.
   Review the rows in `local/`, then rerun with `--publish` if the VM/disk
   details are fine to publish.
5. Repeat at a few IOPS points (e.g. 3,000 / 4,000 / 8,000) so the validation
   count climbs past `n=1` and the 0.25-per-IOPS slope is exercised, not just
   one point on it.

## 4. The guard

**What would this print if the thing being measured never happened?**

- **Wrong device.** The single most likely failure: fio hits the VM's local
  NVMe temp disk (often `/dev/sdb`, ephemeral, very fast) instead of the
  Premium SSD v2. The probe checks delivered throughput against the settable
  ceiling and flags any reading materially above it, because a managed disk
  cannot deliver more than it was allowed to be set to; the importer then
  refuses to write a validation case. Confirm `--device` and the mount.
- **Page cache instead of disk.** `--direct=1` on every job; a run whose
  O_DIRECT did not engage is rejected rather than reported as disk speed.
- **Queue too shallow.** A collapsed queue depth turns a throughput
  measurement into a latency one; the probe rejects the run.
- **Unit confusion.** fio reports KiB/s; the probe converts to decimal MB/s
  (÷1000 after ×1024 from KiB), the unit Azure and the model use. Delivery and
  ceiling are kept in separate parameters so "what I got" is never compared
  against a model of "what I was allowed to set".

## 5. What lands in the corpus

Written to `local/` first, `data/` only after a human reviews the numbers:

- `data/sources/azure-psv2-<vm>-<date>.yaml` — `source_type: benchmark`, naming
  `tools/bench/azure_premium_v2_probe.sh` so the run is reproducible.
- `data/observations/...` — delivered throughput (`io.throughput_mbps`) and
  delivered IOPS (`io.iops`), the data-plane reality.
- `data/validation/...` — one case per IOPS point against
  `azure.premium-v2-throughput-ceiling`, `actual` = the ceiling Azure enforced.

The coefficients themselves are unchanged: this validates the model, it does not
re-derive the vendor's constants.

## 6. Effort and dependencies

- Free Azure credits cover it; on-demand cost is a few cents to a dollar for a
  small VM + one Premium SSD v2 disk for under an hour (see also the AWS cost
  note for the sibling EBS probe).
- Needs: an Azure VM, `az` logged in (or pass the config via env), `fio`, and a
  fresh mounted Premium SSD v2 data disk.
- Blocks nothing; unblocks the model's first real validation.
