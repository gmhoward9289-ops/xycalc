$ErrorActionPreference="Continue"
$cfgPath = "$env:USERPROFILE\.docker\config.json"
New-Item -ItemType Directory -Force -Path (Split-Path $cfgPath) | Out-Null
$cfg = @{ auths = @{} }
if (Test-Path $cfgPath) {
  try { $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json } catch {}
}
# Remove Windows credential helper that breaks headless/SSH pulls
if ($cfg.PSObject.Properties.Name -contains 'credsStore') { $cfg.PSObject.Properties.Remove('credsStore') }
if ($cfg.PSObject.Properties.Name -contains 'credStore') { $cfg.PSObject.Properties.Remove('credStore') }
$cfg | ConvertTo-Json -Depth 8 | Set-Content $cfgPath -Encoding UTF8
"wrote $cfgPath" | Out-File C:\Users\Owner\lab\docker-cfg-fix.out
Get-Content $cfgPath | Out-File C:\Users\Owner\lab\docker-cfg-fix.out -Append
docker pull clickhouse/clickhouse-server:23.3 2>&1 | Tee-Object -FilePath C:\Users\Owner\lab\ch-pull-23.log
docker pull clickhouse/clickhouse-server:24.8 2>&1 | Tee-Object -FilePath C:\Users\Owner\lab\ch-pull-24.log
"DONE pulls" | Add-Content C:\Users\Owner\lab\docker-cfg-fix.out