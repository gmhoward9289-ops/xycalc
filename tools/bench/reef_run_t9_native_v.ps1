# Native Arm B on V: (WD BLACK SN770 NVMe) — backslash path for windowsaio.
$ErrorActionPreference = 'Continue'
$Out = 'V:\xycalc-results\io-crossover'
$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$Log = Join-Path $Out "native-nvme-v-$Stamp.log"
$Py = 'C:\Users\Owner\AppData\Local\Programs\Python\Python312\python.exe'
$Repo = 'C:\Users\Owner\dev\xycalc'
$Dir = 'V:\xycalc-work\io-crossover'
$Test = Join-Path $Dir "probe-$Stamp.bin"
New-Item -ItemType Directory -Force -Path $Out,$Dir | Out-Null
function Log([string]$m) {
  $line = "$(Get-Date -Format o) $m"
  [IO.File]::AppendAllText($Log, $line + [Environment]::NewLine)
  Write-Host $line
}
try {
  Log "native Arm B on V: SN770 path=$Test"
  $env:Path = 'C:\Program Files\fio;' + $env:Path
  $fill = @"
from pathlib import Path
p = Path(r'$($Test.Replace('\','\\'))')
p.parent.mkdir(parents=True, exist_ok=True)
chunk = b'\0' * (1024 * 1024)
with p.open('wb') as f:
    for _ in range(512):
        f.write(chunk)
print(p, p.stat().st_size)
"@
  $fillPath = Join-Path $Dir "fill-$Stamp.py"
  [IO.File]::WriteAllText($fillPath, $fill)
  & $Py $fillPath
  Log "fill exit=$LASTEXITCODE size=$((Get-Item $Test).Length)"
  # Use Windows path with backslashes for fio
  $fioSmoke = & fio --name=smoke --filename="$Test" --rw=randread --bs=4k --iodepth=32 --runtime=3 --time_based --ioengine=windowsaio --direct=1 --thread --size=$((Get-Item $Test).Length) --output-format=normal 2>&1
  Log "fio smoke exit=$LASTEXITCODE :: $($fioSmoke | Select-Object -First 5)"
  if ($LASTEXITCODE -ne 0) { throw "fio smoke failed" }
  $txt = Join-Path $Out "arm-local-v-nvme-$Stamp.txt"
  $json = Join-Path $Out "arm-local-v-nvme-$Stamp.json"
  & $Py "$Repo\tools\bench\io_crossover_probe.py" `
    --test-file $Test --device 'V: WD BLACK SN770 NVMe' --arm local-v-nvme `
    --sizes-kib '4,8,16,32,64,128,256,512,1024' `
    --runtime 8 --iodepth 32 *> $txt
  Log "probe exit=$LASTEXITCODE"
  $raw = Get-Content $txt -Raw
  if ($raw -match '(?s)===JSON===\s*(\{.*\})\s*\z') {
    [IO.File]::WriteAllText($json, $Matches[1].Trim())
    Log "wrote $json"
  } else { throw 'no JSON' }
  Log '===DONE==='
  exit 0
} catch {
  Log "FATAL: $_"
  exit 1
}
