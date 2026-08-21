# Azure Premium SSD v2 lifecycle (Windows/COOPER companion).
# Prefer Git Bash for full run: tools/bench/azure_psv2_lifecycle.sh run-all
#
#   .\tools\bench\azure_psv2_lifecycle.ps1 -Action preflight
#   .\tools\bench\azure_psv2_lifecycle.ps1 -Action create
#   .\tools\bench\azure_psv2_lifecycle.ps1 -Action destroy
#   .\tools\bench\azure_psv2_lifecycle.ps1 -Action confirm-zero
param(
  [ValidateSet('preflight','create','destroy','confirm-zero','status')]
  [string]$Action = 'preflight',
  [string]$Location = 'westus2',
  [string]$Zone = '1',
  [string]$VmSize = 'Standard_D2s_v5',
  [int]$DiskGib = 64,
  [string]$SshPubKey = "$env:USERPROFILE\.ssh\id_ed25519.pub"
)

$ErrorActionPreference = 'Stop'
$Tag = "xycalc-psv2-$(Get-Date -Format 'yyyyMMdd')"
$Rg = "rg-$Tag"
$VmName = 'psv2probe'
$DiskName = 'psv2data'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Stage = Join-Path $Root "tmp\$Tag"
New-Item -ItemType Directory -Force -Path $Stage | Out-Null

function Get-SubPolicies {
  $id = az account show --query id -o tsv
  $json = az rest --method get --url "https://management.azure.com/subscriptions/${id}?api-version=2020-01-01" -o json
  return ($json | ConvertFrom-Json)
}

function Invoke-Preflight {
  $acct = az account show -o json | ConvertFrom-Json
  if ($acct.state -ne 'Enabled') { throw "subscription state=$($acct.state)" }
  $pol = (Get-SubPolicies).subscriptionPolicies
  Write-Host "subscription=$($acct.id) spendingLimit=$($pol.spendingLimit) quotaId=$($pol.quotaId)"
  if ($pol.spendingLimit -ne 'On' -and $pol.quotaId -notlike 'FreeTrial*') {
    if ($env:AZ_ALLOW_PAID -ne '1') {
      throw "refusing paid path (spendingLimit=$($pol.spendingLimit) quota=$($pol.quotaId))"
    }
  }
  if (-not (Test-Path $SshPubKey)) { throw "SSH pubkey missing: $SshPubKey" }
  Write-Host "preflight ok tag=$Tag rg=$Rg loc=$Location zone=$Zone size=$VmSize"
}

function Invoke-Create {
  Invoke-Preflight
  # First IOPS point at create (counts as adjust #1 of 4/24h)
  $firstIops = 3000
  $firstMbps = 750
  Write-Host "creating RG $Rg"
  az group create -n $Rg -l $Location --tags "xycalc=$Tag" "purpose=premium-ssd-v2-probe" "owner=xycalc" | Out-Null
  Write-Host "creating PremiumV2 disk ${DiskGib}GiB @ ${firstIops}/${firstMbps}"
  az disk create -g $Rg -n $DiskName -l $Location --zone $Zone `
    --sku PremiumV2_LRS --size-gb $DiskGib `
    --disk-iops-read-write $firstIops --disk-mbps-read-write $firstMbps `
    --tags "xycalc=$Tag" | Out-Null
  Write-Host "creating VM $VmName ($VmSize)"
  az vm create -g $Rg -n $VmName -l $Location --zone $Zone `
    --size $VmSize `
    --image "Canonical:0001-com-ubuntu-server-jammy:22_04-lts-gen2:latest" `
    --admin-username azureuser `
    --ssh-key-values $SshPubKey `
    --public-ip-sku Standard `
    --nsg-rule SSH `
    --os-disk-size-gb 30 `
    --storage-sku Premium_LRS `
    --tags "xycalc=$Tag" `
    -o json | Tee-Object -FilePath (Join-Path $Stage 'vm-create.json') | Out-Null
  $ip = az vm show -d -g $Rg -n $VmName --query publicIps -o tsv
  Set-Content -Path (Join-Path $Stage 'ip.txt') -Value $ip
  Set-Content -Path (Join-Path $Stage 'rg.txt') -Value $Rg
  Set-Content -Path (Join-Path $Stage 'vm.txt') -Value $VmName
  Set-Content -Path (Join-Path $Stage 'disk.txt') -Value $DiskName
  Set-Content -Path (Join-Path $Stage 'vm_size.txt') -Value $VmSize
  Set-Content -Path (Join-Path $Stage 'tag.txt') -Value $Tag
  Write-Host "IP=$ip - attaching disk"
  az vm disk attach -g $Rg --vm-name $VmName --name $DiskName | Out-Null
  Write-Host "create done. Next (Git Bash): ./tools/bench/azure_psv2_lifecycle.sh probe"
  Write-Host "Stage: $Stage"
}

function Invoke-Destroy {
  $rgPath = Join-Path $Stage 'rg.txt'
  $rg = if (Test-Path $rgPath) { (Get-Content $rgPath -Raw).Trim() } else { $Rg }
  Write-Host "DESTROY resource group $rg"
  az group delete -n $rg --yes --no-wait
  Write-Host "delete requested. Confirm with -Action confirm-zero"
}

function Invoke-ConfirmZero {
  Write-Host '=== groups with xycalc-psv2 ==='
  az group list --query "[?contains(name,'xycalc-psv2')].{name:name,loc:location}" -o table
  Write-Host '=== all VMs ==='
  az vm list -o table
  Write-Host '=== all disks ==='
  az disk list -o table
}

switch ($Action) {
  'preflight' { Invoke-Preflight }
  'create' { Invoke-Create }
  'destroy' { Invoke-Destroy }
  'confirm-zero' { Invoke-ConfirmZero }
  'status' {
    if (Test-Path (Join-Path $Stage 'ip.txt')) {
      Get-ChildItem $Stage | ForEach-Object { Write-Host $_.Name; Get-Content $_.FullName }
    } else {
      Write-Host "no stage at $Stage"
    }
    Invoke-ConfirmZero
  }
}
