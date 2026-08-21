$ErrorActionPreference = 'Continue'
$env:Path = 'C:\Program Files\Docker\Docker\resources\bin;' + $env:Path
Set-Location 'C:\Users\Owner\dev\xycalc\tools\bench\colocation_probe'
$env:MONGO_MEM = '4g'
$env:MONGO_CACHE_GB = '2'
$env:PROBE_DOCS = '100000'
$env:REDIS_MEM = '2g'
$env:CLICKHOUSE_MEM = '4g'
$env:WORKER_MEM = '1g'
Write-Host 'SMOKE start'
docker compose down -v --remove-orphans
Write-Host 'compose up...'
docker compose up -d --build mongo redis clickhouse worker
Write-Host "exit=$LASTEXITCODE"
docker ps
