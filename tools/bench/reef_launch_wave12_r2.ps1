$ErrorActionPreference="Continue"
$Log="V:\xycalc-results\wave12-smokes-r2\wmi-launch.log"
New-Item -ItemType Directory -Force -Path (Split-Path $Log) | Out-Null
function Log($m){ $l="$(Get-Date -Format o) $m"; [IO.File]::AppendAllText($Log,$l+[Environment]::NewLine) }
Log "start"
& wsl.exe -e bash -lc "sed -i 's/\r`$//' /mnt/c/Users/Owner/lab/reef_run_wave12_r2.sh; bash /mnt/c/Users/Owner/lab/reef_run_wave12_r2.sh"
Log "wsl exit=$LASTEXITCODE"
Log "===DONE==="