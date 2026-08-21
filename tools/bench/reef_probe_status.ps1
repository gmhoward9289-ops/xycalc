Get-CimInstance Win32_Process -Filter "name='python.exe'" |
  Select-Object ProcessId, CreationDate, CommandLine |
  Format-List | Out-File -FilePath C:\Users\Owner\xycalc-results\python-procs.txt -Encoding utf8
Get-CimInstance Win32_Process -Filter "name='bash.exe'" |
  Select-Object ProcessId, CreationDate, CommandLine |
  Format-List | Out-File -FilePath C:\Users\Owner\xycalc-results\bash-procs.txt -Encoding utf8
Get-Process | Where-Object { $_.ProcessName -match 'cmd|wsl|bash|python|Docker' } |
  Select-Object Id, ProcessName, SessionId, WorkingSet64 |
  Format-Table -AutoSize |
  Out-File -FilePath C:\Users\Owner\xycalc-results\related-procs.txt -Encoding utf8
"done $(Get-Date -Format o)" | Out-File C:\Users\Owner\xycalc-results\probe-status.txt -Encoding utf8
