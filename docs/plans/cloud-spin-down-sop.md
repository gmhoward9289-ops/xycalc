# Cloud spin-down SOP — xycalc probes → zero ongoing cost

**Goal:** After each AWS/Azure probe (and at program end), leave **no billable
xycalc leftovers**. Remaining ROADMAP work (T3–T6, T8, T10, reef Docker) is
**$0 cloud** — reef / COOPER / lynx only.

**Do not** raise budgets or launch new cloud boxes unless George explicitly
approves a new run.

**Regions used historically:** AWS `us-east-2`; Azure `westcentralus` (PSv2),
plus any free-trial fallback you actually used.

---

## 0. Auth (once per session)

```powershell
# AWS (Console Credentials / aws login — session expires)
aws login --region us-east-2
aws sts get-caller-identity

# Azure
az account show --query "{name:name,id:id,state:state,policies:subscriptionPolicies}" -o json
# Expect free trial + hard stop: quotaId FreeTrial_*, spendingLimit On
az rest --method get --url "/subscriptions/$(az account show --query id -o tsv)?api-version=2020-01-01" `
  --query "subscriptionPolicies" -o json
```

---

## 1. Immediate verify-zero (must be empty / false)

### AWS (`us-east-2` — also run if you ever used another region)

```powershell
$R = "us-east-2"

# Instances (running + stopped) tagged for xycalc probes
aws ec2 describe-instances --region $R `
  --filters "Name=tag-key,Values=xycalc*" "Name=instance-state-name,Values=pending,running,stopping,stopped" `
  --query "Reservations[].Instances[].[InstanceId,State.Name,Tags[?Key=='Name'].Value|[0]]" --output table

aws ec2 describe-instances --region $R `
  --filters "Name=tag:Name,Values=xycalc-*" `
  --query "Reservations[].Instances[].[InstanceId,State.Name,Tags[?Key=='Name'].Value|[0]]" --output table

# Broader name / tag sweep (t9c / t11)
aws ec2 describe-instances --region $R `
  --filters "Name=tag:Name,Values=xycalc-t9c-*,xycalc-t11-*" `
  --query "Reservations[].Instances[].[InstanceId,State.Name,Tags]" --output json

# Volumes: available (orphans) + in-use
aws ec2 describe-volumes --region $R `
  --filters "Name=tag-key,Values=xycalc*" `
  --query "Volumes[].[VolumeId,State,Size,Attachments[0].InstanceId]" --output table

aws ec2 describe-volumes --region $R `
  --filters "Name=status,Values=available" `
  --query "Volumes[?Tags[?contains(Key, 'xycalc') || contains(Value, 'xycalc')]].[VolumeId,Size,CreateTime]" --output table

# Elastic IPs (unassociated = ongoing cost)
aws ec2 describe-addresses --region $R `
  --query "Addresses[?AssociationId==null].[PublicIp,AllocationId,Tags]" --output table

# Key pairs + security groups named for probes
aws ec2 describe-key-pairs --region $R `
  --query "KeyPairs[?starts_with(KeyName, 'xycalc')].[KeyName,KeyPairId]" --output table

aws ec2 describe-security-groups --region $R `
  --query "SecurityGroups[?starts_with(GroupName, 'xycalc')].[GroupId,GroupName,VpcId]" --output table

# Snapshots
aws ec2 describe-snapshots --region $R --owner-ids self `
  --query "Snapshots[?contains(Description, 'xycalc') || Tags[?contains(Key,'xycalc') || contains(Value,'xycalc')]].[SnapshotId,StartTime,VolumeSize]" --output table
```

**Pass criteria:** every table empty (except maybe unrelated personal resources
you already own — those are out of scope).

### Azure

```powershell
az group exists -n rg-xycalc-psv2-20260821   # must be: false
az group list --query "[].name" -o tsv        # xycalc RGs must be absent
az vm list -d -o table                        # empty of xycalc VMs
az resource list --query "[?contains(name, 'xycalc') || contains(name, 'psv2') || contains(name, '20260821')].[name,type,resourceGroup]" -o table
az network public-ip list -o table            # no probe IPs
az resource list --resource-type Microsoft.Compute/disks -o table
```

`NetworkWatcherRG` alone is fine (Azure platform; not an xycalc probe RG).

---

## 2. Destroy leftovers (only if verify finds them)

### AWS — known historical IDs (2026-08-21; should already be gone)

| What | ID / name |
|------|-----------|
| T9c instance | `i-0b0f4da9d10b1abba` |
| T9c SG | `sg-0e63801df08605d1e` |
| T9c key | `xycalc-t9c-xycalc-t9c-20260821` |
| T11 instance | `i-0ab88434f24464a3e` |
| T11 SG | `sg-00c0dd8b4ed5f0198` |
| T11 key | `xycalc-t11-xycalc-t11-20260821-0147` |

```powershell
$R = "us-east-2"

# Instances
aws ec2 terminate-instances --region $R --instance-ids i-0b0f4da9d10b1abba i-0ab88434f24464a3e
aws ec2 wait instance-terminated --region $R --instance-ids i-0b0f4da9d10b1abba i-0ab88434f24464a3e

