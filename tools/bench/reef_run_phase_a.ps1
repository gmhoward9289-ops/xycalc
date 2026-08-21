# Prefer Windows docker.exe from Git Bash or cmd — no WSL required for the outer shell.
# cache_cliff_probe.sh still needs bash + Linux-ish tools; use Git Bash.
$ErrorActionPreference = 'Stop'
$Root = 'C:\Users\Owner\dev\xycalc'
$Out = 'C:\Users\Owner\xycalc-results'
$Log = Join-Path $Out 'phase-a.log'
$GitBash = 'C:\Program Files\Git\bin\bash.exe'
New-Item -ItemType Directory -Force -Path $Out | Out-Null

function Log([string]$m) {
  Add-Content -Path $Log -Value $m
}

Set-Content -Path $Log -Value "===== phase A (git-bash) start $(Get-Date -Format o) ====="
Log "Checking docker..."
$di = & docker info 2>&1 | Out-String
Log $di
if ($LASTEXITCODE -ne 0) {
  Log 'FAIL: docker info'
  exit 1
}
Log 'Docker OK'

& docker pull mongo:7 2>&1 | Tee-Object -FilePath $Log -Append | Out-Null
& docker pull python:3.12-slim 2>&1 | Tee-Object -FilePath $Log -Append | Out-Null

if (-not (Test-Path $GitBash)) {
  Log "FAIL: missing $GitBash"
  exit 1
}

# Wrapper on a real temp dir via Git Bash /tmp under Git's MSYS — still use WSL /tmp for linux docker wrapper
# Actually cache_cliff needs: lsblk, docker with --device-read-bps. That is Linux-only.
# So we still invoke WSL for the probe, but with /tmp docker wrapper.
$sh = '/mnt/c/Users/Owner/dev/xycalc/tools/bench/reef_run_cache_cliff_a.sh'
Log 'Starting WSL probe script...'
& wsl.exe -e bash -lc "sed -i 's/\r`$//' $sh; bash $sh" 2>&1 | Tee-Object -FilePath $Log -Append
$rc = $LASTEXITCODE
Log "wsl_exit=$rc"
exit $rc
