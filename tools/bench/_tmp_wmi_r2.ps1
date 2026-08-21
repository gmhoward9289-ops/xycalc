$r=Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\Owner\lab\ch_pull.ps1'}
"ch_pull pid=$($r.ProcessId) rv=$($r.ReturnValue)" | Out-File C:\Users\Owner\lab\wmi-r2.out
$r2=Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\Owner\lab\reef_run_t9_native_v.ps1'}
"native_v pid=$($r2.ProcessId) rv=$($r2.ReturnValue)" | Add-Content C:\Users\Owner\lab\wmi-r2.out