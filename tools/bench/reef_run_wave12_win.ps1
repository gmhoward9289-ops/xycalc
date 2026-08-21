# Thin WMI launcher — runs Git Bash script, no nested quoting.
$ErrorActionPreference = 'Continue'
$Out = 'V:\xycalc-results\wave12-win'
$Log = Join-Path $Out 'wmi-launch.log'
New-Item -ItemType Directory -Force -Path $Out | Out-Null
function Log($m) { $l = "$(Get-Date -Format o) $m"; [IO.File]::AppendAllText($Log, $l + [Environment]::NewLine); Write-Host $l }
$Bash = 'C:\Program Files\Git\bin\bash.exe'
$Sh = 'C:\Users\Owner\lab\reef_run_wave12_win.sh'
# docker config no BOM
$utf8 = New-Object System.Text.UTF8Encoding $false
[IO.File]::WriteAllText("$env:USERPROFILE\.docker\config.json", '{"auths":{},"currentContext":"desktop-linux"}', $utf8)
try {
  Log "start $Sh"
  & $Bash $Sh
  Log "bash exit=$LASTEXITCODE"
  Log '===DONE==='
  exit 0
} catch {
  Log "FATAL: $_"
  exit 1
}
