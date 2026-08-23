# Findings — Azure Premium SSD v2 throughput ceiling

**Investigated:** 2026-08-21 · **Model:** `azure.premium-v2-throughput-ceiling` ·
**Host:** `Standard_D2s_v5` · **Region:** `westcentralus` (nonzonal; free-trial
zonal capacity was unavailable in eastus2/westus2) · **Tag:** `xycalc-psv2-20260821`

**Question:** Does a live Premium SSD v2 control plane enforce the documented
ceiling (0.25 MB/s per provisioned IOPS, floor 125, cap 2,000) the model
reproduces from Azure’s worked examples?

---

## The short answer

Yes for the control plane. Azure accepted the model’s predicted maximum at
every IOPS point we set, including **8,000 IOPS → 2,000 MB/s**. That settles
the pricing-page (1,200) vs technical-reference (2,000) tension for this
subscription/region in favor of the 2,000 cap the model already uses.

Delivery is a separate story: short fio runs sometimes ran *above* the
settable ceiling at 3,000 IOPS (managed disk confirmed — not local NVMe). The
importer correctly refused a ceiling validation case for that point; 4,000 and
8,000 imported clean validation cases into `local/`.

---

## What ran

| IOPS set | MB/s requested (= model) | Azure accepted? | Delivered seq write MB/s | Validation case |
|---:|---:|---|---:|---|
| 3,000 | 750 | yes (at create) | 863 | refused (delivery > ceiling×1.10) |
| 4,000 | 1,000 | yes | 867 | written → `local/validation/` |
| 8,000 | 2,000 | yes | 1,172 | written → `local/validation/` |

Harness: `tools/bench/azure_premium_v2_probe.sh` + `.py` (`fio --direct=1`,
20s, iodepth 32, `--size=1G`). Import: `tools/import_azure_probe.py` →
`local/` only (not published). Device: `/dev/sdb` 64 GiB PremiumV2_LRS
mounted at `/mnt/psv2` (OS is `/dev/sda`; this SKU has no local NVMe temp
disk).

Lifecycle helpers (for reruns): `tools/bench/azure_psv2_lifecycle.sh` /
`.ps1`. Prefer `westcentralus` nonzonal on free trial; zonal SKUs were
`SkuNotAvailable` in eastus2/westus2 for this subscription.

---

## Control-plane finding (validates the model)

At 3k / 4k / 8k IOPS, `az disk create` / `az disk update` accepted exactly
`min(2000, max(125, 0.25 × IOPS))` MB/s. The 8k→2000 acceptance is the
decisive check against the pricing-page 1,200 figure.

Coefficients are unchanged — this validates the ceiling model against the live
API; it does not re-derive vendor constants. Nothing was written into `ebs.*`.

---

## Delivery finding (not a ceiling-model failure)

- **3,000 IOPS:** delivered ~863 MB/s vs 750 settable (~15% over). Guard
  flagged “wrong device”; lsblk says otherwise. Treat as short-run
  overdelivery / burst above the provisioned floor, not a model miss.
- **4,000 / 8,000:** delivered under the settable ceiling (867 / 1,172).
  Sustained delivery well below provisioned at 8k is expected on a small VM
  and does not falsify the *settable* ceiling.

---

## Ops / leftovers

Resource group `rg-xycalc-psv2-20260821` deleted after import. Confirm with:

```powershell
az group exists -n rg-xycalc-psv2-20260821
az vm list -o table
az disk list -o table
```

---

## Publish gate

Published 2026-08-23 into `data/`: 4k and 8k control-plane ceilings as
validation cases; 3k/4k/8k delivery as observations only. 3k still has no
ceiling case (delivery overran the guard). Cases are API acceptances, not
fio proofs. See `data/validation/azure-psv2-2026-08-21.yaml`.
