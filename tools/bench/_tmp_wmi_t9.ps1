$ErrorActionPreference = "Continue"
$cmd = "powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\Owner\lab\reef_run_t9_io.ps1"
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd }
"ReturnValue=$($r.ReturnValue) ProcessId=$($r.ProcessId) at=$(Get-Date -Format o)" | Out-File C:\Users\Owner\lab\t9-wmi.out
