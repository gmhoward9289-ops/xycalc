$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
  CommandLine = 'powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\Owner\lab\reef_launch_wave12_r3_win.ps1'
}
Write-Output ("Return=$($r.ReturnValue) Pid=$($r.ProcessId)")
