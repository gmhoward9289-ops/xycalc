# Investigation 017 — I/O size crossover (T9 / #17)

**Status:** Arm A complete on reef; Arm B native Z: landing; **Arm C
complete** (2026-08-21) on temp AWS `m6i.large` + gp3 (`i-0b0f4da9d10b1abba`,
torn down same day).

**Harness:** `tools/bench/io_crossover_probe.py` +
`tools/bench/reef_run_t9_io.ps1` / `reef_run_t9_native_nvme.ps1`.

---

## Short answer

Under Docker Desktop blkio emulation of gp3 limits, the IOPS→throughput
knee appears, and the throttle ceilings themselves match the configured
caps. Absolute KiB of the knee is **coarser than the pure arithmetic**
(baseline measured **64 KiB** vs predicted **42.7 KiB**; throughput-cap
**256 KiB** vs predicted **~195 KiB**) — still the right *shape*, not a
silent no-knee.

| Arm | Throttle | IOPS plateau | Throughput plateau | Crossover | Predicted |
|---|---|---:|---:|---:|---:|
| A gp3-baseline | 3000 / 125 MiB/s | 3000.8 | 125.4 MiB/s | **64 KiB** | 42.7 KiB |
| A gp3-throughput-cap | 10500 / 2000 MiB/s | 10496.8 | 2000.0 MiB/s | **256 KiB** | 195 KiB |
| B Docker-local (virtio `/dev/sdd` path used for Arm A device) | none | (see Docker local arm file) | ~5.8 GiB/s class | 64 KiB | — |
| B native Z: Samsung NVMe | none | *(landing)* | *(landing)* | *(landing)* | — |
| C real gp3 (m6i.large) | AWS 3000 / 125 | ~3212 (8–32 KiB) | ~134.1 MiB/s | harness **128 KiB** (first TP-bound **64 KiB**) | 42.7 KiB |

**Do not treat Arm A numbers as real EBS.** They validate the queuing
arithmetic under cgroup caps. Arm C is the real-gp3 measurement.

---

## Guards

- Arm A used `--device-read-bps/iops` against `/dev/sdd` (1 TiB Virtual
  Disk inside Docker Desktop). Caps matched plateaus (±0.1%).
- Healthy-env check: a run without throttle would not print flat 3000 /
  125 — the plateaus tracking the configured caps is the guard that
  this is not a free-disk table.
- Prior C: SATA smoke coefficients remain labeled; native Z: NVMe is the
  intended `nvme-ssd` replacement when import lands.

---

## Arm C (real gp3)

- Instance `i-0b0f4da9d10b1abba` (`m6i.large`, us-east-2), dedicated 100 GiB
  gp3 @ 3000 IOPS / 125 MiB/s on `/dev/nvme1n1`. Wall-clock ~5 min.
  Soft cost estimate **~$0.02** (well under ~$5). Terminated + SG/keypair
  deleted; zero leftovers.
- Plateaus track provisioned caps (±~7%). Automated crossover landed at
  **128 KiB** because the 4 KiB point was cold (801 IOPS) and pulled the
  IOPS-plateau mean down; the first clearly throughput-bound size is
  **64 KiB** — same coarse knee as Arm A baseline emulation.
- Documented planning `ebs.gp3-baseline-io-crossover-kib` (42.7) **still
  kept**; Arm C is observation evidence, not a coefficient overwrite.
- Launched with `GEORGE_T9C_OVERRIDE=1` while Arm B native was still
  landing; Arm A observations were already in-repo.

## Corpus

- Observations: `data/observations/reef-io-crossover-cgroup-2026-08-21.yaml`,
  `data/observations/aws-t9c-gp3-2026-08-21.yaml`
- Emulated crossover coeffs (distinct slugs, not overwriting documented
  arithmetic): `data/coefficients/ebs-io-crossover-emulated-2026-08-21.yaml`
- Documented `ebs.gp3-baseline-io-crossover-kib` (42.7) **kept** as the
  planning figure (Arm C agrees on shape; knee still coarser than arithmetic).
- Artifacts: `docs/investigations/017-io-crossover/artifacts/`
  (`aws-t9c-gp3-20260821.json`)

---

## Remaining

1. Confirm native Z: NVMe Arm B import → replace/augment `nvme-ssd.*`
   measured rows (prior C: SATA smoke was mis-captioned).
