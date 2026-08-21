# Launch wave12-r3 on reef via WSL (Docker Desktop engine).
$ErrorActionPreference = 'Continue'
$Sh = 'C:\Users\Owner\lab\reef_run_wave12_r3.sh'
$Log = 'V:\xycalc-results\wave12-r3\launch.log'
New-Item -ItemType Directory -Force -Path 'V:\xycalc-results\wave12-r3' | Out-Null
# Copy from synced lab path if present, else from repo.
$Src = 'C:\Users\Owner\dev\xycalc\tools\bench\reef_run_wave12_r3.sh'
if (Test-Path $Src) { Copy-Item -Force $Src $Sh }
$stamp = Get-Date -Format 'yyyy-MM-ddTHH:mm:ss'
"$stamp launching r3" | Out-File -FilePath $Log -Encoding utf8
$proc = Start-Process -FilePath 'wsl.exe' -ArgumentList @(
  '-e','bash','-lc',
  "sed -i 's/\r$//' /mnt/c/Users/Owner/lab/reef_run_wave12_r3.sh; chmod +x /mnt/c/Users/Owner/lab/reef_run_wave12_r3.sh; bash /mnt/c/Users/Owner/lab/reef_run_wave12_r3.sh"
) -WindowStyle Hidden -PassThru
"pid=$($proc.Id)" | Out-File -FilePath $Log -Append -Encoding utf8
