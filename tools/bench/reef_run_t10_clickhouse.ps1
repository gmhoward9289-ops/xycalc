# T10 / investigation 012 — continuous merges-on ClickHouse probe on reef.
# Data + results on V: (WD BLACK SN770). Never grow C:. Never leave multi-GB
# scratch on swamplink (N/A here; still keep binaries off git).
#
#   # On reef (PowerShell), after git pull on C:\Users\Owner\dev\xycalc:
#   powershell -File tools\bench\reef_run_t10_clickhouse.ps1
#
# Default: continuous merges (PROBE_STOP_MERGES=0, no duty-cycle), 23.3 smoke
# first (delay=150). Set PROBE_DUAL=1 for 23.3+24.8. Optional block-IO throttle
# via PROBE_WRITE_IOPS / PROBE_WRITE_BPS (Docker Desktop virtio disk).
$ErrorActionPreference = 'Continue'
$Repo = if ($env:XYCALC_REPO) { $env:XYCALC_REPO } else { 'C:\Users\Owner\dev\xycalc' }
$Bash = if ($env:GIT_BASH) { $env:GIT_BASH } else { 'C:\Program Files\Git\bin\bash.exe' }
$Py = if ($env:PROBE_PYTHON) { $env:PROBE_PYTHON } else {
  'C:\Users\Owner\AppData\Local\Programs\Python\Python312\python.exe'
}
$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$Out = 'V:\xycalc-results\clickhouse-parts'
$Work = Join-Path 'V:\xycalc-work' ("clickhouse-parts-" + $Stamp)
$Log = Join-Path $Out ("run-$Stamp.log")
$Json = Join-Path $Out ("merges-on-continuous-$Stamp.json")

New-Item -ItemType Directory -Force -Path $Out, $Work | Out-Null

function Log([string]$m) {
  $line = "$(Get-Date -Format o) $m"
  [IO.File]::AppendAllText($Log, $line + [Environment]::NewLine)
  Write-Host $line
}

if (-not (Test-Path $Bash)) { throw "Git Bash not found at $Bash — set GIT_BASH" }
if (-not (Test-Path $Py)) { throw "Python not found at $Py — set PROBE_PYTHON" }
if (-not (Test-Path (Join-Path $Repo 'tools\bench\clickhouse_probe.sh'))) {
  throw "repo missing clickhouse_probe.sh under $Repo — git pull?"
}

Log "REPO=$Repo WORK=$Work OUT=$Out"

# clickhouse-connect for host-side driver (PROBE_LOCAL).
& $Py -c "import clickhouse_connect" 2>$null
if ($LASTEXITCODE -ne 0) {
  Log 'pip install clickhouse-connect...'
  & $Py -m pip install --quiet clickhouse-connect
  if ($LASTEXITCODE -ne 0) { throw 'pip install clickhouse-connect failed' }
}

# Git Bash path for the work dir (V:\... -> /v/...).
$WorkUnix = (& $Bash -lc "cygpath -u '$Work'").Trim()
if (-not $WorkUnix) { $WorkUnix = '/v/xycalc-work/clickhouse-parts-' + $Stamp }
$RepoUnix = (& $Bash -lc "cygpath -u '$Repo'").Trim()
$PyUnix = (& $Bash -lc "cygpath -u '$Py'").Trim()

$Dual = if ($env:PROBE_DUAL) { $env:PROBE_DUAL } else { '0' }
$Rows = if ($env:PROBE_ROWS) { $env:PROBE_ROWS } else { '50000' }
$Writers = if ($env:PROBE_WRITERS) { $env:PROBE_WRITERS } else { '16' }
$Readers = if ($env:PROBE_READERS) { $env:PROBE_READERS } else { '2' }
$StepCap = if ($env:PROBE_STEP_CAP_S) { $env:PROBE_STEP_CAP_S } else { '600' }
$Batches = if ($env:PROBE_BATCHES) { $env:PROBE_BATCHES } else { '1,10,100,1000' }
$Cpus = if ($env:PROBE_CPUS) { $env:PROBE_CPUS } else { '2' }
$Memory = if ($env:PROBE_MEMORY) { $env:PROBE_MEMORY } else { '2g' }
$BgPool = if ($env:PROBE_BACKGROUND_POOL_SIZE) { $env:PROBE_BACKGROUND_POOL_SIZE } else { '' }
$Fsync = if ($env:PROBE_FSYNC_INSERTS) { $env:PROBE_FSYNC_INSERTS } else { '0' }
$WriteBps = if ($env:PROBE_WRITE_BPS) { $env:PROBE_WRITE_BPS } else { '' }
$ReadBps = if ($env:PROBE_READ_BPS) { $env:PROBE_READ_BPS } else { '' }
$WriteIops = if ($env:PROBE_WRITE_IOPS) { $env:PROBE_WRITE_IOPS } else { '' }
$ReadIops = if ($env:PROBE_READ_IOPS) { $env:PROBE_READ_IOPS } else { '' }
$Dev = if ($env:PROBE_DEV) { $env:PROBE_DEV } else { '' }

