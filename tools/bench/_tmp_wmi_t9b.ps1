$ErrorActionPreference="Continue"
$r=Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\Owner\lab\reef_run_t9_native_nvme.ps1'}
"ReturnValue=$($r.ReturnValue) ProcessId=$($r.ProcessId) at=$(Get-Date -Format o)" | Out-File C:\Users\Owner\lab\t9b-wmi.out
