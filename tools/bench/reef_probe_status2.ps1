$out = 'C:\Users\Owner\xycalc-results'
New-Item -ItemType Directory -Force -Path $out | Out-Null

# Session-1 processes (RDP) with command lines
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -match '^(cmd|bash|wsl|python|conhost)\.exe$' } |
  ForEach-Object {
    $sess = (Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue).SessionId
    [pscustomobject]@{
      Pid = $_.ProcessId
      SessionId = $sess
      Name = $_.Name
      Created = $_.CreationDate
      Cmd = $_.CommandLine
    }
  } |
  Where-Object { $_.SessionId -eq 1 -or ($_.Cmd -match 'cache_cliff|phase.?a|xycalc|PROBE_') } |
  Format-List |
  Out-File "$out\session1-cmds.txt" -Encoding utf8

# Any json/log already written
Get-ChildItem $out -Force | Format-Table Name, Length, LastWriteTime -AutoSize |
  Out-File "$out\results-dir.txt" -Encoding utf8

Get-ChildItem 'C:\Users\Owner\Desktop','C:\Users\Owner\dev\xycalc\tools\bench' -Filter '*cache*' -ErrorAction SilentlyContinue |
  Format-Table FullName, Length, LastWriteTime -AutoSize |
  Out-File "$out\cache-files.txt" -Encoding utf8

# Docker from this process (will fail in session 0) — also write a drop for RDP
@'
@echo off
docker ps --format "{{.Names}} {{.Status}} {{.Image}}" > C:\Users\Owner\xycalc-results\docker-ps.txt 2>&1
docker images mongo --format "{{.Repository}}:{{.Tag}} {{.ID}}" > C:\Users\Owner\xycalc-results\docker-images.txt 2>&1
dir C:\Users\Owner\xycalc-results >> C:\Users\Owner\xycalc-results\docker-ps.txt
'@ | Set-Content -Path 'C:\Users\Owner\Desktop\DUMP-DOCKER.bat' -Encoding ASCII

"status $(Get-Date -Format o)" | Out-File "$out\probe-status.txt" -Encoding utf8