# Continuous merges — no duty-cycle unless caller sets it.
$StopMerges = if ($env:PROBE_STOP_MERGES) { $env:PROBE_STOP_MERGES } else { '0' }
$Duty = if ($env:PROBE_MERGE_DUTY_CYCLE) { $env:PROBE_MERGE_DUTY_CYCLE } else { '' }

Log "STOP_MERGES=$StopMerges DUAL=$Dual ROWS=$Rows WRITERS=$Writers throttle_iops=$WriteIops/$ReadIops"

$envExtras = @(
  "export MSYS_NO_PATHCONV=1",
  "export PROBE_STOP_MERGES=$StopMerges",
  "export PROBE_DATA_DIR='$WorkUnix/chdata'",
  "export PROBE_PYTHON='$PyUnix'",
  "export PROBE_LOCAL=1",
  "export PROBE_ROWS=$Rows",
  "export PROBE_BATCHES='$Batches'",
  "export PROBE_WRITERS=$Writers",
  "export PROBE_READERS=$Readers",
  "export PROBE_STEP_CAP_S=$StepCap",
  "export PROBE_CPUS=$Cpus",
  "export PROBE_MEMORY=$Memory",
  "export PROBE_FSYNC_INSERTS=$Fsync",
  "export PROBE_HTTP_TIMEOUT_S=180"
)
if ($BgPool) { $envExtras += "export PROBE_BACKGROUND_POOL_SIZE=$BgPool" }
if ($Duty) { $envExtras += "export PROBE_MERGE_DUTY_CYCLE=$Duty" }
if ($WriteBps) { $envExtras += "export PROBE_WRITE_BPS=$WriteBps" }
if ($ReadBps) { $envExtras += "export PROBE_READ_BPS=$ReadBps" }
if ($WriteIops) { $envExtras += "export PROBE_WRITE_IOPS=$WriteIops" }
if ($ReadIops) { $envExtras += "export PROBE_READ_IOPS=$ReadIops" }
if ($Dev) { $envExtras += "export PROBE_DEV='$Dev'" }

if ($Dual -eq '1') {
  $envExtras += "export PROBE_SMOKE=0"
  Log 'mode=dual (23.3 + 24.8)'
} else {
  $envExtras += "export PROBE_SMOKE=1"
  $envExtras += "export PROBE_SMOKE_SIDE=pre23_6"
  Log 'mode=smoke pre23_6 (delay=150)'
}

$prelude = ($envExtras -join '; ')
$cmd = "$prelude; cd '$RepoUnix' && bash ./tools/bench/clickhouse_probe.sh"
Log "bash: $cmd"

$rawFile = Join-Path $Work 'probe-raw.txt'
cmd /c "`"$Bash`" -lc `"$cmd`" > `"$rawFile`" 2>> `"$Log`""
$rc = $LASTEXITCODE
Log "probe exit=$rc"

$raw = if (Test-Path $rawFile) { [IO.File]::ReadAllText($rawFile) } else { '' }
if ($raw -match '(?s)===JSON===\r?\n(\{.*\})\s*\z') {
  $jsonText = $Matches[1]
  [IO.File]::WriteAllText($Json, $jsonText)
  Log "wrote $Json"
} else {
  Log 'REFUSING: no ===JSON=== object in probe output — see log'
  if ($rc -eq 0) { $rc = 2 }
}

# Stamp host for applies_to / observation import.
$hostMeta = @{
  host = 'reef'
  machine_class = 'reef-ryzen-64g-win11-docker-v-sn770'
  storage = 'V: WD BLACK SN770 (Docker Desktop bind)'
  continuous_merges = ($StopMerges -eq '0' -and -not $Duty)
  probe_exit = $rc
  artifact = $Json
  stamp = $Stamp
} | ConvertTo-Json
[IO.File]::WriteAllText((Join-Path $Out "host-$Stamp.json"), $hostMeta)
Log "host meta written; done exit=$rc"
exit $rc
