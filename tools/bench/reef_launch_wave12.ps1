# Launch wave12 batch via WSL bash (Docker Desktop). Native NVMe is separate.
$ErrorActionPreference = 'Continue'
$Log = 'V:\xycalc-results\wave12-smokes\wmi-launch.log'
New-Item -ItemType Directory -Force -Path (Split-Path $Log) | Out-Null
function Log($m) { $l="$(Get-Date -Format o) $m"; [IO.File]::AppendAllText($Log, $l+[Environment]::NewLine); Write-Host $l }
try {
  Log 'start wave12'
  $wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
  if (-not $wsl) { throw 'wsl.exe not found' }
  # Convert CRLF script already on disk
  $sh = '/mnt/c/Users/Owner/lab/reef_run_wave12_smokes.sh'
  & wsl.exe -e bash -lc "sed -i 's/\r$//' $sh; chmod +x $sh; bash $sh"
  Log "wsl exit=$LASTEXITCODE"
  Log '===DONE==='
  exit 0
} catch {
  Log "FATAL: $_"
  exit 1
}
