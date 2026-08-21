# T9 Arm A (cgroup throttle) + Arm B (local NVMe) on reef.
# Scratch + results on V:/Z: — never leave multi-GB binaries on C: or commit them.
$ErrorActionPreference = 'Continue'
$Repo = 'C:\Users\Owner\dev\xycalc'
$Out = 'V:\xycalc-results\io-crossover'
$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$Log = Join-Path $Out ("run-$Stamp.log")
$Json = Join-Path $Out ("io-crossover-$Stamp.json")
New-Item -ItemType Directory -Force -Path $Out | Out-Null

function Log([string]$m) {
  $line = "$(Get-Date -Format o) $m"
  [IO.File]::AppendAllText($Log, $line + [Environment]::NewLine)
  Write-Host $line
}

# Prefer Z: (Samsung NVMe) for Arm B if present; else V: (WD BLACK SN770).
$ScratchRoot = if (Test-Path 'Z:\') { 'Z:\xycalc-work\io-crossover' } else { 'V:\xycalc-work\io-crossover' }
New-Item -ItemType Directory -Force -Path $ScratchRoot | Out-Null
$Arm = if ($env:PROBE_ARM) { $env:PROBE_ARM } else { 'both' }
$Runtime = if ($env:PROBE_RUNTIME) { $env:PROBE_RUNTIME } else { '12' }
$FileMb = if ($env:PROBE_FILE_MB) { $env:PROBE_FILE_MB } else { '4096' }
$Name = if ($env:PROBE_NAME) { $env:PROBE_NAME } else { "xycalc-io-$Stamp" }
$Container = "$Name-fio"
$PyImage = if ($env:PROBE_PY_IMAGE) { $env:PROBE_PY_IMAGE } else { 'python:3.12-slim' }

Log "OUT=$Out SCRATCH=$ScratchRoot ARM=$Arm RUNTIME=$Runtime FILE_MB=$FileMb"

# Resolve a block device inside Linux containers via docker run probe.
# On Docker Desktop Windows the throttle device is the virtio disk backing the
# Linux VM — document transport honestly; Arm B plateaus are still useful as
# "local to the Docker VM" and we stamp machine_class accordingly.
$Dev = if ($env:PROBE_DEV) { $env:PROBE_DEV } else { '' }

try { docker rm -f $Container "${Container}-t" 2>$null | Out-Null } catch {}

try {
  Log "starting base container $Container from $PyImage"
  docker run -d --name $Container --memory 512m --memory-swap 512m $PyImage sleep infinity
  if ($LASTEXITCODE -ne 0) { throw 'docker run base failed' }
  docker exec $Container apt-get update -qq
  docker exec $Container apt-get install -y -qq --no-install-recommends fio python3
  docker cp "$Repo\tools\bench\io_crossover_probe.py" "${Container}:/tmp/io_crossover_probe.py"

  if (-not $Dev) {
    $Dev = (docker exec $Container bash -lc "df --output=source / | tail -1").Trim()
    $parent = (docker exec $Container bash -lc "lsblk -no PKNAME '$Dev' 2>/dev/null | head -1").Trim()
    if ($parent) { $Dev = "/dev/$parent" }
  }
  Log "device=$Dev"
  docker exec $Container bash -lc "lsblk -o NAME,ROTA,TRAN,SIZE,MODEL '$Dev' || true" | ForEach-Object { Log $_ }

  $Test = "/tmp/io-probe-${FileMb}m.bin"
  Log "allocating ${FileMb} MiB test file in container (ephemeral)"
  docker exec $Container dd if=/dev/zero of=$Test bs=1M count=$FileMb status=none
  if ($LASTEXITCODE -ne 0) { throw 'dd test file failed' }

  $Sizes = if ($env:PROBE_SIZES) { $env:PROBE_SIZES } else { '4,8,16,32,64,128,256,512,1024' }
  $armsOut = Join-Path $Out "arms-$Stamp.jsonl"
  [IO.File]::WriteAllText($armsOut, '')

  function Invoke-Arm([string]$tag, [string]$extraArgs) {
    Log "=== arm $tag ==="
    $cmd = "python3 /tmp/io_crossover_probe.py --test-file $Test --device $Dev --arm $tag --sizes-kib $Sizes --runtime $Runtime --iodepth 32 $extraArgs"
    $raw = docker exec $Container bash -lc $cmd
    $text = ($raw | Out-String)
    [IO.File]::AppendAllText((Join-Path $Out "arm-$tag-$Stamp.txt"), $text)
    $jsonLine = ($text -split "`n" | Where-Object { $_.Trim().StartsWith('{') } | Select-Object -Last 1)
    if (-not $jsonLine) {
      # probe may print ===JSON=== then object
      if ($text -match '(?s)===JSON===\r?\n(\{.*\})\s*$') { $jsonLine = $Matches[1] }
    }
    if (-not $jsonLine) { throw "no JSON from arm $tag" }
    [IO.File]::AppendAllText($armsOut, $jsonLine.Trim() + [Environment]::NewLine)
    return $jsonLine
  }

  $armDocs = @()
  if ($Arm -eq 'local' -or $Arm -eq 'both') {
    $armDocs += (Invoke-Arm 'local' '')
  }
  if ($Arm -eq 'throttled' -or $Arm -eq 'both') {
    # Arm A baseline 3000 IOPS / 125 MiB/s
    docker rm -f "${Container}-t" 2>$null | Out-Null
    $bps125 = 125 * 1024 * 1024
    docker run -d --name "${Container}-t" --device-read-bps "${Dev}:${bps125}" --device-read-iops "${Dev}:3000" --memory 512m --memory-swap 512m $PyImage sleep infinity
    docker exec "${Container}-t" apt-get update -qq
    docker exec "${Container}-t" apt-get install -y -qq --no-install-recommends fio python3
    docker cp "$Repo\tools\bench\io_crossover_probe.py" "${Container}-t:/tmp/io_crossover_probe.py"
    $tfile = "/tmp/io-probe-t-${FileMb}m.bin"
    docker exec "${Container}-t" dd if=/dev/zero of=$tfile bs=1M count=$FileMb status=none
    Log '=== arm gp3-baseline ==='
    $raw = docker exec "${Container}-t" bash -lc "python3 /tmp/io_crossover_probe.py --test-file $tfile --device $Dev --arm gp3-baseline --sizes-kib $Sizes --runtime $Runtime --iodepth 32 --throttle-iops 3000 --throttle-bps $bps125"
    $text = ($raw | Out-String)
    [IO.File]::AppendAllText((Join-Path $Out "arm-gp3-baseline-$Stamp.txt"), $text)
    $jsonLine = if ($text -match '(?s)===JSON===\r?\n(\{.*\})\s*$') { $Matches[1] } else { ($text -split "`n" | Where-Object { $_.Trim().StartsWith('{') } | Select-Object -Last 1) }
    if (-not $jsonLine) { throw 'no JSON from gp3-baseline' }
    [IO.File]::AppendAllText($armsOut, $jsonLine.Trim() + [Environment]::NewLine)
    docker rm -f "${Container}-t" 2>$null | Out-Null

    # Arm A throughput-cap 10500 IOPS / 2000 MiB/s
    $bps2000 = 2000 * 1024 * 1024
    docker run -d --name "${Container}-t" --device-read-bps "${Dev}:${bps2000}" --device-read-iops "${Dev}:10500" --memory 512m --memory-swap 512m $PyImage sleep infinity
    docker exec "${Container}-t" apt-get update -qq
    docker exec "${Container}-t" apt-get install -y -qq --no-install-recommends fio python3
    docker cp "$Repo\tools\bench\io_crossover_probe.py" "${Container}-t:/tmp/io_crossover_probe.py"
    docker exec "${Container}-t" dd if=/dev/zero of=$tfile bs=1M count=$FileMb status=none
    Log '=== arm gp3-throughput-cap ==='
    $raw = docker exec "${Container}-t" bash -lc "python3 /tmp/io_crossover_probe.py --test-file $tfile --device $Dev --arm gp3-throughput-cap --sizes-kib $Sizes --runtime $Runtime --iodepth 32 --throttle-iops 10500 --throttle-bps $bps2000"
    $text = ($raw | Out-String)
    [IO.File]::AppendAllText((Join-Path $Out "arm-gp3-throughput-cap-$Stamp.txt"), $text)
    $jsonLine = if ($text -match '(?s)===JSON===\r?\n(\{.*\})\s*$') { $Matches[1] } else { ($text -split "`n" | Where-Object { $_.Trim().StartsWith('{') } | Select-Object -Last 1) }
    if (-not $jsonLine) { throw 'no JSON from gp3-throughput-cap' }
    [IO.File]::AppendAllText($armsOut, $jsonLine.Trim() + [Environment]::NewLine)
    docker rm -f "${Container}-t" 2>$null | Out-Null
  }

  # Bundle arms into one document for import_io_probe.py
  $arms = @()
  Get-Content $armsOut | ForEach-Object {
    if ($_.Trim()) { $arms += (ConvertFrom-Json $_) }
  }
  $bundle = @{
    host = 'reef'
    device = $Dev
    scratch_root = $ScratchRoot
    observed_on = (Get-Date -Format 'yyyy-MM-dd')
    arms = $arms
  } | ConvertTo-Json -Depth 8
  [IO.File]::WriteAllText($Json, $bundle)
  Log "wrote $Json"
  Log '===DONE==='
  exit 0
}
catch {
  Log "FATAL: $_"
  exit 1
}
finally {
  try { docker rm -f $Container "${Container}-t" 2>$null | Out-Null } catch {}
}
