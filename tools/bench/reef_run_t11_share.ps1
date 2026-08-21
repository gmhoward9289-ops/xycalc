# T11 WiredTiger share sweep — Windows/PowerShell, no Git Bash.
# Shares: 50%, 70% of MONGO_MEM_GB as WT cache; OVERSUB dataSize/cache.
$ErrorActionPreference = 'Stop'
$OutDir = 'C:\Users\Owner\xycalc-results\colocation-share'
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$Log = Join-Path $OutDir 'sweep.log'
function Log([string]$m) { $m | Tee-Object -FilePath $Log -Append }

'' | Set-Content $Log
$env:Path = 'C:\Program Files\Docker\Docker\resources\bin;' + $env:Path
Set-Location 'C:\Users\Owner\dev\xycalc\tools\bench\colocation_probe'

$MongoMemGb = 4
$Shares = @(50, 70)
$Oversub = 2.5
$BytesPerDoc = 756
$env:REDIS_MEM = '2g'
$env:CLICKHOUSE_MEM = '4g'
$env:WORKER_MEM = '1g'

Log "T11 PS share sweep mongo_mem=${MongoMemGb}g shares=$($Shares -join ',') oversub=$Oversub"
docker version | Out-String | Tee-Object -FilePath $Log -Append | Out-Null

$summary = Join-Path $OutDir 'summary.jsonl'
'' | Set-Content $summary

foreach ($pct in $Shares) {
  $cacheGb = [math]::Round($MongoMemGb * $pct / 100.0, 3)
  $docs = [int](($Oversub * $cacheGb * [math]::Pow(1024,3)) / $BytesPerDoc)
  $tag = "share${pct}-cache${cacheGb}g-docs${docs}"
  Log "== $tag =="
  $env:MONGO_MEM = "${MongoMemGb}g"
  $env:MONGO_CACHE_GB = "$cacheGb"
  $env:PROBE_DOCS = "$docs"
  $env:OUT = (Join-Path $OutDir "$tag.json")

  try {
    docker compose down -v --remove-orphans 2>$null | Out-Null
    Log 'phase 1 idle'
    docker compose up -d --build mongo redis clickhouse worker 2>&1 | Tee-Object -FilePath $Log -Append
    Start-Sleep -Seconds 8
    python sample.py idle | Set-Content (Join-Path $OutDir 'phase_idle.json')

    Log 'loading'
    docker compose --profile driver run --rm --no-deps -T driver python drive.py 2>&1 | Tee-Object -FilePath $Log -Append
    Get-Content clickhouse_load.sql | docker compose exec -T clickhouse clickhouse-client --multiquery 2>&1 | Tee-Object -FilePath $Log -Append

    Log 'phase 2 loaded'
    Start-Sleep -Seconds 3
    python sample.py loaded | Set-Content (Join-Path $OutDir 'phase_loaded.json')

    Log 'phase 3 under_load'
    $job = Start-Job { Set-Location $using:PWD; $env:Path = $using:env:Path; docker compose --profile driver run --rm --no-deps -T driver python drive.py }
    Start-Sleep -Seconds 8
    python sample.py under_load | Set-Content (Join-Path $OutDir 'phase_under_load.json')
    Wait-Job $job -Timeout 180 | Out-Null
    Receive-Job $job 2>&1 | Tee-Object -FilePath $Log -Append
    Remove-Job $job -Force -ErrorAction SilentlyContinue

    $data = @{
      idle = Get-Content (Join-Path $OutDir 'phase_idle.json') -Raw | ConvertFrom-Json
      loaded = Get-Content (Join-Path $OutDir 'phase_loaded.json') -Raw | ConvertFrom-Json
      under_load = Get-Content (Join-Path $OutDir 'phase_under_load.json') -Raw | ConvertFrom-Json
    }
    $data | ConvertTo-Json -Depth 8 | Set-Content $env:OUT
    $row = [ordered]@{ share_pct = $pct; cache_gb = $cacheGb; probe_docs = $docs }
    foreach ($ph in 'idle','loaded','under_load') {
      $svc = $data.$ph.services
      $row[$ph] = @{}
      foreach ($k in $svc.PSObject.Properties.Name) {
        $row[$ph][$k] = $svc.$k.mem_used
      }
    }
    ($row | ConvertTo-Json -Compress) | Add-Content $summary
    Log "wrote $($env:OUT)"
  }
  catch {
    Log "FAILED $tag : $_"
  }
  finally {
    docker compose down -v --remove-orphans 2>$null | Out-Null
  }
}
Log 'DONE'
