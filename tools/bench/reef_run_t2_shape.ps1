# T2 compression_shape_probe on reef (Windows docker + Python).
# Work/results on V: — never grow C:.
$ErrorActionPreference = 'Continue'
$Repo = 'C:\Users\Owner\dev\xycalc'
$Py = 'C:\Users\Owner\AppData\Local\Programs\Python\Python312\python.exe'
$Out = 'V:\xycalc-results\compression-shape'
$Work = Join-Path 'V:\xycalc-work' ("compression-shape-" + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$Image = if ($env:PROBE_MONGO_IMAGE) { $env:PROBE_MONGO_IMAGE } else { 'mongo:7' }
$Target = if ($env:PROBE_TARGET_BYTES) { $env:PROBE_TARGET_BYTES } else { '300000000' }
$Name = if ($env:PROBE_NAME) { $env:PROBE_NAME } else { 'xycalc-shape-reef' }
$Shapes = @('pure-random','random-repeated-fields','low-cardinality-enums','realistic-mixed','near-duplicate')
$Comps = @('snappy','zstd','zlib')

New-Item -ItemType Directory -Force -Path $Out,$Work | Out-Null
$Log = Join-Path $Out ("run-" + (Get-Date -Format 'yyyyMMdd-HHmmss') + ".log")
$Detail = Join-Path $Work 'detail.log'
$Json = Join-Path $Out 'shape-sweep.json'

function Log([string]$m) {
  $line = "$(Get-Date -Format o) $m"
  [IO.File]::AppendAllText($Log, $line + [Environment]::NewLine)
  Write-Host $line
}

function Run-Logged([string]$label, [string]$cmdLine) {
  Log $label
  cmd /c "$cmdLine >> `"$Detail`" 2>&1"
  return $LASTEXITCODE
}

Log "WORK=$Work TARGET=$Target IMAGE=$Image CONTAINER=$Name"

try { docker rm -f $Name 2>$null | Out-Null } catch {}

try {
  $env:PROBE_TARGET_BYTES = "$Target"
  $rc = Run-Logged 'generating shapes...' (
    "`"$Py`" `"$Repo\tools\bench\compression_shape_probe.py`" generate --out `"$Work\shapes`""
  )
  if ($rc -ne 0) { throw "generate failed exit=$rc" }
  Log 'generate complete'

  $rc = Run-Logged "starting $Image..." ("docker run -d --name $Name $Image")
  if ($rc -ne 0) { throw "docker run failed exit=$rc" }

  $ready = $false
  for ($i = 0; $i -lt 90; $i++) {
    docker exec $Name mongosh --quiet --eval 'db.runCommand({ping:1}).ok' 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    Start-Sleep -Seconds 2
  }
  if (-not $ready) { throw 'mongod never became ready' }
  Log 'mongod ready'

  $results = Join-Path $Work 'results.jsonl'
  [IO.File]::WriteAllText($results, '')
  $jsDir = Join-Path $Work 'js'
  New-Item -ItemType Directory -Force -Path $jsDir | Out-Null

  foreach ($shape in $Shapes) {
    $jsonl = Join-Path $Work "shapes\$shape.jsonl"
    docker cp $jsonl "${Name}:/tmp/$shape.jsonl" *>> $Detail
    foreach ($comp in $Comps) {
      $db = "cprobe_$($shape.Replace('-','_'))_$comp"
      Log "=== $shape / $comp -> db=$db ==="
      $createJs = Join-Path $jsDir "create_${shape}_${comp}.js"
      @(
        "db = db.getSiblingDB('$db');"
        'db.docs.drop();'
        'db.createCollection(''docs'', {'
        "  storageEngine: { wiredTiger: { configString: 'block_compressor=$comp' } }"
        '});'
      ) -join "`n" | Set-Content -Path $createJs -Encoding ASCII
      docker cp $createJs "${Name}:/tmp/create.js" *>> $Detail
      docker exec $Name mongosh --quiet /tmp/create.js *>> $Detail
      if ($LASTEXITCODE -ne 0) { throw "createCollection failed $db" }
      docker exec $Name mongoimport --quiet --db $db --collection docs --file "/tmp/$shape.jsonl" *>> $Detail
      if ($LASTEXITCODE -ne 0) { throw "mongoimport failed $db" }
    }
  }

  Log 'checkpoint...'
  docker exec $Name mongosh --quiet --eval 'db.adminCommand({fsync:1})' *>> $Detail

  foreach ($shape in $Shapes) {
    foreach ($comp in $Comps) {
      $db = "cprobe_$($shape.Replace('-','_'))_$comp"
      $statsJs = Join-Path $jsDir "stats_${shape}_${comp}.js"
      @(
        "db = db.getSiblingDB('$db');"
        'var st = db.docs.stats();'
        "var cs = (st.wiredTiger && st.wiredTiger.creationString) || '';"
        'print(JSON.stringify({'
        "  shape: '$shape',"
        "  compressor: '$comp',"
        "  db: '$db',"
        '  version: db.version(),'
        '  count: st.count,'
        '  data_size: st.size,'
        '  storage_size: st.storageSize,'
        '  creation_string: cs'
        '}));'
      ) -join "`n" | Set-Content -Path $statsJs -Encoding ASCII
      docker cp $statsJs "${Name}:/tmp/stats.js" *>> $Detail
      $line = docker exec $Name mongosh --quiet /tmp/stats.js
      $trim = (($line | Out-String) -split "`n" | Where-Object { $_.Trim().StartsWith('{') } | Select-Object -Last 1)
      if (-not $trim) { throw "no stats JSON for $db" }
      [IO.File]::AppendAllText($results, $trim.Trim() + [Environment]::NewLine)
    }
  }

  Log 'evaluating...'
  $summaryPath = Join-Path $Work 'summary.txt'
  & $Py "$Repo\tools\bench\compression_shape_probe.py" evaluate $results --shapes-meta "$Work\shapes\shapes.json" *> $summaryPath
  if ($LASTEXITCODE -ne 0) { throw "evaluate failed exit=$LASTEXITCODE" }
  Get-Content $summaryPath | ForEach-Object { Log $_ }
  $summaryText = Get-Content $summaryPath -Raw
  if ($summaryText -match '(?s)===JSON===\r?\n(.+)$') {
    [IO.File]::WriteAllText($Json, $Matches[1].Trim() + [Environment]::NewLine)
  } else {
    throw 'evaluate output missing ===JSON=== marker'
  }
  Log "wrote $Json"
  Log '===DONE==='
  exit 0
}
catch {
  Log "FATAL: $_"
  exit 1
}
finally {
  try { docker rm -f $Name 2>$null | Out-Null } catch {}
}
