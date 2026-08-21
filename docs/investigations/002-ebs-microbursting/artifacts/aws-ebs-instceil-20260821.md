# Arm 2 — Instance EBS ceiling vs volume ceiling (2026-08-21)

**Status:** DONE. Guest + `InstanceEBSIOPSExceededCheck` land; volume ExceededChecks empty (same quirk as Arm 1).

| | |
|---|---|
| Instance | `i-02f1aae93a27252fe` (terminated) |
| Volume | `vol-0cc877f344bd250bd` (deleted) — gp3 **16000 IOPS / 1000 MiB/s** |
| Host | `t3.medium` (doc max **11800 IOPS / ~261 MiB/s**), us-east-2 |
| Stage | `tmp/xycalc-ebs-instceil-20260821/` |
| Harness | `tools/bench/_aws_ebs_instceil_launch.sh` |

## Result

fio drive phase held **11.8k IOPS** (instance pipe). CloudWatch `InstanceEBSIOPSExceededCheck=1` for every minute of the drive window. VolumeReadOps ≈ 11.8k IOPS while volume was provisioned at 16000 — instance ceiling first.

`VolumeIOPSExceededCheck` / `VolumeThroughputExceededCheck`: **no datapoints** (do not treat empty as proven 0; Arm 1 volume-ceiling run also empty).

Throughput probe: **250 MiB/s** with `InstanceEBSThroughputExceededCheck=1`.

## Corpus

`data/observations/aws-ebs-instceil-2026-08-21.yaml` + source `obs-aws-ebs-instceil-2026-08-21`.
