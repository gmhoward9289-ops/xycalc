$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
  CommandLine = 'cmd /c C:\Users\Owner\lab\r3_load_go.cmd'
}
Write-Output ("Return=$($r.ReturnValue) Pid=$($r.ProcessId)")
