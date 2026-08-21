$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
  CommandLine = 'cmd.exe /c C:\Users\Owner\lab\RUN-T11-SHARE.bat'
}
"ReturnValue=$($r.ReturnValue) ProcessId=$($r.ProcessId)" | Out-File C:\Users\Owner\lab\t11-wmi.out
