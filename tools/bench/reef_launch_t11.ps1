$p = Start-Process -FilePath 'powershell.exe' -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','C:\Users\Owner\lab\reef_run_t11_share.ps1' -WindowStyle Hidden -PassThru
"started pid=$($p.Id)" | Out-File C:\Users\Owner\lab\t11-wmi.out
