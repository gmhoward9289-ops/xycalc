$ErrorActionPreference = 'Continue'
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
  CommandLine = 'cmd.exe /c C:\Users\Owner\lab\reef_repair_and_docker.bat'
}
$r | Format-List | Out-File C:\Users\Owner\lab\wmi-launch.out
"ReturnValue=$($r.ReturnValue) ProcessId=$($r.ProcessId)" | Out-File C:\Users\Owner\lab\wmi-launch.out -Append