# Orphans discovered by verify (fill in)
# aws ec2 terminate-instances --region $R --instance-ids <iid>
# aws ec2 delete-volume --region $R --volume-id <vol>
# aws ec2 release-address --region $R --allocation-id <eipalloc>
# aws ec2 delete-snapshot --region $R --snapshot-id <snap>
# aws ec2 delete-security-group --region $R --group-id <sg>
# aws ec2 delete-key-pair --region $R --key-name <name>
```

### Azure

```powershell
# Whole probe RG (preferred — deletes VM, disks, NIC, public IP)
az group delete -n rg-xycalc-psv2-20260821 --yes --no-wait

# Orphans outside that RG (rare)
# az vm delete -g <rg> -n <vm> --yes
# az disk delete -g <rg> -n <disk> --yes
# az network public-ip delete -g <rg> -n <ip> --yes
```

---

## 3. After each probe — teardown checklist

Copy this for every future cloud run:

1. **Watcher running before probe** (`_aws_t9c_watcher.sh` / `_aws_t11_watcher.sh`
   or Azure lifecycle helper). Soft max hours ≤ soft cap (~$5 AWS).
2. On DONE / FAIL / timeout: pull JSON → **terminate / delete RG same session**.
3. Confirm watcher log contains `TEARDOWN=OK` (or `az group exists` → `false`).
4. Re-run **§1 verify-zero**.
5. Import artifacts to `local/` → review → publish YAML only (no scratch binaries).
6. Optionally delete local stage PEMs under `tmp/xycalc-*/*.pem` (not a cloud
   cost, but reduces key sprawl).

---

## 4. Disable / delete standing control-plane leftovers

| Asset | Action |
|-------|--------|
| Key pairs `xycalc-*` | `aws ec2 delete-key-pair` |
| Security groups `xycalc-*` | `aws ec2 delete-security-group` (after instance gone) |
| Unattached volumes / snapshots | delete |
| Unassociated Elastic IPs | `release-address` (**these bill**) |
| Azure RGs `rg-xycalc-*` | `az group delete` |
| Local stage dirs `tmp/xycalc-*`, `tmp/aws-ebs-*` | keep JSON if needed; delete `*.pem` |

---

## 5. Confirm nothing auto-launches boxes

```powershell
# Windows Scheduled Tasks — must find nothing xycalc/aws probe related
Get-ScheduledTask | Where-Object { $_.TaskName -match 'xycalc|t9c|t11|psv2|aws-ebs' }

# No detached watchers holding launch scripts
Get-Process | Where-Object { $_.ProcessName -match 'bash|ssh' }   # eyeball; kill only if you know it's a probe watcher

# Launch scripts stay gated — do NOT export these unless George approved a run:
#   CONFIRM_T9C_LAUNCH=1
#   GEORGE_T9C_OVERRIDE=1
#   CONFIRM_T11_LAUNCH (if used)
```

There is **no** estate cron that re-launches xycalc EC2/Azure VMs. Launchers
under `tools/bench/_aws_t*.sh` and `azure_psv2_lifecycle.*` are manual only.

---

## 6. What stays free forever vs what must be gone

| Stays ($0 ongoing) | Must be gone (billable if left) |
|--------------------|----------------------------------|
| reef Docker probes, local NVMe/fio | EC2 instances (running **or** stopped) |
| Corpus YAML under `data/**` | EBS volumes, snapshots |
| Findings / ROADMAP / this SOP | Elastic IPs |
| LiteLLM → Ollama on reef | Unused key pairs / SGs (hygiene; SG alone ≈ $0) |
| Azure **free trial** with `spendingLimit: On` idle | Azure VMs, managed disks, public IPs, probe RGs |
| `NetworkWatcherRG` (platform) | Any `rg-xycalc-*` |

**Program status (cheap_cloud_reef_tests plan):** AWS T9c and Azure PSv2 are
**done**. Remaining waves are reef-only → **no further cloud spend required**.

---

## 7. Budget rule (locked)

- Soft cap: **~$5 / AWS run**, tear down same day.
- Azure: **free credits only** (`spendingLimit: On`). Do not convert to pay-as-you-go
  for this program.
- **Raise budget?** For remaining work: **NO** — unless George later orders a
  new cloud measurement outside this plan.

---

## 8. One-shot recheck after `aws login`

```powershell
$R = "us-east-2"
aws sts get-caller-identity
aws ec2 describe-instances --region $R --filters "Name=tag-key,Values=xycalc*" --query "length(Reservations[].Instances[])" -o text
aws ec2 describe-key-pairs --region $R --query "length(KeyPairs[?starts_with(KeyName, 'xycalc')])" -o text
aws ec2 describe-security-groups --region $R --query "length(SecurityGroups[?starts_with(GroupName, 'xycalc')])" -o text
aws ec2 describe-addresses --region $R --query "length(Addresses[?AssociationId==null])" -o text
az group exists -n rg-xycalc-psv2-20260821
az resource list --query "length([?contains(name, 'xycalc')])" -o tsv
```

All lengths / exists should be `0` / `false`.
