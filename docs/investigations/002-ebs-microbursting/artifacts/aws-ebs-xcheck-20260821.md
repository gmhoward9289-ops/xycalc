# Arm 1 — EBS ExceededChecks + guest iostat (2026-08-21)

**Status:** guest + observation YAML landed; CloudWatch ExceededChecks **still empty after re-pull** (publish quirk, not lag). Striping deferred.

| | |
|---|---|
| Instance | `i-04431bd8261dbd94a` (terminated) |
| Volume | `vol-0859940aeff344ecc` (deleted) — 100 GiB gp3 @ **3000 IOPS / 125 MiB/s** |
| Host | `m6i.large`, us-east-2, account `189575358547` |
| Stage | `tmp/xycalc-ebs-xcheck-20260821/results/` |
| Harness | `tools/bench/_aws_ebs_xcheck_launch.sh` |

## Phase windows (UTC)

| Phase | Start | End | Intent |
|---|---|---|---|
| under | 2026-08-21T17:10:26Z | 17:13:26Z | ~2000 IOPS (below provisioned mean) |
| over | 2026-08-21T17:13:56Z | 17:16:57Z | high QD, uncapped vs 3000 |

## Guest fio / 1s IOPS log (`xcheck-summary.json`)

| Phase | n (1s samples) | mean IOPS | peak IOPS | peak/mean |
|---|---:|---:|---:|---:|
| under | 170 | 2106.2 | 5609 | **2.66** |
| over | 180 | 3017.0 | 5992 | **1.99** |

fio aggregate: under IOPS=1999 (rate-limited); over IOPS=3016 (QD64, latency ~21 ms — volume ceiling).

**Important for the 0-under / 1-over hypothesis:** under *mean* stayed below 3000, but under *peak second was 5609*. If `VolumeIOPSExceededCheck` means any second in the minute, under is **not** expected to stay at 0. Do not treat "0 under" as the success criterion once CW lands.

## CloudWatch (immediate pull + re-pull 2026-08-21 evening — still empty)

`VolumeIOPSExceededCheck` / `VolumeThroughputExceededCheck` / `VolumeAvgIOPS`: **n=0** datapoints on both pulls. Classic `VolumeReadOps` / `VolumeWriteOps` / `VolumeQueueLength` **did** land (see table).

Classic ops **did** land (Sum / minute ≈ ops in that minute):

| UTC minute | VolumeReadOps | ≈ IOPS | Phase overlap |
|---|---:|---:|---|
| 17:11 | 94690 | ~1578 | under (ramp) |
| 17:12–17:13 | ~120000 | ~2000 | under |
| 17:14 | 79294 | ~1322 | gap / over start |
| 17:15–17:16 | ~180000 | ~3000 | over |
| 17:17 | 129059 | ~2151 | over end / drain |

So the volume was busy; ExceededCheck emptiness is lag or publish quirk, not "no load."

Re-pull: `bash tools/bench/_aws_ebs_xcheck_pull_cw.sh`

## Spend (re-check at land)

| Meter | Value |
|---|---|
| SwampLink budget | **$0** / $20 (console; soft alert) |
| xycalc-hard-cap | **$0** / $150 (campaign hard) |
| Free-tier remaining credits | **$200** |
| CE UnblendedCost MTD | **$0** (estimated) |
| Live EC2/EBS inventory | 0 instances, 0 volumes |

## Corpus landing

Guest peak/mean + mean IOPS: `data/observations/aws-ebs-xcheck-2026-08-21.yaml` + `data/sources/aws-ebs-xcheck-2026-08-21.yaml`. **No** ExceededCheck 0/1 rows — CW never published them for this volume.
