# Native Arm B v2 — fill file (not sparse), forward-slash path, keep artifact.
$ErrorActionPreference = 'Continue'
$Out = 'V:\xycalc-results\io-crossover'
$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$Log = Join-Path $Out "native-nvme-$Stamp.log"
$Py = 'C:\Users\Owner\AppData\Local\Programs\Python\Python312\python.exe'
$Repo = 'C:\Users\Owner\dev\xycalc'
$Dir = 'Z:\xycalc-work\io-crossover'
$Test = Join-Path $Dir "probe-$Stamp.bin"
$TestSlash = ($Test -replace '\\','/')
New-Item -ItemType Directory -Force -Path $Out,$Dir | Out-Null
function Log([string]$m) {
  $line = "$(Get-Date -Format o) $m"
  [IO.File]::AppendAllText($Log, $line + [Environment]::NewLine)
  Write-Host $line
}
try {
  Log "native Arm B v2 on Z: path=$TestSlash"
  $env:Path = 'C:\Program Files\fio;' + $env:Path
  # Non-sparse: write 512 MiB (enough for fio size=) via Python
  Log 'writing 512 MiB non-sparse'
  & $Py -c "p=r'$Test'; open(p,'wb'); b=b'\0'*1048576; [p.write(b) for _ in range(512)]; open(p.name).close()" 2>&1 | ForEach-Object { Log "py: $_" }
  # Actually fix the python one-liner properly via a tiny script
  $fill = @'
from pathlib import Path
p = Path(r"PLACEHOLDER")
p.parent.mkdir(parents=True, exist_ok=True)
chunk = b"\0" * (1024 * 1024)
with p.open("wb") as f:
    for _ in range(512):
        f.write(chunk)
print(p, p.stat().st_size)
'@
  $fill = $fill.Replace('PLACEHOLDER', $Test.Replace('\','\\'))
  $fillPath = Join-Path $Dir "fill-$Stamp.py"
  [IO.File]::WriteAllText($fillPath, $fill)
  & $Py $fillPath
  Log "fill exit=$LASTEXITCODE size=$((Get-Item $Test).Length)"
  # Smoke one fio call
  $fioOut = & fio --name=smoke --filename=$TestSlash --rw=randread --bs=4k --iodepth=32 --runtime=3 --time_based --ioengine=windowsaio --direct=1 --thread --size=$((Get-Item $Test).Length) --output-format=json 2>&1
  Log "fio smoke: $($fioOut | Select-Object -First 3)"
  if ($LASTEXITCODE -ne 0) { throw "fio smoke failed: $fioOut" }
  $txt = Join-Path $Out "arm-local-z-nvme-$Stamp.txt"
  $json = Join-Path $Out "arm-local-z-nvme-$Stamp.json"
  & $Py "$Repo\tools\bench\io_crossover_probe.py" `
    --test-file $TestSlash --device 'Z: Samsung NVMe' --arm local-z-nvme `
    --sizes-kib '4,8,16,32,64,128,256,512,1024' `
    --runtime 8 --iodepth 32 *> $txt
  Log "probe exit=$LASTEXITCODE"
  $raw = Get-Content $txt -Raw
  if ($raw -match '(?s)===JSON===\s*(\{.*\})\s*\z') {
    [IO.File]::WriteAllText($json, $Matches[1].Trim())
    Log "wrote $json"
  } else {
    throw 'no JSON in probe output'
  }
  Log '===DONE==='
  exit 0
} catch {
  Log "FATAL: $_"
  exit 1
}
