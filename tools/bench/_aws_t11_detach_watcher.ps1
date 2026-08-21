# Detached T11 AWS watcher — survives Cursor shell abort.
$ErrorActionPreference = 'Stop'
$bash = 'C:\Program Files\Git\bin\bash.exe'
$watcher = '/c/Users/gmhow/dev/xycalc/tools/bench/_aws_t11_watcher.sh'
$stage = 'C:\Users\gmhow\dev\xycalc\tmp\xycalc-t11-20260821-0147'
$log = Join-Path $stage 'watcher.log'
$pidFile = Join-Path $stage 'watcher.pid'

# Kill prior bash watchers matching our script name (best-effort)
Get-CimInstance Win32_Process -Filter "Name='bash.exe'" |
  Where-Object { $_.CommandLine -match '_aws_t11_watcher' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $bash
$psi.Arguments = "-lc `"bash $watcher 900`""
$psi.WorkingDirectory = 'C:\Users\gmhow\dev\xycalc'
$psi.UseShellExecute = $true
$psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
$p = [System.Diagnostics.Process]::Start($psi)
$p.Id | Set-Content $pidFile
Add-Content $log "$(Get-Date -Format o) launch_ps pid=$($p.Id)"
Write-Output "detached_pid=$($p.Id)"
