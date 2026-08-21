$r1=Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\Owner\lab\reef_launch_wave12.ps1'}
"wave12 ReturnValue=$($r1.ReturnValue) ProcessId=$($r1.ProcessId)" | Out-File C:\Users\Owner\lab\wmi-wave12.out
Start-Sleep -Seconds 2
$r2=Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\Owner\lab\reef_run_t9_native_nvme.ps1'}
"native ReturnValue=$($r2.ReturnValue) ProcessId=$($r2.ProcessId) at=$(Get-Date -Format o)" | Add-Content C:\Users\Owner\lab\wmi-wave12.out
